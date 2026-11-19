"""Enfileira/consulta uma homologação OP455 sem ativar a delegação global."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.config import load_server_settings
from modules.local_agent.delegation import delegation_enabled
from modules.local_agent.private_env import load_private_environment
from modules.local_agent.repository import LocalAgentRepository
from modules.local_agent.ssw_tasks import TASK_METRICAS, validate_ssw_payload


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {
        "1", "true", "yes", "sim", "on",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Homologação isolada OP455; não habilita schedulers."
    )
    parser.add_argument(
        "--env-file", type=Path, default=Path("/etc/nexus/local-agent.env")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("--days", type=int, default=1)
    enqueue.add_argument("--correlation-id", required=True)
    status = commands.add_parser("status")
    status.add_argument("job_id")
    args = parser.parse_args()

    load_private_environment(args.env_file, project_root=PROJECT_ROOT)
    if not _enabled("NEXUS_LOCAL_AGENT_METRICS_HOMOLOGATION_ENABLED"):
        parser.error("Gate isolado de homologação de Métricas está desativado.")
    if delegation_enabled():
        parser.error("Homologação isolada exige delegação global desativada.")

    settings = load_server_settings()
    if not settings.enabled or TASK_METRICAS not in settings.allowed_tasks:
        parser.error("Local Agent/Métricas não está autorizado na VPS.")
    if not any(TASK_METRICAS in tasks for tasks in settings.agent_tasks.values()):
        parser.error("Nenhum agente está autorizado para Métricas.")
    repository = LocalAgentRepository(
        settings.database_path,
        max_payload_bytes=settings.max_payload_bytes,
        max_result_bytes=settings.max_result_bytes,
        max_log_bytes=settings.max_log_bytes,
    )

    if args.command == "enqueue":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,63}", args.correlation_id):
            parser.error("Correlation ID inválido; use 8–64 caracteres seguros.")
        payload = validate_ssw_payload(TASK_METRICAS, {
            "days": args.days,
            "origin": "homologation",
            "mode": "validate_only",
            "correlation_id": args.correlation_id,
        })
        job_id = repository.enqueue(
            task=TASK_METRICAS,
            payload=payload,
            priority=10,
            max_attempts=1,
            idempotency_key=f"metrics-hml:{args.correlation_id}",
            concurrency_key="ssw.metricas_op455",
            coalesce_active=True,
        )
        print(f"Homologação enfileirada: job_id={job_id}")
        print("Delegação global permaneceu desativada; nenhum segredo foi exibido.")
        return 0

    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        args.job_id,
    ):
        parser.error("Job ID inválido.")
    job = repository.get_job(args.job_id)
    if (
        job is None
        or job.get("task") != TASK_METRICAS
        or (job.get("payload") or {}).get("mode") != "validate_only"
    ):
        parser.error("Job de homologação OP455 não encontrado.")
    print(
        "status={status} progress={current}/{total} attempts={attempts}".format(
            status=job["status"], current=job["progress_current"],
            total=job["progress_total"], attempts=job["attempts"],
        )
    )
    if job.get("result"):
        result = job["result"]
        print(
            "resultado: mode={mode} total={total} bytes={bytes} sha256={sha256}".format(
                mode=result.get("mode"), total=result.get("total"),
                bytes=result.get("bytes"), sha256=result.get("sha256"),
            )
        )
    for entry in job.get("logs", []):
        print(f"{entry['created_at']} {entry['level']} {entry['message']}")
    if job.get("error_message"):
        print(f"erro={job['error_message']}")
    return 0 if job["status"] != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

