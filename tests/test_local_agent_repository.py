from datetime import datetime, timedelta, timezone

import pytest

from modules.local_agent.repository import LocalAgentRepository


NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def repository(tmp_path):
    return LocalAgentRepository(tmp_path / "local-agent.sqlite3")


def test_claim_is_exclusive_and_completion_preserves_audit(tmp_path):
    repo = repository(tmp_path)
    job_id = repo.enqueue(task="system.ping", payload={"message": "teste"}, now=NOW)

    first = repo.claim(
        agent_id="windows-cwb-01",
        capabilities={"system.ping"},
        lease_seconds=60,
        now=NOW,
    )
    second = repo.claim(
        agent_id="windows-cwb-02",
        capabilities={"system.ping"},
        lease_seconds=60,
        now=NOW,
    )

    assert first["id"] == job_id
    assert first["payload"] == {"message": "teste"}
    assert second is None

    heartbeat = repo.heartbeat(
        job_id=job_id,
        agent_id="windows-cwb-01",
        lease_token=first["lease_token"],
        lease_seconds=60,
        progress_current=1,
        progress_total=2,
        now=NOW + timedelta(seconds=10),
    )
    assert heartbeat["cancellation_requested"] is False
    assert repo.append_logs(
        job_id=job_id,
        agent_id="windows-cwb-01",
        lease_token=first["lease_token"],
        entries=[{"level": "INFO", "message": "etapa segura concluída"}],
        now=NOW + timedelta(seconds=11),
    ) == 1
    status = repo.complete(
        job_id=job_id,
        agent_id="windows-cwb-01",
        lease_token=first["lease_token"],
        succeeded=True,
        result={"ok": True},
        now=NOW + timedelta(seconds=12),
    )

    assert status == "SUCCEEDED"
    detail = repo.get_job(job_id)
    assert detail["status"] == "SUCCEEDED"
    assert detail["result"] == {"ok": True}
    assert detail["progress_current"] == 1
    assert detail["logs"][0]["message"] == "etapa segura concluída"
    assert "lease_token" not in detail


def test_expired_lease_is_requeued_for_another_agent(tmp_path):
    repo = repository(tmp_path)
    job_id = repo.enqueue(
        task="system.ping", payload={}, max_attempts=2, now=NOW
    )
    first = repo.claim(
        agent_id="windows-cwb-01", capabilities={"system.ping"},
        lease_seconds=30, now=NOW,
    )
    with pytest.raises(LookupError):
        repo.append_logs(
            job_id=job_id, agent_id="windows-cwb-01",
            lease_token=first["lease_token"],
            entries=[{"level": "INFO", "message": "atrasado"}],
            now=NOW + timedelta(seconds=31),
        )
    with pytest.raises(LookupError):
        repo.complete(
            job_id=job_id, agent_id="windows-cwb-01",
            lease_token=first["lease_token"], succeeded=True,
            now=NOW + timedelta(seconds=31),
        )
    second = repo.claim(
        agent_id="windows-cwb-02", capabilities={"system.ping"},
        lease_seconds=30, now=NOW + timedelta(seconds=31),
    )
    assert second["id"] == job_id
    assert second["lease_token"] != first["lease_token"]
    assert second["attempt"] == 2
    with pytest.raises(LookupError):
        repo.heartbeat(
            job_id=job_id,
            agent_id="windows-cwb-01",
            lease_token=first["lease_token"],
            lease_seconds=30,
            now=NOW + timedelta(seconds=32),
        )


def test_expired_final_attempt_fails_closed(tmp_path):
    repo = repository(tmp_path)
    job_id = repo.enqueue(
        task="system.ping", payload={}, max_attempts=1, now=NOW
    )
    repo.claim(
        agent_id="windows-cwb-01", capabilities={"system.ping"},
        lease_seconds=30, now=NOW,
    )
    assert repo.claim(
        agent_id="windows-cwb-02", capabilities={"system.ping"},
        lease_seconds=30, now=NOW + timedelta(seconds=31),
    ) is None
    detail = repo.get_job(job_id)
    assert detail["status"] == "FAILED"
    assert "tentativas esgotadas" in detail["error_message"]


def test_cancel_is_immediate_in_queue_and_cooperative_during_lease(tmp_path):
    repo = repository(tmp_path)
    queued = repo.enqueue(task="system.ping", payload={}, now=NOW)
    assert repo.request_cancel(queued, now=NOW) == "CANCELLED"
    assert repo.get_job(queued)["status"] == "CANCELLED"

    leased = repo.enqueue(task="system.ping", payload={}, now=NOW)
    claim = repo.claim(
        agent_id="windows-cwb-01", capabilities={"system.ping"},
        lease_seconds=60, now=NOW,
    )
    assert repo.request_cancel(leased, now=NOW) == "CANCELLATION_REQUESTED"
    heartbeat = repo.heartbeat(
        job_id=leased, agent_id="windows-cwb-01",
        lease_token=claim["lease_token"], lease_seconds=60, now=NOW,
    )
    assert heartbeat["cancellation_requested"] is True
    assert repo.complete(
        job_id=leased, agent_id="windows-cwb-01",
        lease_token=claim["lease_token"], succeeded=True, result={"ok": True}, now=NOW,
    ) == "CANCELLED"


def test_cancelled_agent_that_disappears_does_not_leave_stuck_queue(tmp_path):
    repo = repository(tmp_path)
    job_id = repo.enqueue(task="system.ping", payload={}, now=NOW)
    repo.claim(
        agent_id="windows-cwb-01", capabilities={"system.ping"},
        lease_seconds=30, now=NOW,
    )
    assert repo.request_cancel(job_id, now=NOW) == "CANCELLATION_REQUESTED"
    assert repo.claim(
        agent_id="windows-cwb-02", capabilities={"system.ping"},
        lease_seconds=30, now=NOW + timedelta(seconds=31),
    ) is None
    assert repo.get_job(job_id)["status"] == "CANCELLED"


def test_payload_logs_progress_and_result_are_bounded(tmp_path):
    repo = LocalAgentRepository(
        tmp_path / "bounded.sqlite3",
        max_payload_bytes=30,
        max_result_bytes=30,
        max_log_bytes=20,
    )
    with pytest.raises(ValueError, match="Payload excede"):
        repo.enqueue(task="system.ping", payload={"value": "x" * 40})
    job_id = repo.enqueue(task="system.ping", payload={})
    claim = repo.claim(
        agent_id="windows-cwb-01", capabilities={"system.ping"}, lease_seconds=60
    )
    with pytest.raises(ValueError, match="acima do limite"):
        repo.append_logs(
            job_id=job_id, agent_id="windows-cwb-01",
            lease_token=claim["lease_token"],
            entries=[{"level": "INFO", "message": "x" * 21}],
        )
    with pytest.raises(ValueError, match="Resultado excede"):
        repo.complete(
            job_id=job_id, agent_id="windows-cwb-01",
            lease_token=claim["lease_token"], succeeded=True,
            result={"value": "x" * 40},
        )
