"""Executor foreground do Nexus Local Agent para Windows; não instala agendamento."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import threading


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.config import load_executor_settings
from modules.local_agent.private_env import SSW_RUNTIME_PREFIXES, load_private_environment
from modules.local_agent.executor import (
    AgentProtocolError,
    AgentClient,
    HandlerRegistry,
    LocalAgentExecutor,
    register_builtin_handlers,
)
from modules.local_agent.remote_auth import RemoteAuthError, WindowsRemoteAuthExecutor
from modules.local_agent.ssw_tasks import register_ssw_handlers


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
    if os.getenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "sim", "on",
    }:
        runtime_value = os.getenv("NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE", "").strip()
        if not runtime_value:
            parser.error("Handlers SSW exigem NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE.")
        runtime_env = Path(runtime_value)
        load_private_environment(
            runtime_env,
            project_root=PROJECT_ROOT,
            allowed_prefixes=SSW_RUNTIME_PREFIXES,
        )
    settings = load_executor_settings()
    registry = HandlerRegistry()
    register_builtin_handlers(registry)
    register_ssw_handlers(registry)
    client = AgentClient(settings)
    executor = LocalAgentExecutor(client, registry)
    remote_auth_executor = (
        WindowsRemoteAuthExecutor(client) if settings.remote_auth_enabled else None
    )

    def run_once() -> bool:
        if remote_auth_executor is not None and remote_auth_executor.run_once():
            return True
        return executor.run_once()

    if args.once:
        run_once()
        return 0

    stop_event = threading.Event()

    def stop(*_args):
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    if remote_auth_executor is None:
        executor.run_forever(poll_seconds=settings.poll_seconds, stop_event=stop_event)
        return 0
    while not stop_event.is_set():
        try:
            worked = run_once()
        except (AgentProtocolError, RemoteAuthError):
            worked = False
        if not worked:
            stop_event.wait(settings.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
