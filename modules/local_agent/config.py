from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse


AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
TASK_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,99}$")
TRUE_VALUES = {"1", "true", "yes", "sim", "on"}


class LocalAgentConfigurationError(RuntimeError):
    pass


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise LocalAgentConfigurationError(f"{name} inválido.") from exc
    if not minimum <= value <= maximum:
        raise LocalAgentConfigurationError(f"{name} fora do intervalo permitido.")
    return value


def _tasks(value: str) -> frozenset[str]:
    tasks = frozenset(item.strip() for item in value.split(",") if item.strip())
    if not tasks or any(not TASK_RE.fullmatch(item) for item in tasks):
        raise LocalAgentConfigurationError("Allowlist de tarefas inválida.")
    return tasks


def _tokens(value: str) -> dict[str, str]:
    try:
        raw = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise LocalAgentConfigurationError("NEXUS_LOCAL_AGENT_TOKENS_JSON inválido.") from exc
    if not isinstance(raw, dict):
        raise LocalAgentConfigurationError("NEXUS_LOCAL_AGENT_TOKENS_JSON deve ser um objeto.")
    result: dict[str, str] = {}
    for agent_id, token in raw.items():
        if not isinstance(agent_id, str) or not AGENT_ID_RE.fullmatch(agent_id):
            raise LocalAgentConfigurationError("Identificador de agente inválido.")
        if (
            not isinstance(token, str)
            or token != token.strip()
            or len(token) < 32
            or len(token) > 512
        ):
            raise LocalAgentConfigurationError("Token de agente fora do tamanho permitido.")
        result[agent_id] = token
    if len(set(result.values())) != len(result):
        raise LocalAgentConfigurationError("Cada agente deve possuir um token exclusivo.")
    return result


def _agent_tasks(
    value: str,
    *,
    known_agents: set[str],
    allowed_tasks: frozenset[str],
) -> dict[str, frozenset[str]]:
    try:
        raw = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise LocalAgentConfigurationError(
            "NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON inválido."
        ) from exc
    if not isinstance(raw, dict):
        raise LocalAgentConfigurationError("Allowlist por agente deve ser um objeto.")
    result: dict[str, frozenset[str]] = {}
    for agent_id, tasks in raw.items():
        if (
            agent_id not in known_agents
            or not isinstance(tasks, list)
            or not tasks
            or any(not isinstance(task, str) or not TASK_RE.fullmatch(task) for task in tasks)
        ):
            raise LocalAgentConfigurationError("Allowlist por agente inválida.")
        normalized = frozenset(tasks)
        if not normalized <= allowed_tasks:
            raise LocalAgentConfigurationError("Tarefa não autorizada na allowlist do agente.")
        result[agent_id] = normalized
    return result


@dataclass(frozen=True)
class ServerSettings:
    enabled: bool
    database_path: Path
    tokens: dict[str, str]
    allowed_tasks: frozenset[str]
    agent_tasks: dict[str, frozenset[str]]
    lease_seconds: int
    max_payload_bytes: int
    max_result_bytes: int
    max_log_bytes: int


def load_server_settings() -> ServerSettings:
    enabled = _enabled("NEXUS_LOCAL_AGENT_ENABLED", False)
    tokens = _tokens(os.getenv("NEXUS_LOCAL_AGENT_TOKENS_JSON", "{}"))
    allowed_tasks = _tasks(os.getenv("NEXUS_LOCAL_AGENT_ALLOWED_TASKS", "system.ping"))
    agent_tasks = _agent_tasks(
        os.getenv("NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON", "{}"),
        known_agents=set(tokens),
        allowed_tasks=allowed_tasks,
    )
    settings = ServerSettings(
        enabled=enabled,
        database_path=Path(os.getenv(
            "NEXUS_LOCAL_AGENT_DB", "data/nexus_local_agent.sqlite3"
        )),
        tokens=tokens,
        allowed_tasks=allowed_tasks,
        agent_tasks=agent_tasks,
        lease_seconds=_bounded_int("NEXUS_LOCAL_AGENT_LEASE_SECONDS", 120, 30, 3600),
        max_payload_bytes=_bounded_int(
            "NEXUS_LOCAL_AGENT_MAX_PAYLOAD_BYTES", 65536, 256, 1048576
        ),
        max_result_bytes=_bounded_int(
            "NEXUS_LOCAL_AGENT_MAX_RESULT_BYTES", 65536, 256, 1048576
        ),
        max_log_bytes=_bounded_int(
            "NEXUS_LOCAL_AGENT_MAX_LOG_BYTES", 4096, 128, 65536
        ),
    )
    if settings.enabled and not settings.tokens:
        raise LocalAgentConfigurationError(
            "Local Agent habilitado sem tokens de agentes configurados."
        )
    if settings.enabled and set(settings.agent_tasks) != set(settings.tokens):
        raise LocalAgentConfigurationError(
            "Todo agente habilitado deve possuir uma allowlist própria."
        )
    return settings


@dataclass(frozen=True)
class ExecutorSettings:
    server_url: str
    agent_id: str
    token: str
    poll_seconds: int
    request_timeout_seconds: int
    allow_insecure_http: bool


def load_executor_settings() -> ExecutorSettings:
    server_url = os.getenv("NEXUS_LOCAL_AGENT_SERVER_URL", "").strip().rstrip("/")
    agent_id = os.getenv("NEXUS_LOCAL_AGENT_ID", "").strip()
    token = os.getenv("NEXUS_LOCAL_AGENT_TOKEN", "")
    allow_insecure = _enabled("NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP", False)
    parsed = urlparse(server_url)
    if not server_url or parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise LocalAgentConfigurationError("URL do servidor do Local Agent inválida.")
    if parsed.scheme != "https" and not allow_insecure:
        raise LocalAgentConfigurationError("O executor exige HTTPS.")
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise LocalAgentConfigurationError("Identificador do executor inválido.")
    if token != token.strip() or len(token) < 32 or len(token) > 512:
        raise LocalAgentConfigurationError("Token do executor fora do tamanho permitido.")
    return ExecutorSettings(
        server_url=server_url,
        agent_id=agent_id,
        token=token,
        poll_seconds=_bounded_int("NEXUS_LOCAL_AGENT_POLL_SECONDS", 5, 1, 300),
        request_timeout_seconds=_bounded_int(
            "NEXUS_LOCAL_AGENT_REQUEST_TIMEOUT_SECONDS", 15, 2, 120
        ),
        allow_insecure_http=allow_insecure,
    )
