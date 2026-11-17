"""Bootstrap explícito do FastAPI com ambiente privado externo ao checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.private_env import load_private_environment


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia o Nexus FastAPI sob Supervisor.")
    parser.add_argument(
        "--env-file", type=Path, default=Path("/etc/nexus/local-agent.env")
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.workers <= 8:
        parser.error("Porta ou quantidade de workers inválida.")
    load_private_environment(args.env_file, project_root=PROJECT_ROOT)
    import uvicorn

    uvicorn.run(
        "web.main:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        proxy_headers=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
