"""Motor contínuo opt-in; nunca é iniciado pelo FastAPI ou por schedulers de bots."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from modules.health_center.dependency_models import (
    DependencyDefinition, DependencyState, ProbeKind, ProbeResult,
)
from modules.health_center.dependency_probes import PROBES, allowed_hosts_from_env
from modules.health_center.dependency_repository import DependencyRepository


def _boolean(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "true" if default else "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"Flag inválida: {name}")
    return value == "true"


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"Valor inválido: {name}") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"Valor fora do intervalo: {name}")
    return value


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"Valor inválido: {name}") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"Valor fora do intervalo: {name}")
    return value


@dataclass(frozen=True)
class MonitorSettings:
    enabled: bool = False
    interval_seconds: int = 60
    timeout_seconds: float = 5.0
    max_concurrency: int = 4
    retries: int = 1
    failure_threshold: int = 3
    recovery_threshold: int = 2
    stale_after_seconds: int = 300
    alert_cooldown_seconds: int = 1800

    @classmethod
    def from_env(cls) -> "MonitorSettings":
        return cls(
            enabled=_boolean("HEALTH_DEPENDENCY_MONITOR_ENABLED"),
            interval_seconds=_integer("HEALTH_DEPENDENCY_INTERVAL_SECONDS", 60, 15, 86400),
            timeout_seconds=_float("HEALTH_DEPENDENCY_TIMEOUT_SECONDS", 5.0, 0.2, 60.0),
            max_concurrency=_integer("HEALTH_DEPENDENCY_MAX_CONCURRENCY", 4, 1, 32),
            retries=_integer("HEALTH_DEPENDENCY_RETRIES", 1, 0, 3),
            failure_threshold=_integer("HEALTH_DEPENDENCY_FAILURE_THRESHOLD", 3, 1, 20),
            recovery_threshold=_integer("HEALTH_DEPENDENCY_RECOVERY_THRESHOLD", 2, 1, 20),
            stale_after_seconds=_integer("HEALTH_DEPENDENCY_STALE_AFTER_SECONDS", 300, 30, 86400),
            alert_cooldown_seconds=_integer("HEALTH_DEPENDENCY_ALERT_COOLDOWN_SECONDS", 1800, 60, 86400),
        )


def definitions_from_env() -> list[DependencyDefinition]:
    """Catálogo inicial SSW; não usa usuário, cookie, senha ou login automatizado."""
    if not _boolean("HEALTH_DEPENDENCY_SSW_ENABLED"):
        return []
    parsed = urlparse(os.getenv("HEALTH_DEPENDENCY_SSW_URL", ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HEALTH_DEPENDENCY_SSW_URL inválida.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    probes = [ProbeKind.DNS, ProbeKind.TCP]
    if parsed.scheme == "https":
        probes.append(ProbeKind.TLS)
    probes.append(ProbeKind.HTTP)
    return [DependencyDefinition(
        identifier="ssw_web",
        name="SSW Web",
        host=parsed.hostname,
        port=port,
        scheme=parsed.scheme,
        path=parsed.path or "/",
        probes=tuple(probes),
        enabled=True,
        metadata={"http_method": os.getenv("HEALTH_DEPENDENCY_SSW_HTTP_METHOD", "HEAD")},
    )]


class DependencyMonitor:
    def __init__(
        self, *, repository: DependencyRepository | None = None,
        settings: MonitorSettings | None = None,
        definitions: list[DependencyDefinition] | None = None,
        probes: dict | None = None,
        allowed_hosts: frozenset[str] | None = None,
    ):
        self.repository = repository or DependencyRepository()
        self.settings = settings or MonitorSettings.from_env()
        self.definitions = definitions if definitions is not None else definitions_from_env()
        self.probes = probes or PROBES
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else allowed_hosts_from_env()

    async def _probe_with_retry(
        self, definition: DependencyDefinition, kind: ProbeKind,
    ) -> ProbeResult:
        result = ProbeResult.unknown(definition.identifier, kind, "probe_not_run")
        for attempt in range(self.settings.retries + 1):
            probe = self.probes[kind]
            try:
                result = await probe(
                    definition,
                    timeout=self.settings.timeout_seconds,
                    allowed_hosts=self.allowed_hosts,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                result = ProbeResult.unknown(
                    definition.identifier, kind, "monitor_internal_failure"
                )
            if result.state not in {DependencyState.DOWN, DependencyState.UNKNOWN}:
                break
            if attempt < self.settings.retries:
                await asyncio.sleep(min(0.25 * (2 ** attempt), 1.0))
        return result

    @staticmethod
    def _aggregate(definition: DependencyDefinition, results: list[ProbeResult]) -> ProbeResult:
        rank = {
            DependencyState.HEALTHY: 0,
            DependencyState.UNKNOWN: 1,
            DependencyState.DEGRADED: 2,
            DependencyState.DOWN: 3,
            DependencyState.MAINTENANCE: 0,
        }
        worst = max(results, key=lambda item: rank[item.state])
        latency = sum(item.latency_ms or 0 for item in results)
        return ProbeResult(
            dependency_id=definition.identifier,
            probe=ProbeKind.COMPOSITE,
            state=worst.state,
            checked_at=max(item.checked_at for item in results),
            latency_ms=latency,
            reason_code=worst.reason_code,
            detail=worst.detail,
            http_status=worst.http_status,
        )

    async def _run_dependency(self, definition: DependencyDefinition) -> dict:
        if not definition.enabled:
            return {"dependency_id": definition.identifier, "state": "SKIPPED"}
        try:
            self.repository.register(definition, now=datetime.now(timezone.utc))
            results = []
            for kind in definition.probes:
                result = await self._probe_with_retry(definition, kind)
                results.append(result)
                self.repository.record_observation(result)
            aggregate = self._aggregate(definition, results)
            decision = self.repository.record_result(
                aggregate,
                failure_threshold=self.settings.failure_threshold,
                recovery_threshold=self.settings.recovery_threshold,
                stale_after_seconds=self.settings.stale_after_seconds,
                alert_cooldown_seconds=self.settings.alert_cooldown_seconds,
            )
            return {
                "dependency_id": definition.identifier,
                "observed_state": aggregate.state.value,
                "current_state": decision.state.value,
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            return {
                "dependency_id": definition.identifier,
                "observed_state": DependencyState.UNKNOWN.value,
                "current_state": DependencyState.UNKNOWN.value,
                "reason_code": "monitor_internal_failure",
            }

    async def run_cycle(self) -> list[dict]:
        if not self.settings.enabled:
            return []
        semaphore = asyncio.Semaphore(self.settings.max_concurrency)

        async def limited(definition):
            async with semaphore:
                return await self._run_dependency(definition)

        return list(await asyncio.gather(*(limited(item) for item in self.definitions)))

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Loop explícito; falhas são isoladas e nunca alcançam bots operacionais."""
        if not self.settings.enabled:
            return
        while not stop_event.is_set():
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self.settings.interval_seconds
                )
            except asyncio.TimeoutError:
                continue
