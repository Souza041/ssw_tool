import asyncio
from unittest.mock import patch

from modules.health_center.dependency_models import (
    DependencyDefinition, DependencyState, ProbeKind,
)
from modules.health_center.dependency_probes import (
    probe_dns, probe_http, probe_mysql, probe_tcp, probe_tls,
)


def definition(*probes, **overrides):
    values = {
        "identifier": "ssw_web",
        "name": "SSW Web",
        "host": "ssw.example.test",
        "port": 443,
        "scheme": "https",
        "path": "/health",
        "probes": probes or (ProbeKind.DNS,),
        "enabled": True,
    }
    values.update(overrides)
    return DependencyDefinition(**values)


async def public_resolver(_host, port):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


async def private_resolver(_host, port):
    return [(2, 1, 6, "", ("127.0.0.1", port))]


def run(coro):
    return asyncio.run(coro)


def test_dns_success_and_ssrf_private_address_blocked():
    allowed = frozenset({"ssw.example.test"})
    healthy = run(probe_dns(
        definition(), timeout=1, allowed_hosts=allowed, resolver=public_resolver,
    ))
    blocked = run(probe_dns(
        definition(), timeout=1, allowed_hosts=allowed, resolver=private_resolver,
    ))
    assert healthy.state is DependencyState.HEALTHY
    assert healthy.metadata == {"address_count": 1}
    assert blocked.state is DependencyState.DOWN
    assert blocked.reason_code == "destination_blocked"
    assert "127.0.0.1" not in blocked.detail


def test_destination_outside_allowlist_is_blocked_before_connection():
    result = run(probe_tcp(
        definition(ProbeKind.TCP), timeout=1,
        allowed_hosts=frozenset(), resolver=public_resolver,
    ))
    assert result.state is DependencyState.DOWN
    assert result.reason_code == "destination_blocked"


class Writer:
    def __init__(self, ssl_object=None):
        self.closed = False
        self.ssl_object = ssl_object

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None

    def get_extra_info(self, name):
        return self.ssl_object if name == "ssl_object" else None


class Certificate:
    def getpeercert(self):
        return {"notAfter": "Jan 01 00:00:00 2099 GMT"}


def test_tcp_and_tls_success_close_connections():
    tcp_writer = Writer()
    tls_writer = Writer(Certificate())
    calls = []

    async def tcp_connect(*args, **kwargs):
        calls.append((args, kwargs))
        return object(), tcp_writer

    async def tls_connect(*args, **kwargs):
        calls.append((args, kwargs))
        return object(), tls_writer

    allowed = frozenset({"ssw.example.test"})
    with patch("asyncio.open_connection", tcp_connect):
        tcp = run(probe_tcp(
            definition(ProbeKind.TCP), timeout=1,
            allowed_hosts=allowed, resolver=public_resolver,
        ))
    with patch("asyncio.open_connection", tls_connect):
        tls = run(probe_tls(
            definition(ProbeKind.TLS), timeout=1,
            allowed_hosts=allowed, resolver=public_resolver,
        ))
    assert tcp.state is DependencyState.HEALTHY and tcp_writer.closed
    assert tls.state is DependencyState.HEALTHY and tls_writer.closed
    assert calls[0][0][0] == calls[1][0][0] == "93.184.216.34"
    assert calls[1][1]["server_hostname"] == "ssw.example.test"


def test_http_does_not_claim_functional_availability_or_follow_external_redirect():
    allowed = frozenset({"ssw.example.test"})
    with patch(
        "modules.health_center.dependency_probes._http_request",
        return_value=(200, None),
    ):
        reachable = run(probe_http(
            definition(ProbeKind.HTTP), timeout=1,
            allowed_hosts=allowed, resolver=public_resolver,
        ))
    with patch(
        "modules.health_center.dependency_probes._http_request",
        return_value=(302, "https://evil.example/collect"),
    ):
        redirected = run(probe_http(
            definition(ProbeKind.HTTP), timeout=1,
            allowed_hosts=allowed, resolver=public_resolver,
        ))
    assert reachable.state is DependencyState.HEALTHY
    assert reachable.reason_code == "http_reachable_not_functional"
    assert reachable.metadata["functional_availability_proven"] is False
    assert redirected.state is DependencyState.DOWN
    assert redirected.reason_code == "redirect_blocked"


def test_http_timeout_is_sanitized():
    with patch(
        "modules.health_center.dependency_probes._http_request",
        side_effect=TimeoutError("token=must-not-leak"),
    ):
        result = run(probe_http(
            definition(ProbeKind.HTTP), timeout=1,
            allowed_hosts=frozenset({"ssw.example.test"}), resolver=public_resolver,
        ))
    assert result.state is DependencyState.DOWN
    assert result.reason_code == "timeout"
    assert "token" not in result.detail.lower()


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement):
        assert statement in {"START TRANSACTION READ ONLY", "SELECT 1"}

    def fetchone(self):
        return (1,)


class Connection:
    def __init__(self):
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return Cursor()

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_mysql_probe_uses_read_only_transaction(monkeypatch):
    connection = Connection()
    received = {}

    def connector(**kwargs):
        received.update(kwargs)
        return connection

    monkeypatch.setenv("DEPTEST_USER", "readonly")
    monkeypatch.setenv("DEPTEST_PASSWORD", "synthetic")
    monkeypatch.setenv("DEPTEST_NAME", "disposable")
    result = run(probe_mysql(
        definition(
            ProbeKind.MYSQL, scheme="mysql", port=3306,
            mysql_env_prefix="DEPTEST",
        ),
        timeout=1, allowed_hosts=frozenset({"ssw.example.test"}),
        resolver=public_resolver, connector=connector,
    ))
    assert result.state is DependencyState.HEALTHY
    assert connection.rolled_back and connection.closed
    assert received["host"] == "93.184.216.34"
