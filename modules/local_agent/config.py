from __future__ import annotations

from dataclasses import dataclass, field
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
            "NEXUS_LOCAL_AGENT_MAX_PAYLOAD_BYTES", 65536, 256, 1048576
        ),
        max_result_bytes=_bounded_int(
            "NEXUS_LOCAL_AGENT_MAX_RESULT_BYTES", 65536, 256, 1048576
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
        remote_auth_timeout_seconds=_bounded_int(
            "NEXUS_SSW_REMOTE_AUTH_TIMEOUT_SECONDS", 20, 2, 60
        ),
        remote_auth_request_ttl_seconds=_bounded_int(
            "NEXUS_SSW_REMOTE_AUTH_REQUEST_TTL_SECONDS", 30, 5, 120
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
    if settings.remote_auth_enabled:
        if not settings.enabled:
            raise LocalAgentConfigurationError(
                "Autenticação remota exige o Local Agent habilitado."
            )
        if (
            not AGENT_ID_RE.fullmatch(settings.remote_auth_agent_id)
            or settings.remote_auth_agent_id not in settings.tokens
        ):
            raise LocalAgentConfigurationError(
                "Agente autorizado para autenticação remota inválido."
            )
    return settings


@dataclass(frozen=True)
class ExecutorSettings:
    server_url: str
    agent_id: str
    token: str = field(repr=False)
    poll_seconds: int
    request_timeout_seconds: int
    allow_insecure_http: bool
    remote_auth_enabled: bool
    https_proxy: str | None = field(default=None, repr=False)
    ca_bundle: Path | None = None


def load_executor_settings() -> ExecutorSettings:
    server_url = os.getenv("NEXUS_LOCAL_AGENT_SERVER_URL", "").strip().rstrip("/")
    agent_id = os.getenv("NEXUS_LOCAL_AGENT_ID", "").strip()
    token = os.getenv("NEXUS_LOCAL_AGENT_TOKEN", "")
    allow_insecure = _enabled("NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP", False)
    proxy_value = os.getenv("NEXUS_LOCAL_AGENT_HTTPS_PROXY", "").strip()
    proxy = urlparse(proxy_value) if proxy_value else None
    if proxy is not None and (
        proxy.scheme not in {"http", "https"}
        or not proxy.netloc
        or proxy.query
        or proxy.fragment
    ):
        raise LocalAgentConfigurationError("Proxy HTTPS do executor inválido.")
    ca_value = os.getenv("NEXUS_LOCAL_AGENT_CA_BUNDLE", "").strip()
    ca_bundle = Path(ca_value).expanduser().resolve() if ca_value else None
    if ca_bundle is not None and not ca_bundle.is_file():
        raise LocalAgentConfigurationError("CA bundle do executor não encontrado.")
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
        https_proxy=proxy_value or None,
        ca_bundle=ca_bundle,
    )
