"""Executor foreground do Nexus Local Agent para Windows; não instala agendamento."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys
import threading


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.config import load_executor_settings
from modules.local_agent.private_env import load_private_environment
from modules.local_agent.executor import (
    AgentClient,
    HandlerRegistry,
    LocalAgentExecutor,
    register_builtin_handlers,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa o Nexus Local Agent em foreground no Windows."
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Faz um único polling e encerra; útil para homologação controlada.",
    )
    parser.add_argument(
        "--env-file", type=Path,
        help="Arquivo privado fora do repositório, por exemplo em ProgramData.",
    )
    args = parser.parse_args()
    if args.env_file:
        load_private_environment(args.env_file, project_root=PROJECT_ROOT)
    settings = load_executor_settings()
    registry = HandlerRegistry()
    register_builtin_handlers(registry)
    executor = LocalAgentExecutor(AgentClient(settings), registry)
    if args.once:
        executor.run_once()
        return 0

    stop_event = threading.Event()

    def stop(*_args):
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    executor.run_forever(poll_seconds=settings.poll_seconds, stop_event=stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
