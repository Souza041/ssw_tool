from datetime import datetime, timezone

import pytest

from modules.health_center.dependency_models import (
    DependencyState, ProbeKind, ProbeResult,
)
from modules.health_center.dependency_repository import DependencyRepository


def result(*, detail="Detalhe técnico sanitizado.", metadata=None):
    return ProbeResult(
        dependency_id="ssw_web", probe=ProbeKind.HTTP,
        state=DependencyState.HEALTHY, checked_at=datetime.now(timezone.utc),
        latency_ms=1, reason_code="http_reachable_not_functional",
        detail=detail, metadata=metadata or {},
    )


def forbidden_transaction():
    raise AssertionError("database opened before validation")


@pytest.mark.parametrize("detail", (
    "password=secret", "token=secret", "cookie=session", "mysql://user:secret@host",
))
def test_sensitive_details_are_rejected_before_database_access(detail):
    repository = DependencyRepository(transaction_factory=forbidden_transaction)
    with pytest.raises(ValueError, match="sensível"):
        repository.record_observation(result(detail=detail))


def test_unapproved_metadata_is_rejected_before_database_access():
    repository = DependencyRepository(transaction_factory=forbidden_transaction)
    with pytest.raises(ValueError, match="Metadado"):
        repository.record_observation(result(metadata={"resolved_address": "127.0.0.1"}))
