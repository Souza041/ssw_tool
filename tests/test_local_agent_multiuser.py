from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from modules.local_agent.config import load_server_settings, remote_auth_agent_for
from modules.local_agent.remote_auth import (
    RemoteAuthBroker, RemoteAuthConflict, RemoteIdentity, identity_fingerprint, response_proof,
)
from modules.local_agent.repository import LocalAgentRepository


NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
TASKS = frozenset({
    "system.ping", "ssw.documentos_gce", "ssw.op001.nfd",
    "ssw.op001.transporte", "ssw.session.close",
})


def test_remote_proof_accepts_server_side_hash_without_storing_raw_token():
    token = "individual-token-with-more-than-thirty-two-characters"
    kwargs = {
        "request_id": "r" * 48, "challenge": "c" * 48,
        "identity": "f" * 64, "agent_id": "note-alice-01", "accepted": True,
    }
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert response_proof(token, **kwargs) == response_proof(f"sha256:{digest}", **kwargs)


def test_pairing_mode_never_falls_back_arbitrarily_to_central_agent(monkeypatch, tmp_path):
    central = "windows-corporativo-01"
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_DB", str(tmp_path / "config.sqlite3"))
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_TOKENS_JSON", '{"%s":"%s"}' % (central, "t" * 48))
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ALLOWED_TASKS", ",".join(sorted(TASKS)))
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON", '{"%s":["system.ping"]}' % central)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_PAIRING_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_PAIRED_TASKS", ",".join(sorted(TASKS)))
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_AGENT_ID", central)
    monkeypatch.setenv("NEXUS_SSW_REMOTE_AUTH_BINDINGS_JSON", "{}")
    settings = load_server_settings()
    with pytest.raises(LookupError, match="sem computador"):
        remote_auth_agent_for(settings, dominio="ROD", usuario="nao-autorizado")


def paired(repo, identity, machine, *, now=NOW):
    code = repo.create_pairing(
        identity_key=identity, tasks=TASKS, created_by="admin",
        ttl_seconds=600, now=now,
    )
    return code, repo.redeem_pairing(code=code, machine_name=machine, now=now)


def test_two_users_pair_to_unique_agents_route_exclusively_and_block_cross_access(tmp_path):
    repo = LocalAgentRepository(tmp_path / "multi.sqlite3")
    code_a, first = paired(repo, "ROD/alice", "NOTE-ALICE")
    _, second = paired(repo, "ROD/bob", "NOTE-BOB")

    assert first["agent_id"] != second["agent_id"]
    assert first["token"] != second["token"]
    assert repo.bound_agent(dominio="rod", usuario="ALICE") == first["agent_id"]
    assert repo.bound_agent(dominio="ROD", usuario="bob") == second["agent_id"]
    assert repo.authenticate_dynamic_agent(
        agent_id=first["agent_id"], token=second["token"]
    ) is None
    with pytest.raises(LookupError, match="utilizado"):
        repo.redeem_pairing(code=code_a, machine_name="OUTRO", now=NOW)

    gce = repo.enqueue(
        task="ssw.documentos_gce", payload={}, target_agent_id=first["agent_id"], now=NOW
    )
    op001 = repo.enqueue(
        task="ssw.op001.nfd", payload={}, target_agent_id=second["agent_id"], now=NOW
    )
    claim_b = repo.claim(
        agent_id=second["agent_id"], capabilities=TASKS, lease_seconds=60, now=NOW
    )
    claim_a = repo.claim(
        agent_id=first["agent_id"], capabilities=TASKS, lease_seconds=60, now=NOW
    )
    assert claim_b["id"] == op001
    assert claim_a["id"] == gce
    with pytest.raises(LookupError):
        repo.complete(
            job_id=op001, agent_id=first["agent_id"],
            lease_token=claim_b["lease_token"], succeeded=True, now=NOW,
        )


def test_targeted_remote_auth_skips_other_user_and_rejects_cross_completion():
    broker = RemoteAuthBroker()
    binding_a, binding_b = "a" * 48, "b" * 48
    request_a = broker.create(
        RemoteIdentity("ROD", "alice", "CWB", binding_a),
        session_binding=binding_a, ttl_seconds=60, per_minute=5, max_pending=5,
        target_agent_id="note-alice-01",
    )
    request_b = broker.create(
        RemoteIdentity("ROD", "bob", "CWB", binding_b),
        session_binding=binding_b, ttl_seconds=60, per_minute=5, max_pending=5,
        target_agent_id="note-bob-01",
    )
    claimed_b = broker.claim(agent_id="note-bob-01")
    assert claimed_b["request_id"] == request_b
    assert claimed_b["identity"]["usuario"] == "bob"
    with pytest.raises(RemoteAuthConflict):
        broker.complete(
            request_id=request_b, lease_token=claimed_b["lease_token"],
            agent_id="note-alice-01", accepted=True,
            identity=claimed_b["identity_fingerprint"], proof="0" * 64, secret="x" * 48,
        )
    claimed_a = broker.claim(agent_id="note-alice-01")
    assert claimed_a["request_id"] == request_a


def test_pairing_expiration_revocation_disconnect_offline_and_concurrency(tmp_path):
    repo = LocalAgentRepository(tmp_path / "lifecycle.sqlite3")
    expired = repo.create_pairing(
        identity_key="ROD/expired", tasks=TASKS, created_by="admin",
        ttl_seconds=60, now=NOW,
    )
    with pytest.raises(LookupError, match="expirado"):
        repo.redeem_pairing(
            code=expired, machine_name="NOTE-OLD", now=NOW + timedelta(seconds=61)
        )

    _, paired_agent = paired(repo, "ROD/alice", "NOTE-ALICE")
    agent_id = paired_agent["agent_id"]
    binding = "s" * 48
    repo.claim(
        agent_id=agent_id, capabilities=TASKS, lease_seconds=60, now=NOW,
        sessions=[{
            "session_binding": binding, "state": "authenticated",
            "operation": "", "observed_unit": "CWB",
        }],
    )
    assert repo.session_status(
        agent_id=agent_id, session_binding=binding, offline_seconds=30, now=NOW
    )["state"] == "authenticated"
    repo.claim(
        agent_id=agent_id, capabilities=TASKS, lease_seconds=60,
        now=NOW + timedelta(seconds=1), sessions=[{
            "session_binding": binding, "state": "reauthentication_required",
            "operation": "", "observed_unit": "CWB",
        }],
    )
    assert repo.session_status(
        agent_id=agent_id, session_binding=binding, offline_seconds=30,
        now=NOW + timedelta(seconds=1),
    )["state"] == "reauthentication_required"
    assert repo.session_status(
        agent_id=agent_id, session_binding=binding, offline_seconds=30,
        now=NOW + timedelta(seconds=32),
    )["state"] == "offline"

    first = repo.enqueue(
        task="ssw.op001.nfd", payload={}, concurrency_key=f"ssw.session:{binding}",
        target_agent_id=agent_id, now=NOW + timedelta(seconds=2),
    )
    second = repo.enqueue(
        task="ssw.documentos_gce", payload={}, concurrency_key=f"ssw.session:{binding}",
        target_agent_id=agent_id, now=NOW + timedelta(seconds=3),
    )
    lease = repo.claim(
        agent_id=agent_id, capabilities=TASKS, lease_seconds=60,
        now=NOW + timedelta(seconds=3),
    )
    assert lease["id"] == first
    assert repo.claim(
        agent_id=agent_id, capabilities=TASKS, lease_seconds=60,
        now=NOW + timedelta(seconds=4),
    ) is None
    repo.complete(
        job_id=first, agent_id=agent_id, lease_token=lease["lease_token"],
        succeeded=True, now=NOW + timedelta(seconds=4),
    )
    assert repo.claim(
        agent_id=agent_id, capabilities=TASKS, lease_seconds=60,
        now=NOW + timedelta(seconds=5),
    )["id"] == second

    assert repo.revoke_agent(agent_id, now=NOW + timedelta(seconds=6)) is True
    assert repo.authenticate_dynamic_agent(
        agent_id=agent_id, token=paired_agent["token"]
    ) is None
    assert repo.bound_agent(dominio="ROD", usuario="alice") is None
