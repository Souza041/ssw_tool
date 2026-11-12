from modules.health_center.dependency_models import DependencyState, apply_hysteresis


def test_three_failures_confirm_down_and_open_incident():
    first = apply_hysteresis(
        current=DependencyState.HEALTHY, observed=DependencyState.DOWN,
        failures=0, successes=4,
    )
    second = apply_hysteresis(
        current=first.state, observed=DependencyState.DOWN,
        failures=first.consecutive_failures, successes=first.consecutive_successes,
    )
    third = apply_hysteresis(
        current=second.state, observed=DependencyState.DOWN,
        failures=second.consecutive_failures, successes=second.consecutive_successes,
    )
    assert first.state is second.state is DependencyState.DEGRADED
    assert not first.open_incident and not second.open_incident
    assert third.state is DependencyState.DOWN
    assert third.open_incident


def test_two_successes_confirm_recovery_once():
    first = apply_hysteresis(
        current=DependencyState.DOWN, observed=DependencyState.HEALTHY,
        failures=5, successes=0,
    )
    second = apply_hysteresis(
        current=first.state, observed=DependencyState.HEALTHY,
        failures=first.consecutive_failures, successes=first.consecutive_successes,
    )
    assert first.state is DependencyState.DOWN
    assert not first.recover_incident
    assert second.state is DependencyState.HEALTHY
    assert second.recover_incident


def test_unknown_does_not_create_false_outage_and_maintenance_is_stable():
    unknown = apply_hysteresis(
        current=DependencyState.HEALTHY, observed=DependencyState.UNKNOWN,
        failures=0, successes=3,
    )
    maintenance = apply_hysteresis(
        current=DependencyState.MAINTENANCE, observed=DependencyState.DOWN,
        failures=0, successes=0,
    )
    assert unknown.state is DependencyState.UNKNOWN
    assert not unknown.open_incident
    assert maintenance.state is DependencyState.MAINTENANCE
    assert not maintenance.open_incident


def test_hysteresis_thresholds_are_bounded():
    try:
        apply_hysteresis(
            current=DependencyState.UNKNOWN, observed=DependencyState.DOWN,
            failures=0, successes=0, failure_threshold=0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Limiar inválido deveria ser rejeitado")
