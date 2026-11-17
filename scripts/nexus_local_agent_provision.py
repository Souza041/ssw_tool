"""Gera credenciais Local Agent em arquivos privados, sem imprimi-las."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.provisioning import (
    ProvisioningError,
    atomic_private_write,
    merge_env,
    provision_values,
    read_env,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provisiona HMAC e token individual sem exibir segredos."
    )
    parser.add_argument("--server-env", type=Path, default=Path("/etc/nexus/local-agent.env"))
    parser.add_argument("--windows-env-output", type=Path, required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--agent-id", default="windows-corporativo-01")
    parser.add_argument("--owner", default="automacao")
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    try:
        if os.name == "nt":
            parser.error("Execute este provisionador na VPS Ubuntu.")
        import pwd

        account = pwd.getpwnam(args.owner)
        current_text, current = read_env(args.server_env)
        server, windows = provision_values(
            current, agent_id=args.agent_id, server_url=args.server_url, rotate=args.rotate
        )
        windows_text = "\n".join(f"{key}={value}" for key, value in windows.items()) + "\n"
        atomic_private_write(
            args.server_env,
            merge_env(current_text, server),
            uid=account.pw_uid,
            gid=account.pw_gid,
        )
        atomic_private_write(args.windows_env_output, windows_text)
    except (KeyError, OSError, ProvisioningError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    action = "rotacionadas" if args.rotate else "provisionadas ou preservadas"
    print(f"OK: credenciais {action}; nenhum segredo foi exibido.")
    print(f"Servidor: {args.server_env} (0600, proprietário {args.owner}).")
    print(f"Pacote Windows: {args.windows_env_output} (0600; transferir por canal seguro).")
    print("As flags de ativação permanecem false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
