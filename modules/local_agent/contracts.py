from __future__ import annotations

import json
import hashlib
import os
import re
from typing import Any

from modules.local_agent.config import TASK_RE


FINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
ALL_STATUSES = frozenset({"QUEUED", "LEASED", *FINAL_STATUSES})


def permission_revision(tasks: frozenset[str]) -> str:
    return hashlib.sha256("\n".join(sorted(tasks)).encode("ascii")).hexdigest()


def checked_task(value: object) -> str:
    if not isinstance(value, str) or not TASK_RE.fullmatch(value):
        raise ValueError("Tarefa inválida.")
    return value


def checked_json_object(value: object, *, maximum_bytes: int, label: str) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"{label} deve ser um objeto JSON.")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} não é serializável.") from exc
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{label} excede o limite permitido.")
    return encoded


def decoded_object(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    return decoded if isinstance(decoded, dict) else {}


def checked_log_message(value: object, *, maximum_bytes: int) -> str:
    if not isinstance(value, str):
        raise ValueError("Log inválido.")
    normalized = value.replace("\x00", "").strip()
    normalized = re.sub(
        r"(?i)(https?://[^\s/:@]+:)[^\s/@]+@", r"\1[REDACTED]@", normalized
    )
    for name, secret in os.environ.items():
        if (
            any(marker in name.upper() for marker in ("PASSWORD", "SENHA", "TOKEN", "SECRET"))
            and len(secret) >= 4
        ):
            normalized = normalized.replace(secret, "[REDACTED]")
    if not normalized or len(normalized.encode("utf-8")) > maximum_bytes:
        raise ValueError("Log vazio ou acima do limite permitido.")
    return normalized
