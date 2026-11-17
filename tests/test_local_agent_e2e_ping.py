import json
import secrets
import time

from modules.local_agent import routes
from modules.local_agent.bootstrap import canonical_ping_body, sign_request
from modules.local_agent.executor import HandlerRegistry, LocalAgentExecutor


AGENT_TOKEN = "agent-token-for-e2e-with-more-than-32-characters"
BOOTSTRAP_SECRET = "bootstrap-secret-independent-with-more-than-32-characters"
KEY_ID = "homologacao-admin"
AGENT_ID = "windows-corporativo-01"


class RequestStub:
    session = {}
    client = type("Client", (), {"host": "127.0.0.1"})()


def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_DB", str(tmp_path / "e2e.sqlite3"))
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_TOKENS_JSON", json.dumps({AGENT_ID: AGENT_TOKEN})
    )
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ALLOWED_TASKS", "system.ping")
    monkeypatch.setenv(
        "NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON",
        json.dumps({AGENT_ID: ["system.ping"]}),
    )
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_LEASE_SECONDS", "30")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED", "true")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_KEY_ID", KEY_ID)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET", BOOTSTRAP_SECRET)
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_PER_MINUTE", "5")
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_BOOTSTRAP_PER_HOUR", "20")


def signed(path: str, body: bytes, nonce: str | None = None):
    timestamp = str(int(time.time()))
    nonce = nonce or secrets.token_urlsafe(32)
    return {
        "x_nexus_bootstrap_key_id": KEY_ID,
        "x_nexus_bootstrap_timestamp": timestamp,
        "x_nexus_bootstrap_nonce": nonce,
        "x_nexus_bootstrap_signature": sign_request(
            BOOTSTRAP_SECRET,
            method="POST",
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
        ),
    }


class RouteClient:
    def __init__(self):
        self.heartbeats = 0

    @property
    def headers(self):
        return {
            "x_nexus_agent_id": AGENT_ID,
            "authorization": f"Bearer {AGENT_TOKEN}",
        }

    def claim(self, capabilities):
        response = routes.claim(
            routes.ClaimRequest(capabilities=capabilities), **self.headers
        )
        # Reduz somente o intervalo da thread do teste; o lease do servidor segue em 30s.
        response["lease_seconds"] = 1
        return response

    def heartbeat(self, job_id, lease_token, current, total):
        self.heartbeats += 1
        return routes.heartbeat(
            routes.HeartbeatRequest(
                job_id=job_id,
                lease_token=lease_token,
                progress_current=current,
                progress_total=total,
            ),
            **self.headers,
        )

    def logs(self, job_id, lease_token, entries):
        routes.logs(
            routes.LogsRequest(job_id=job_id, lease_token=lease_token, entries=entries),
            **self.headers,
        )

    def complete(self, job_id, lease_token, **kwargs):
        return routes.complete(
            routes.CompleteRequest(
                job_id=job_id, lease_token=lease_token, **kwargs
            ),
            **self.headers,
        )


def test_system_ping_end_to_end_with_bootstrap_heartbeat_and_result(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    request = RequestStub()
    message = "e2e-homologacao"
    enqueue_path = "/api/local-agent/v1/bootstrap/system-ping"
    enqueue_body = canonical_ping_body(message)
    queued = routes.bootstrap_system_ping(
        request,
        routes.BootstrapPingRequest(message=message),
        **signed(enqueue_path, enqueue_body),
    )
    assert queued["task"] == "system.ping"

    registry = HandlerRegistry()

    def ping_handler(payload, context):
        context.set_progress(0, 1)
        context.log("Ping E2E iniciado.")
        time.sleep(1.15)
        context.raise_if_cancelled()
        context.set_progress(1, 1)
        return {"ok": True, "message": payload["message"]}

    registry.register("system.ping", ping_handler)
    client = RouteClient()
    assert LocalAgentExecutor(client, registry).run_once() is True
    assert client.heartbeats >= 1

    status_path = (
        f"/api/local-agent/v1/bootstrap/system-ping/{queued['job_id']}/status"
    )
    result = routes.bootstrap_system_ping_status(
        request,
        queued["job_id"],
        **signed(status_path, b""),
    )
    assert result["status"] == "SUCCEEDED"
    assert result["progress_current"] == 1
    assert result["result"] == {"ok": True, "message": message}
    assert any("Ping E2E" in entry["message"] for entry in result["logs"])
