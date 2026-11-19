"""Provisionamento seguro e idempotente das credenciais do Local Agent."""

from __future__ import annotations

import json
import io
import os
from pathlib import Path
import re
import secrets
import tempfile
from urllib.parse import urlparse

from dotenv import dotenv_values

from .config import AGENT_ID_RE


class ProvisioningError(RuntimeError):
    pass


def _new_secret() -> str:
    return secrets.token_urlsafe(48)


def _is_true(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "sim", "on"}


def provision_values(
    current: dict[str, object], *, agent_id: str, server_url: str, rotate: bool = False
) -> tuple[dict[str, str], dict[str, str]]:
    """Retorna valores servidor/Windows sem expor material secreto."""
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ProvisioningError("Identificador de agente inválido.")
    parsed = urlparse(server_url.rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProvisioningError("A URL pública deve ser HTTPS e não conter credenciais.")
    if _is_true(current.get("NEXUS_LOCAL_AGENT_ENABLED")) or _is_true(
        current.get("NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED")
    ):
        raise ProvisioningError("Desative as duas flags antes de provisionar ou rotacionar.")

    try:
        tokens = json.loads(str(current.get("NEXUS_LOCAL_AGENT_TOKENS_JSON") or "{}"))
    except json.JSONDecodeError as exc:
        raise ProvisioningError("JSON de tokens existente é inválido.") from exc
    if not isinstance(tokens, dict):
        raise ProvisioningError("JSON de tokens existente deve ser um objeto.")

    previous_token = tokens.get(agent_id)
    token = _new_secret() if rotate or not isinstance(previous_token, str) else previous_token
    if len(token) < 32 or token != token.strip():
        raise ProvisioningError("Token existente é inválido; use --rotate para substituí-lo.")
    secret_value = current.get("NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET")
    bootstrap_secret = (
        _new_secret()
        if rotate or not isinstance(secret_value, str) or not secret_value
        else secret_value
    )
    if len(bootstrap_secret) < 32 or bootstrap_secret != bootstrap_secret.strip():
        raise ProvisioningError("Segredo HMAC existente é inválido; use --rotate.")
    if token == bootstrap_secret:
        raise ProvisioningError("Token do agente e segredo HMAC devem ser diferentes.")
    tokens[agent_id] = token

    server = {
        "NEXUS_LOCAL_AGENT_ENABLED": "false",
        "NEXUS_SSW_DELEGATION_ENABLED": "false",
        "NEXUS_LOCAL_AGENT_METRICS_HOMOLOGATION_ENABLED": "false",
        "NEXUS_LOCAL_AGENT_DB": "/var/lib/nexus/local-agent.sqlite3",
        "NEXUS_LOCAL_AGENT_TOKENS_JSON": json.dumps(tokens, separators=(",", ":")),
        "NEXUS_LOCAL_AGENT_ALLOWED_TASKS": "system.ping",
        "NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON": json.dumps(
            {key: ["system.ping"] for key in tokens}, separators=(",", ":")
        ),
        "NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED": "false",
        "NEXUS_LOCAL_AGENT_BOOTSTRAP_KEY_ID": str(
            current.get("NEXUS_LOCAL_AGENT_BOOTSTRAP_KEY_ID") or "homologacao-admin"
        ),
        "NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET": bootstrap_secret,
        "NEXUS_LOCAL_AGENT_BOOTSTRAP_URL": server_url.rstrip("/"),
    }
    windows = {
        "NEXUS_LOCAL_AGENT_SERVER_URL": server_url.rstrip("/"),
        "NEXUS_LOCAL_AGENT_ID": agent_id,
        "NEXUS_LOCAL_AGENT_TOKEN": token,
        "NEXUS_LOCAL_AGENT_POLL_SECONDS": "5",
        "NEXUS_LOCAL_AGENT_REQUEST_TIMEOUT_SECONDS": "15",
        "NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP": "false",
        "NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED": "false",
        "NEXUS_LOCAL_AGENT_SSW_TASKS": "",
        "NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE": r"C:\ProgramData\Nexus\ssw-automation.env",
    }
    return server, windows


def merge_env(text: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    lines: list[str] = []
    pattern = re.compile(r"^(NEXUS_LOCAL_AGENT_[A-Z0-9_]+)=.*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines.append(f"{key}={remaining.pop(key)}")
        else:
            lines.append(line)
    if lines and lines[-1]:
        lines.append("")
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(lines).rstrip() + "\n"


def atomic_private_write(path: Path, content: str, *, uid: int | None = None, gid: int | None = None) -> None:
    path = Path(path)
    if path.is_symlink():
        raise ProvisioningError(f"Destino não pode ser link simbólico: {path}")
    if not path.parent.is_dir():
        raise ProvisioningError(f"Diretório de destino não existe: {path.parent}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if uid is not None and gid is not None and hasattr(os, "chown"):
            os.chown(temporary, uid, gid)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except (FileNotFoundError, PermissionError):
            pass
        raise


def read_env(path: Path) -> tuple[str, dict[str, object]]:
    if path.is_symlink() or not path.is_file():
        raise ProvisioningError(f"Arquivo privado não encontrado ou inseguro: {path}")
    text = path.read_text(encoding="utf-8")
    values = dict(dotenv_values(stream=io.StringIO(text)))
    return text, values
