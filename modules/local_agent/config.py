from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse


AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
GCE_PIPELINE_TASK = "ssw.documentos_gce"
GCE_QUERY_TASK = "ssw.documentos_gce.collect_ssw"
GCE_REPORTS = ("op455", "op930")
GCE_MAX_REPORT_BYTES = 128 * 1024 * 1024
INDIVIDUAL_TASKS = frozenset({
    "system.ping", GCE_QUERY_TASK, "ssw.op001.nfd", "ssw.op001.transporte", "ssw.session.close",
})
INDIVIDUAL_AGENT_VERSION = "0.2.1"
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


def _remote_auth_bindings(value: str, *, known_agents: set[str]) -> dict[str, str]:
    """Mapa fechado DOMINIO/USUARIO -> agente; nunca recebe escolha do frontend."""
    try:
        raw = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise LocalAgentConfigurationError(
            "NEXUS_SSW_REMOTE_AUTH_BINDINGS_JSON inválido."
        ) from exc
    if not isinstance(raw, dict):
        raise LocalAgentConfigurationError(
            "Vínculos de autenticação remota devem ser um objeto."
        )
    result: dict[str, str] = {}
    for identity, agent_id in raw.items():
        if not isinstance(identity, str) or identity != identity.strip():
            raise LocalAgentConfigurationError("Identidade remota inválida.")
        parts = identity.split("/", 1)
        if (
            len(parts) != 2
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", parts[0])
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", parts[1])
            or not isinstance(agent_id, str)
            or agent_id not in known_agents
        ):
            raise LocalAgentConfigurationError("Vínculo de autenticação remota inválido.")
        key = f"{parts[0].strip().casefold()}/{parts[1].strip().casefold()}"
        if key in result:
            raise LocalAgentConfigurationError("Vínculo de autenticação remota duplicado.")
        result[key] = agent_id
    return result


@dataclass(frozen=True)
class ServerSettings:
    enabled: bool
    database_path: Path
    tokens: dict[str, str] = field(repr=False)
    allowed_tasks: frozenset[str]
    agent_tasks: dict[str, frozenset[str]]
    lease_seconds: int
    max_payload_bytes: int
    max_result_bytes: int
    max_log_bytes: int
    bootstrap_enabled: bool
    bootstrap_key_id: str
    bootstrap_secret: str = field(repr=False)
    bootstrap_max_skew_seconds: int
    bootstrap_per_minute: int
    bootstrap_per_hour: int
    remote_auth_enabled: bool
    remote_auth_agent_id: str
    remote_auth_bindings: dict[str, str]
    agent_offline_seconds: int
    pairing_enabled: bool
    pairing_ttl_seconds: int
    paired_tasks: frozenset[str]
    remote_auth_timeout_seconds: int
    remote_auth_request_ttl_seconds: int
    remote_auth_per_minute: int
    remote_auth_max_pending: int


def load_server_settings() -> ServerSettings:
    enabled = _enabled("NEXUS_LOCAL_AGENT_ENABLED", False)
    tokens = _tokens(os.getenv("NEXUS_LOCAL_AGENT_TOKENS_JSON", "{}"))
    allowed_tasks = _tasks(os.getenv("NEXUS_LOCAL_AGENT_ALLOWED_TASKS", "system.ping"))
    agent_tasks = _agent_tasks(
        os.getenv("NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON", "{}"),
        known_agents=set(tokens),
        allowed_tasks=allowed_tasks,
    )
    bootstrap_enabled = _enabled("NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED", False)
    bootstrap_key_id = os.getenv(
        "NEXUS_LOCAL_AGENT_BOOTSTRAP_KEY_ID", ""
    ).strip()
    bootstrap_secret = os.getenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET", "")
    remote_auth_enabled = _enabled("NEXUS_SSW_REMOTE_AUTH_ENABLED", False)
    remote_auth_agent_id = os.getenv(
        "NEXUS_SSW_REMOTE_AUTH_AGENT_ID", ""
    ).strip()
    remote_auth_bindings = _remote_auth_bindings(
        os.getenv("NEXUS_SSW_REMOTE_AUTH_BINDINGS_JSON", "{}"),
        known_agents=set(tokens),
    )
    settings = ServerSettings(
        enabled=enabled,
        database_path=Path(os.getenv(
            "NEXUS_LOCAL_AGENT_DB", "/var/lib/nexus/local-agent.sqlite3"
        )),
        tokens=tokens,
        allowed_tasks=allowed_tasks,
        agent_tasks=agent_tasks,
        lease_seconds=_bounded_int("NEXUS_LOCAL_AGENT_LEASE_SECONDS", 120, 30, 3600),
        max_payload_bytes=_bounded_int(
            "NEXUS_LOCAL_AGENT_MAX_PAYLOAD_BYTES", 65536, 256, 16777216
        ),
        max_result_bytes=_bounded_int(
            "NEXUS_LOCAL_AGENT_MAX_RESULT_BYTES", 65536, 256, 16777216
        ),
        max_log_bytes=_bounded_int(
            "NEXUS_LOCAL_AGENT_MAX_LOG_BYTES", 4096, 128, 65536
        ),
        bootstrap_enabled=bootstrap_enabled,
        bootstrap_key_id=bootstrap_key_id,
        bootstrap_secret=bootstrap_secret,
        bootstrap_max_skew_seconds=_bounded_int(
            "NEXUS_LOCAL_AGENT_BOOTSTRAP_MAX_SKEW_SECONDS", 60, 15, 300
        ),
        bootstrap_per_minute=_bounded_int(
            "NEXUS_LOCAL_AGENT_BOOTSTRAP_PER_MINUTE", 5, 1, 30
        ),
        bootstrap_per_hour=_bounded_int(
            "NEXUS_LOCAL_AGENT_BOOTSTRAP_PER_HOUR", 20, 1, 200
        ),
        remote_auth_enabled=remote_auth_enabled,
        remote_auth_agent_id=remote_auth_agent_id,
        remote_auth_bindings=remote_auth_bindings,
        agent_offline_seconds=_bounded_int(
            "NEXUS_LOCAL_AGENT_OFFLINE_SECONDS", 30, 10, 3600
        ),
        pairing_enabled=_enabled("NEXUS_LOCAL_AGENT_PAIRING_ENABLED", False),
        pairing_ttl_seconds=_bounded_int(
            "NEXUS_LOCAL_AGENT_PAIRING_TTL_SECONDS", 600, 60, 3600
        ),
        paired_tasks=_tasks(os.getenv(
            "NEXUS_LOCAL_AGENT_PAIRED_TASKS",
            "system.ping,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close",
        )) - {GCE_PIPELINE_TASK},
        remote_auth_timeout_seconds=_bounded_int(
            "NEXUS_SSW_REMOTE_AUTH_TIMEOUT_SECONDS", 960, 30, 1200
        ),
        remote_auth_request_ttl_seconds=_bounded_int(
            "NEXUS_SSW_REMOTE_AUTH_REQUEST_TTL_SECONDS", 960, 30, 1200
        ),
        remote_auth_per_minute=_bounded_int(
            "NEXUS_SSW_REMOTE_AUTH_PER_MINUTE", 5, 1, 30
        ),
        remote_auth_max_pending=_bounded_int(
            "NEXUS_SSW_REMOTE_AUTH_MAX_PENDING", 50, 1, 500
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
    if settings.bootstrap_enabled:
        if not settings.enabled:
            raise LocalAgentConfigurationError(
                "Bootstrap não pode ser habilitado com o Local Agent desativado."
            )
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", settings.bootstrap_key_id):
            raise LocalAgentConfigurationError("Identificador do bootstrap inválido.")
        if (
            settings.bootstrap_secret != settings.bootstrap_secret.strip()
            or not 32 <= len(settings.bootstrap_secret) <= 512
        ):
            raise LocalAgentConfigurationError("Segredo do bootstrap inválido.")
        if settings.bootstrap_secret in settings.tokens.values():
            raise LocalAgentConfigurationError(
                "O segredo do bootstrap deve ser independente dos tokens de agentes."
            )
        if "system.ping" not in settings.allowed_tasks:
            raise LocalAgentConfigurationError(
                "O bootstrap exige system.ping na allowlist global."
            )
    if settings.pairing_enabled and not settings.paired_tasks <= settings.allowed_tasks:
        raise LocalAgentConfigurationError(
            "Tarefa de agente pareado fora da allowlist global."
        )
    if settings.pairing_enabled and not settings.enabled:
        raise LocalAgentConfigurationError(
            "Pareamento não pode ser habilitado com o Local Agent desativado."
        )
    if settings.remote_auth_enabled:
        if not settings.enabled:
            raise LocalAgentConfigurationError(
                "Autenticação remota exige o Local Agent habilitado."
            )
        legacy_agent_valid = (
            bool(settings.remote_auth_agent_id)
            and AGENT_ID_RE.fullmatch(settings.remote_auth_agent_id) is not None
            and settings.remote_auth_agent_id in settings.tokens
        )
        if not settings.remote_auth_bindings and not legacy_agent_valid:
            raise LocalAgentConfigurationError(
                "Autenticação remota exige vínculos usuário/agente ou agente legado válido."
            )
    return settings


def remote_auth_agent_for(settings: ServerSettings, *, dominio: str, usuario: str) -> str:
    """Resolve exclusivamente no backend o agente provisionado para a identidade."""
    key = f"{str(dominio).strip().casefold()}/{str(usuario).strip().casefold()}"
    if settings.remote_auth_bindings:
        agent_id = settings.remote_auth_bindings.get(key, "")
        if not agent_id:
            raise LookupError("Usuário sem computador Nexus autorizado.")
        return agent_id
    if settings.pairing_enabled:
        raise LookupError("Usuário sem computador Nexus autorizado.")
    if settings.remote_auth_agent_id:
        return settings.remote_auth_agent_id
    raise LookupError("Usuário sem computador Nexus autorizado.")


def individual_task_grants(tasks: frozenset[str]) -> frozenset[str]:
    """Converte somente o grant GCE legado. O backend ainda aplica a allowlist atual."""
    if GCE_PIPELINE_TASK in tasks:
        return (tasks - {GCE_PIPELINE_TASK}) | {GCE_QUERY_TASK}
    return tasks


@dataclass(frozen=True)
class ExecutorSettings:
    server_url: str
    agent_id: str
    token: str = field(repr=False)
    poll_seconds: int
    request_timeout_seconds: int
    allow_insecure_http: bool
    remote_auth_enabled: bool


def load_executor_settings() -> ExecutorSettings:
    server_url = os.getenv("NEXUS_LOCAL_AGENT_SERVER_URL", "").strip().rstrip("/")
    agent_id = os.getenv("NEXUS_LOCAL_AGENT_ID", "").strip()
    token = os.getenv("NEXUS_LOCAL_AGENT_TOKEN", "")
    allow_insecure = _enabled("NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP", False)
    parsed = urlparse(server_url)
    if (
        not server_url
        or parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
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
        remote_auth_enabled=_enabled("NEXUS_SSW_REMOTE_AUTH_ENABLED", False),
    )
