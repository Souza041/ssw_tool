"""Worker opt-in do Health Center; não inicia bots nem a aplicação web."""

import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from modules.health_center.dependency_monitor import (
    DependencyMonitor, MonitorSettings, definitions_from_env, log_event,
)


def load_worker_environment(env_file: Path | None = None) -> Path:
    """Carrega o .env sem substituir variáveis fornecidas pelo Supervisor."""
    path = env_file or PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=path, override=False)
    return path


async def _main() -> None:
    load_worker_environment()
    settings = MonitorSettings.from_env()
    if not settings.enabled:
        log_event(
            logging.WARNING, "dependency_worker_disabled",
            enabled=False, interval_seconds=settings.interval_seconds,
        )
        raise RuntimeError("HEALTH_DEPENDENCY_MONITOR_ENABLED precisa ser true.")
    definitions = definitions_from_env()
    monitor = DependencyMonitor(settings=settings, definitions=definitions)
    log_event(
        logging.INFO, "dependency_worker_started",
        enabled=True, dependencies=len(definitions),
        interval_seconds=settings.interval_seconds,
    )
    stop_event = asyncio.Event()
    try:
        await monitor.run_forever(stop_event)
    finally:
        stop_event.set()
        log_event(logging.INFO, "dependency_worker_stopped", enabled=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(_main())
