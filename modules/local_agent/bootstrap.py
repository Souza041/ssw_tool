"""Assinatura por requisição do bootstrap restrito a system.ping."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time


NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{24,128}$")
SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_ping_body(message: str) -> bytes:
    return json.dumps(
        {"message": message}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def signature_base(
    *, method: str, path: str, timestamp: str, nonce: str, body: bytes
) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join((method.upper(), path, timestamp, nonce, body_hash)).encode("utf-8")


def sign_request(
    secret: str,
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        signature_base(
            method=method, path=path, timestamp=timestamp, nonce=nonce, body=body
        ),
        hashlib.sha256,
    ).hexdigest()


def validate_freshness(timestamp: str, *, max_skew_seconds: int, now: int | None = None) -> None:
    if not re.fullmatch(r"\d{10}", timestamp):
        raise ValueError("Timestamp do bootstrap inválido.")
    current = int(time.time()) if now is None else int(now)
    if abs(current - int(timestamp)) > max_skew_seconds:
        raise ValueError("Timestamp do bootstrap fora da janela permitida.")


def validate_nonce(nonce: str) -> None:
    if not NONCE_RE.fullmatch(nonce):
        raise ValueError("Nonce do bootstrap inválido.")


def verify_signature(expected: str, supplied: str) -> bool:
    return bool(SIGNATURE_RE.fullmatch(supplied)) and hmac.compare_digest(
        expected, supplied
    )
