"""Cliente de homologação do bootstrap HMAC; nunca imprime o segredo."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.bootstrap import canonical_ping_body, sign_request
from modules.local_agent.private_env import load_private_environment


def _headers(secret: str, key_id: str, path: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(32)
    return {
        "X-Nexus-Bootstrap-Key-Id": key_id,
        "X-Nexus-Bootstrap-Timestamp": timestamp,
        "X-Nexus-Bootstrap-Nonce": nonce,
        "X-Nexus-Bootstrap-Signature": sign_request(
            secret,
            method="POST",
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Homologa system.ping via bootstrap.")
    parser.add_argument("--env-file", type=Path, required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    enqueue = subcommands.add_parser("enqueue")
    enqueue.add_argument("--message", default="homologacao")
    status = subcommands.add_parser("status")
    status.add_argument("job_id")
    args = parser.parse_args()
    load_private_environment(args.env_file, project_root=PROJECT_ROOT)
    base_url = os.getenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_URL", "").rstrip("/")
    key_id = os.getenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_KEY_ID", "")
    secret = os.getenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET", "")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not key_id
        or len(secret) < 32
    ):
        parser.error("Configuração privada do bootstrap ausente ou insegura.")

    if args.command == "enqueue":
        if not 1 <= len(args.message) <= 200:
            parser.error("Mensagem deve possuir entre 1 e 200 caracteres.")
        path = "/api/local-agent/v1/bootstrap/system-ping"
        body = canonical_ping_body(args.message)
        try:
            response = requests.post(
                base_url + path,
                data=body,
                headers=_headers(secret, key_id, path, body),
                timeout=15,
            )
        except requests.RequestException as exc:
            print(f"Falha de comunicação: {type(exc).__name__}.")
            return 1
    else:
        path = f"/api/local-agent/v1/bootstrap/system-ping/{args.job_id}/status"
        try:
            response = requests.post(
                base_url + path,
                data=b"",
                headers=_headers(secret, key_id, path, b""),
                timeout=15,
            )
        except requests.RequestException as exc:
            print(f"Falha de comunicação: {type(exc).__name__}.")
            return 1
    try:
        result = response.json()
    except ValueError:
        result = {"detail": "Resposta não JSON."}
    if not response.ok:
        print(f"Falha HTTP {response.status_code}: {result.get('detail', 'erro')}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
