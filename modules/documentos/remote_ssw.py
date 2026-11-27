"""Coordena a coleta individual e entrega ao pipeline somente relatórios validados."""

from functools import partial
from datetime import datetime
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time

from modules.documentos.collectors.ssw import SSWDownloads
from modules.documentos.config import settings as documents_settings
from modules.local_agent.config import GCE_MAX_REPORT_BYTES, GCE_QUERY_TASK, load_server_settings
from modules.local_agent.delegation import enqueue_ssw_task
from modules.local_agent.repository import LocalAgentRepository, utc_now


def queue_repository() -> LocalAgentRepository:
    settings = load_server_settings()
    return LocalAgentRepository(
        settings.database_path, max_payload_bytes=settings.max_payload_bytes,
        max_result_bytes=settings.max_result_bytes, max_log_bytes=settings.max_log_bytes,
    )


def remote_downloader(*, client, root: Path, execution_id: str, progress):
    return partial(
        _download, client=client, root=root, execution_id=execution_id, progress=progress,
    )


def _download(_pipeline_dir, period_start, period_end, *, client, root, execution_id, progress):
    repo = queue_repository()
    timeout = documents_settings.ssw_report_timeout
    job_id = enqueue_ssw_task(
        GCE_QUERY_TASK,
        {
            **client.identity_payload(),
            "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "cnpj": documents_settings.op930_cnpj, "group": documents_settings.op930_group,
            "timeout": timeout,
        },
        target_agent_id=client.agent_id,
        concurrency_key=f"ssw.session:{client.session_binding}",
        idempotency_key=f"gce.collect:{execution_id}",
        priority=50,
    )
    progress("Consultas OP455 e OP930 enviadas ao agente vinculado.")
    deadline = time.monotonic() + 2 * timeout + 300
    completed = False
    try:
        seen = 0
        while time.monotonic() < deadline:
            job = repo.get_job(job_id)
            if job is None:
                raise RuntimeError("Consulta SSW GCE não encontrada.")
            for entry in job["logs"][seen:]:
                progress(entry["message"])
            seen = len(job["logs"])
            if job["status"] == "SUCCEEDED":
                reports = repo.take_gce_reports(job_id=job_id, agent_id=client.agent_id)
                files = {}
                for kind, (digest, content) in reports.items():
                    if not 0 < len(content) <= GCE_MAX_REPORT_BYTES or not secrets.compare_digest(
                        hashlib.sha256(content).hexdigest(), digest
                    ):
                        raise RuntimeError("Integridade dos relatórios OP455/OP930 inválida.")
                    files[kind] = root / f"{kind}.sswweb"
                    files[kind].write_bytes(content)
                completed = True
                return SSWDownloads(op455=files["op455"], op930=files["op930"])
            if job["status"] in {"FAILED", "CANCELLED"}:
                if str(job.get("error_message") or "").startswith("SSWBrowserAuthenticationError:"):
                    raise RuntimeError("Sessão SSW expirada. Faça login novamente no Nexus.")
                raise RuntimeError("A coleta OP455/OP930 falhou ou foi cancelada no agente.")
            if job["status"] == "LEASED" and datetime.fromisoformat(job["lease_expires_at"]) < utc_now():
                raise RuntimeError("O agente perdeu a conexão durante a coleta OP455/OP930.")
            time.sleep(1)
        raise RuntimeError("Prazo da coleta OP455/OP930 excedido. Verifique o agente.")
    finally:
        if not completed:
            try:
                repo.request_cancel(job_id)
            except (LookupError, sqlite3.Error):
                # A fila expira a tarefa ou os relatórios mesmo se o cancelamento falhar.
                pass
