"""Executor foreground do Nexus Local Agent para Windows; não instala agendamento."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time


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
from modules.local_agent.windows_runtime import (
    SingleInstanceLock,
    configure_runtime_logging,
    sanitize_operational_log,
)


def run_continuously(
    run_once,
    *,
    stop_event: threading.Event,
    poll_seconds: int,
    logger: logging.Logger,
) -> None:
    consecutive_failures = 0
    connection_unavailable = False
    last_health_log = time.monotonic()
    while not stop_event.is_set():
        try:
            worked = run_once()
        except (AgentProtocolError, RemoteAuthError) as exc:
            consecutive_failures += 1
            if not connection_unavailable:
                logger.warning(
                    "Comunicação com a VPS indisponível; novas tentativas continuarão; motivo=%s.",
                    sanitize_operational_log(exc),
                )
                connection_unavailable = True
            delay = min(60, max(poll_seconds, 2 ** min(consecutive_failures, 6)))
            stop_event.wait(delay)
            continue
        if connection_unavailable:
            logger.info("Comunicação com a VPS restabelecida.")
            connection_unavailable = False
        consecutive_failures = 0
        now = time.monotonic()
        if now - last_health_log >= 300:
            logger.info("Polling ativo; comunicação com a VPS disponível.")
            last_health_log = now
        if not worked:
            stop_event.wait(poll_seconds)


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
    program_data = Path(os.getenv("ProgramData", r"C:\ProgramData")) / "Nexus"
    parser.add_argument(
        "--log-file", type=Path,
        default=program_data / "logs" / "local-agent.log",
        help="Log operacional rotativo, sem payloads ou credenciais.",
    )
    parser.add_argument(
        "--lock-file", type=Path,
        default=program_data / "runtime" / "local-agent.lock",
        help="Lock local que impede executores concorrentes.",
    )
    args = parser.parse_args()
    logger = configure_runtime_logging(args.log_file)
    lock = SingleInstanceLock(args.lock_file)
    if not lock.acquire():
        logger.warning("Inicialização recusada: outra instância do agente já está ativa.")
        return 75
    try:
        if args.env_file:
            load_private_environment(args.env_file, project_root=PROJECT_ROOT)
        if os.getenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "sim", "on",
        }:
            runtime_value = os.getenv("NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE", "").strip()
            if not runtime_value:
                raise RuntimeError("Handlers SSW exigem arquivo privado de runtime.")
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
                logger.info("Solicitação de autenticação remota processada.")
                return True
            return executor.run_once()

        logger.info(
            "Nexus Local Agent iniciado; agente=%s; autenticação_remota=%s; handlers_ssw=%s.",
            settings.agent_id,
            "ativa" if settings.remote_auth_enabled else "inativa",
            "ativos" if os.getenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "false").lower() == "true" else "inativos",
        )
        if args.once:
            run_once()
            logger.info("Polling único concluído.")
            return 0

        stop_event = threading.Event()

        def stop(*_args):
            stop_event.set()

        signal.signal(signal.SIGINT, stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop)
        run_continuously(
            run_once,
            stop_event=stop_event,
            poll_seconds=settings.poll_seconds,
            logger=logger,
        )
        logger.info("Nexus Local Agent encerrado normalmente.")
        return 0
    except BaseException as exc:
        logger.error("Nexus Local Agent encerrado inesperadamente; tipo=%s.", type(exc).__name__)
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
