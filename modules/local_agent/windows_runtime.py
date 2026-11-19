"""Runtime seguro para manter o Nexus Local Agent como tarefa Windows."""

from __future__ import annotations

from logging.handlers import RotatingFileHandler
import logging
import os
from pathlib import Path
import re
from typing import IO


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|password|passwd|senha|token|secret|cookie)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}=*")
_CPF = re.compile(r"(?<!\d)(?:\d{3}[.\s-]?){3}\d{2}(?!\d)")
_URL_CREDENTIAL = re.compile(r"(?i)(https?://[^\s/:@]+:)[^\s/@]+@")


def sanitize_operational_log(value: object) -> str:
    text = str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
    text = _URL_CREDENTIAL.sub(r"\1[REDACTED]@", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)
    text = _CPF.sub("[REDACTED_CPF]", text)
    for name, secret in os.environ.items():
        if (
            any(marker in name.upper() for marker in ("PASSWORD", "SENHA", "TOKEN", "SECRET", "COOKIE"))
            and len(secret) >= 4
        ):
            text = text.replace(secret, "[REDACTED]")
    return text[:4000] or "mensagem vazia"


class SafeLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = sanitize_operational_log(record.getMessage())
        except Exception:
            record.msg = "Falha ao sanitizar mensagem operacional."
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_runtime_logging(
    log_file: Path | None,
    *,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    logger = logging.getLogger("nexus.local_agent.windows")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    if log_file is None:
        handler: logging.Handler = logging.StreamHandler()
    else:
        resolved = Path(log_file).expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            resolved,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    handler.addFilter(SafeLogFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    ))
    logger.addHandler(handler)
    return logger


class SingleInstanceLock:
    """Lock não bloqueante liberado automaticamente quando o processo termina."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._stream: IO[bytes] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Outra instância do Nexus Local Agent já está ativa.")
        return self

    def __exit__(self, *_args):
        self.release()

