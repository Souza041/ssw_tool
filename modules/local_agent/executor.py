from __future__ import annotations

from dataclasses import dataclass
import hashlib
import platform
import threading
import time
from typing import Any, Callable

import requests

from modules.local_agent.config import ExecutorSettings, GCE_MAX_REPORT_BYTES, GCE_QUERY_TASK
from modules.local_agent.contracts import checked_log_message, checked_task, permission_revision
from modules.local_agent.remote_auth import response_proof


Handler = Callable[[dict[str, Any], "ExecutionContext"], dict[str, Any]]


class AgentProtocolError(RuntimeError):
    pass


class AgentClient:
    def __init__(self, settings: ExecutorSettings, session: requests.Session | None = None,
                 *, agent_version: str | None = None):
        self.settings = settings
        self.session = session or requests.Session()
        self.agent_version = agent_version
        self.permission_revision: str | None = None
        self.headers = {
            "Authorization": f"Bearer {settings.token}",
            "X-Nexus-Agent-Id": settings.agent_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                f"{self.settings.server_url}{path}",
                headers=self.headers,
                json=payload,
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AgentProtocolError(f"Falha de comunicação com a VPS: {type(exc).__name__}.") from exc
        if not isinstance(data, dict):
            raise AgentProtocolError("Resposta inválida da VPS.")
        return data

    def claim(self, capabilities: list[str]) -> dict[str, Any]:
        from ssw.browser_transport import browser_session_snapshot

        payload: dict[str, Any] = {
            "capabilities": capabilities,
            "sessions": browser_session_snapshot(),
        }
        if self.agent_version is None:
            return self._post("/api/local-agent/v1/claim", payload)

        payload.update(agent_version=self.agent_version, permission_revision=self.permission_revision)
        response = self._post("/api/local-agent/v1/claim", payload)
        snapshot = response.get("permissions")
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("tasks"), list):
            raise AgentProtocolError("A VPS não oferece sincronização de permissões.")
        try:
            tasks = frozenset(checked_task(task) for task in snapshot["tasks"])
        except ValueError:
            raise AgentProtocolError("Lista de permissões inválida.") from None
        revision = permission_revision(tasks)
        if snapshot.get("revision") != revision:
            raise AgentProtocolError("Revisão de permissões inválida.")
        job = response.get("job")
        if job is not None and (
            self.permission_revision != revision or not isinstance(job, dict)
            or not isinstance(job.get("task"), str) or job["task"] not in tasks
            or job["task"] not in capabilities
        ):
            raise AgentProtocolError("A tarefa não pertence às permissões sincronizadas.")
        self.permission_revision = revision
        return response

    def heartbeat(
        self, job_id: str, lease_token: str, current: int, total: int
    ) -> dict[str, Any]:
        return self._post("/api/local-agent/v1/heartbeat", {
            "job_id": job_id,
            "lease_token": lease_token,
            "progress_current": current,
            "progress_total": total,
        })

    def logs(self, job_id: str, lease_token: str, entries: list[dict[str, str]]) -> None:
        self._post("/api/local-agent/v1/logs", {
            "job_id": job_id, "lease_token": lease_token, "entries": entries,
        })

    def complete(
        self,
        job_id: str,
        lease_token: str,
        *,
        succeeded: bool,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return self._post("/api/local-agent/v1/complete", {
            "job_id": job_id,
            "lease_token": lease_token,
            "succeeded": succeeded,
            "result": result or {},
            "error_message": error_message,
        })

    def remote_auth_claim(self) -> dict[str, Any]:
        return self._post("/api/local-agent/v1/remote-auth/claim", {})

    def upload_gce_report(self, job_id: str, lease_token: str, kind: str, content: bytes) -> None:
        if not 0 < len(content) <= GCE_MAX_REPORT_BYTES:
            raise ValueError("Relatório GCE vazio ou acima do limite.")
        try:
            response = self.session.put(
                f"{self.settings.server_url}/api/local-agent/v1/gce-reports/{job_id}/{kind}",
                headers={
                    **self.headers, "Content-Type": "application/octet-stream",
                    "X-Nexus-Lease": lease_token,
                    "X-Nexus-SHA256": hashlib.sha256(content).hexdigest(),
                },
                data=content, timeout=max(60, self.settings.request_timeout_seconds),
                allow_redirects=False,
            )
            if response.status_code != 204:
                raise AgentProtocolError("A VPS rejeitou a transferência do relatório GCE.")
        except requests.RequestException:
            raise AgentProtocolError("Falha de comunicação durante a transferência GCE.") from None

    def remote_auth_complete(
        self,
        *,
        request_id: str,
        lease_token: str,
        challenge: str,
        identity_fingerprint: str,
        accepted: bool,
    ) -> dict[str, Any]:
        proof = response_proof(
            self.settings.token,
            request_id=request_id,
            challenge=challenge,
            identity=identity_fingerprint,
            agent_id=self.settings.agent_id,
            accepted=accepted,
        )
        return self._post("/api/local-agent/v1/remote-auth/complete", {
            "request_id": request_id,
            "lease_token": lease_token,
            "accepted": accepted,
            "identity_fingerprint": identity_fingerprint,
            "proof": proof,
        })


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, task: str, handler: Handler) -> None:
        task = checked_task(task)
        if task in self._handlers:
            raise ValueError(f"Handler duplicado: {task}.")
        if not callable(handler):
            raise TypeError("Handler precisa ser chamável.")
        self._handlers[task] = handler

    def capabilities(self) -> list[str]:
        return sorted(self._handlers)

    def get(self, task: str) -> Handler:
        try:
            return self._handlers[task]
        except KeyError:
            raise LookupError(f"Handler não registrado: {task}.") from None


@dataclass
class Progress:
    current: int = 0
    total: int = 0


class ExecutionContext:
    def __init__(self, log_callback: Callable[[str, str], None],
                 gce_upload: Callable[[str, bytes], None] | None = None) -> None:
        self.progress = Progress()
        self.cancel_event = threading.Event()
        self._log_callback = log_callback
        self._gce_upload = gce_upload
        self._lock = threading.Lock()

    def log(self, message: str, level: str = "INFO") -> None:
        safe = checked_log_message(message, maximum_bytes=4096)
        normalized_level = level.upper()
        if normalized_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("Nível de log inválido.")
        self._log_callback(normalized_level, safe)

    def set_progress(self, current: int, total: int) -> None:
        if current < 0 or total < 0 or (total and current > total):
            raise ValueError("Progresso inválido.")
        with self._lock:
            self.progress = Progress(current, total)

    def progress_snapshot(self) -> Progress:
        with self._lock:
            return Progress(self.progress.current, self.progress.total)

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise RuntimeError("Execução cancelada pelo servidor.")

    def upload_gce_report(self, kind: str, content: bytes) -> None:
        self.raise_if_cancelled()
        if self._gce_upload is None:
            raise RuntimeError("Transferência GCE não autorizada para esta tarefa.")
        self._gce_upload(kind, content)


class _Heartbeat:
    def __init__(
        self,
        client: AgentClient,
        job_id: str,
        lease_token: str,
        context: ExecutionContext,
        lease_seconds: int,
    ) -> None:
        self.client = client
        self.job_id = job_id
        self.lease_token = lease_token
        self.context = context
        self.interval = max(1, min(10, lease_seconds // 3))
        self.stop_event = threading.Event()
        self.lost_lease = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=self.interval + 1)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            progress = self.context.progress_snapshot()
            try:
                state = self.client.heartbeat(
                    self.job_id, self.lease_token, progress.current, progress.total
                )
                if state.get("cancellation_requested"):
                    self.context.cancel_event.set()
            except AgentProtocolError:
                self.lost_lease.set()
                self.context.cancel_event.set()
                return


class LocalAgentExecutor:
    def __init__(self, client: AgentClient, registry: HandlerRegistry):
        self.client = client
        self.registry = registry

    def run_once(self) -> bool:
        response = self.client.claim(self.registry.capabilities())
        job = response.get("job")
        if job is None:
            return False
        if not isinstance(job, dict):
            raise AgentProtocolError("Job inválido recebido da VPS.")
        job_id = str(job.get("id") or "")
        task = str(job.get("task") or "")
        lease_token = str(job.get("lease_token") or "")
        payload = job.get("payload")
        if not job_id or not lease_token or not isinstance(payload, dict):
            raise AgentProtocolError("Contrato de job incompleto.")

        buffered_logs: list[dict[str, str]] = []

        def log_callback(level: str, message: str) -> None:
            buffered_logs.append({"level": level, "message": message})
            if len(buffered_logs) >= 10:
                self.client.logs(job_id, lease_token, list(buffered_logs))
                buffered_logs.clear()

        def upload(kind: str, content: bytes) -> None:
            self.client.upload_gce_report(job_id, lease_token, kind, content)

        context = ExecutionContext(log_callback, gce_upload=upload if task == GCE_QUERY_TASK else None)
        heartbeat = _Heartbeat(
            self.client,
            job_id,
            lease_token,
            context,
            int(response.get("lease_seconds") or 120),
        )
        heartbeat.start()
        succeeded = False
        result: dict[str, Any] = {}
        error_message: str | None = None
        try:
            context.log(f"Iniciando handler permitido: {task}.")
            handler = self.registry.get(task)
            result = handler(payload, context)
            if not isinstance(result, dict):
                raise TypeError("Handler deve retornar um objeto JSON.")
            context.raise_if_cancelled()
            succeeded = True
            context.log("Handler concluído com sucesso.")
        except BaseException as exc:
            error_message = f"{type(exc).__name__}: falha no handler"
            try:
                context.log(error_message, "ERROR")
            except Exception:
                pass
        finally:
            heartbeat.stop()

        final_progress = context.progress_snapshot()
        try:
            final_state = self.client.heartbeat(
                job_id,
                lease_token,
                final_progress.current,
                final_progress.total,
            )
            if final_state.get("cancellation_requested"):
                context.cancel_event.set()
        except AgentProtocolError:
            # A conclusão abaixo preserva o comportamento fail-closed do lease.
            pass

        if buffered_logs:
            try:
                self.client.logs(job_id, lease_token, buffered_logs)
            except AgentProtocolError:
                pass
        self.client.complete(
            job_id,
            lease_token,
            succeeded=succeeded,
            result=result if succeeded else {},
            error_message=error_message,
        )
        return True

    def run_forever(self, *, poll_seconds: int, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                worked = self.run_once()
            except AgentProtocolError:
                worked = False
            if not worked:
                stop_event.wait(poll_seconds)


def register_builtin_handlers(registry: HandlerRegistry) -> None:
    def system_ping(payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        context.set_progress(0, 1)
        context.log("Teste local do executor iniciado.")
        context.raise_if_cancelled()
        context.set_progress(1, 1)
        return {
            "ok": True,
            "message": str(payload.get("message") or "pong")[:200],
            "platform": platform.system(),
        }

    registry.register("system.ping", system_ping)
