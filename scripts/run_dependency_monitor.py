"""Worker opt-in do Health Center; não inicia bots nem a aplicação web."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.health_center.dependency_monitor import DependencyMonitor, MonitorSettings


async def _main() -> None:
    settings = MonitorSettings.from_env()
    if not settings.enabled:
        raise RuntimeError("HEALTH_DEPENDENCY_MONITOR_ENABLED precisa ser true.")
    monitor = DependencyMonitor(settings=settings)
    stop_event = asyncio.Event()
    try:
        await monitor.run_forever(stop_event)
    finally:
        stop_event.set()


if __name__ == "__main__":
    asyncio.run(_main())
