"""Administração local na VPS: emite pareamento temporário e revoga agentes."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.config import load_server_settings
from modules.local_agent.private_env import load_private_environment
from modules.local_agent.repository import LocalAgentRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Administra agentes Nexus sem exibir tokens.")
    parser.add_argument("--env-file", type=Path, default=Path("/etc/nexus/local-agent.env"))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--pair", metavar="DOMINIO/USUARIO")
    action.add_argument("--revoke", metavar="AGENT_ID")
    args = parser.parse_args()
    load_private_environment(args.env_file, project_root=PROJECT_ROOT)
    settings = load_server_settings()
    repository = LocalAgentRepository(
        settings.database_path,
        max_payload_bytes=settings.max_payload_bytes,
        max_result_bytes=settings.max_result_bytes,
        max_log_bytes=settings.max_log_bytes,
    )
    if args.pair:
        if not settings.pairing_enabled:
            parser.error("NEXUS_LOCAL_AGENT_PAIRING_ENABLED está desativado.")
        code = repository.create_pairing(
            identity_key=args.pair,
            tasks=settings.paired_tasks,
            created_by="vps-admin",
            ttl_seconds=settings.pairing_ttl_seconds,
        )
        print(f"Código de uso único (expira em {settings.pairing_ttl_seconds}s): {code}")
        return 0
    if not repository.revoke_agent(args.revoke):
        print("ERRO: agente dinâmico ativo não encontrado.", file=sys.stderr)
        return 1
    print(f"OK: agente {args.revoke} revogado; token rejeitado imediatamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
