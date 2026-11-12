import asyncio
from datetime import datetime, timezone

from modules.health_center.dependency_models import (
    DependencyDefinition, DependencyState, HysteresisDecision, ProbeKind, ProbeResult,
)
from modules.health_center.dependency_monitor import DependencyMonitor, MonitorSettings


def definition(index=0):
    return DependencyDefinition(
        identifier=f"dependency_{index}", name=f"Dependency {index}",
        host=f"dep-{index}.example.test", port=443, probes=(ProbeKind.DNS,),
        enabled=True,
    )


def result(item, state=DependencyState.HEALTHY):
    return ProbeResult(
        dependency_id=item.identifier, probe=ProbeKind.DNS, state=state,
        checked_at=datetime.now(timezone.utc), latency_ms=1.5,
        reason_code="synthetic_result", detail="Resultado sintético sanitizado.",
    )


class Repository:
    def __init__(self, fail=False):
        self.fail = fail
        self.registered = []
        self.observations = []
        self.results = []

    def register(self, item, now):
        if self.fail:
            raise RuntimeError("password=never-leak")
        self.registered.append(item.identifier)

    def record_observation(self, item):
        self.observations.append(item)

    def record_result(self, item, **_kwargs):
        self.results.append(item)
        return HysteresisDecision(DependencyState.HEALTHY, 0, 2)


def settings(**overrides):
    values = {
        "enabled": True, "interval_seconds": 15, "timeout_seconds": 1,
        "max_concurrency": 2, "retries": 0, "failure_threshold": 3,
        "recovery_threshold": 2, "stale_after_seconds": 60,
        "alert_cooldown_seconds": 60,
    }
    values.update(overrides)
    return MonitorSettings(**values)


def test_disabled_monitor_never_touches_repository():
    repository = Repository(fail=True)
    monitor = DependencyMonitor(
        repository=repository, settings=settings(enabled=False),
        definitions=[definition()], probes={}, allowed_hosts=frozenset(),
    )
    assert asyncio.run(monitor.run_cycle()) == []


def test_concurrency_limit_and_cycle_persistence():
    active = 0
    maximum = 0

    async def probe(item, **_kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return result(item)

    repository = Repository()
    definitions = [definition(index) for index in range(5)]
    monitor = DependencyMonitor(
        repository=repository, settings=settings(max_concurrency=2),
        definitions=definitions, probes={ProbeKind.DNS: probe},
        allowed_hosts=frozenset(item.host for item in definitions),
    )
    rows = asyncio.run(monitor.run_cycle())
    assert maximum == 2
    assert len(rows) == len(repository.results) == 5
    assert all(item.probe is ProbeKind.COMPOSITE for item in repository.results)


def test_retry_recovers_without_duplicate_state_transition():
    calls = 0

    async def probe(item, **_kwargs):
        nonlocal calls
        calls += 1
        return result(
            item,
            DependencyState.DOWN if calls == 1 else DependencyState.HEALTHY,
        )

    repository = Repository()
    monitor = DependencyMonitor(
        repository=repository, settings=settings(retries=1),
        definitions=[definition()], probes={ProbeKind.DNS: probe},
        allowed_hosts=frozenset({definition().host}),
    )
    rows = asyncio.run(monitor.run_cycle())
    assert calls == 2
    assert rows[0]["observed_state"] == "HEALTHY"
    assert len(repository.observations) == len(repository.results) == 1


def test_monitor_failure_is_unknown_and_does_not_escape_cycle():
    monitor = DependencyMonitor(
        repository=Repository(fail=True), settings=settings(),
        definitions=[definition()], probes={}, allowed_hosts=frozenset(),
    )
    rows = asyncio.run(monitor.run_cycle())
    assert rows == [{
        "dependency_id": "dependency_0",
        "observed_state": "UNKNOWN",
        "current_state": "UNKNOWN",
        "reason_code": "monitor_internal_failure",
    }]
    assert "password" not in str(rows).lower()


def test_aggregate_uses_worst_probe_without_claiming_success():
    item = DependencyDefinition(
        identifier="ssw_web", name="SSW", host="ssw.example.test", port=443,
        probes=(ProbeKind.DNS, ProbeKind.HTTP), enabled=True,
    )
    healthy = result(item)
    degraded = ProbeResult(
        dependency_id=item.identifier, probe=ProbeKind.HTTP,
        state=DependencyState.DEGRADED, checked_at=datetime.now(timezone.utc),
        latency_ms=10, reason_code="http_server_error",
        detail="Resposta técnica degradada.",
    )
    aggregate = DependencyMonitor._aggregate(item, [healthy, degraded])
    assert aggregate.probe is ProbeKind.COMPOSITE
    assert aggregate.state is DependencyState.DEGRADED
    assert aggregate.reason_code == "http_server_error"


def test_monitor_and_ssw_catalog_are_disabled_by_default(monkeypatch):
    for name in (
        "HEALTH_DEPENDENCY_MONITOR_ENABLED",
        "HEALTH_DEPENDENCY_SSW_ENABLED",
        "HEALTH_DEPENDENCY_SSW_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert MonitorSettings.from_env().enabled is False
    from modules.health_center.dependency_monitor import definitions_from_env
    assert definitions_from_env() == []


def test_ssw_definition_requires_explicit_safe_configuration(monkeypatch):
    from modules.health_center.dependency_monitor import definitions_from_env
    monkeypatch.setenv("HEALTH_DEPENDENCY_SSW_ENABLED", "true")
    monkeypatch.setenv("HEALTH_DEPENDENCY_SSW_URL", "https://ssw.example.test/status")
    rows = definitions_from_env()
    assert len(rows) == 1
    assert rows[0].identifier == "ssw_web"
    assert rows[0].host == "ssw.example.test"
    assert rows[0].path == "/status"
    assert rows[0].mysql_env_prefix is None
