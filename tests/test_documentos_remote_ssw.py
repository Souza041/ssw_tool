from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import re
import hashlib
import json
import sqlite3
import os
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
import pytest

from modules.documentos import remote_ssw
from modules.local_agent import routes, ssw_tasks
from modules.local_agent.config import GCE_MAX_REPORT_BYTES, GCE_PIPELINE_TASK, GCE_QUERY_TASK, load_server_settings
from modules.local_agent.delegation import individual_permission_ceiling
from modules.local_agent import windows_app
from modules.local_agent.executor import AgentClient, ExecutionContext
from modules.local_agent.repository import LocalAgentRepository, utc_now
from modules.local_agent.windows_credentials import load_config, protect_token, save_config
from ssw import browser_transport
from ssw.client import SSWClient
from web.session_store import RemoteAuthenticatedClient


class TestAgentHTTP:
    __test__ = False

    def __init__(self, http):
        self.http = http

    def post(self, url, *, timeout, **options):
        return self.http.post(url, **options)

    def put(self, url, *, data, timeout, allow_redirects, **options):
        return self.http.put(url, content=data, follow_redirects=allow_redirects, **options)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "queue.sqlite"
    tasks = f"system.ping,{GCE_PIPELINE_TASK},{GCE_QUERY_TASK},ssw.op001.nfd,ssw.op001.transporte,ssw.session.close"
    central = "windows-central"
    environments = {
        "NEXUS_LOCAL_AGENT_DB": str(path),
        "NEXUS_LOCAL_AGENT_ENABLED": "true",
        "NEXUS_LOCAL_AGENT_PAIRING_ENABLED": "true",
        "NEXUS_LOCAL_AGENT_ALLOWED_TASKS": tasks,
        "NEXUS_LOCAL_AGENT_PAIRED_TASKS": tasks,
        "NEXUS_SSW_DELEGATION_ENABLED": "true",
        "NEXUS_SSW_DELEGATED_TASKS": tasks.removeprefix("system.ping,"),
        "NEXUS_SSW_REMOTE_AUTH_AGENT_ID": central,
        "NEXUS_LOCAL_AGENT_TOKENS_JSON": json.dumps({central: "c" * 48}),
        "NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON": json.dumps({central: [GCE_PIPELINE_TASK]}),
    }
    for key, value in environments.items():
        monkeypatch.setenv(key, value)
    repo = LocalAgentRepository(path)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-test-key")
    app.include_router(routes.router)
    http = TestClient(app)
    agents = []
    for user in ("alice", "bob"):
        grants = {"system.ping", "ssw.op001.nfd", "ssw.op001.transporte", "ssw.session.close"}
        if user == "bob":
            grants.add(GCE_PIPELINE_TASK)
        code = repo.create_pairing(
            identity_key=f"rod/{user}",
            tasks=frozenset(grants),
            created_by="admin", ttl_seconds=120,
        )
        agent = repo.redeem_pairing(code=code, machine_name=f"note-{user}")
        settings = SimpleNamespace(
            server_url="http://testserver", agent_id=agent["agent_id"],
            token=agent["token"], request_timeout_seconds=2,
        )
        browser = RemoteAuthenticatedClient("ROD", user, "CWB", user * 12, agent["agent_id"])
        agents.append((browser, AgentClient(settings, session=TestAgentHTTP(http))))
    return repo, http, agents


def test_manager_security_revokes_future_claims_without_canceling_existing_lease(setup, monkeypatch):
    repo, http, ((alice, legacy), (bob, _)) = setup
    admin = SimpleNamespace(dominio="ROD", usuario="admin")
    monkeypatch.setattr(routes, "is_admin", lambda _: True)
    monkeypatch.setattr(routes, "obter_client", lambda _: admin)
    page = http.get("/admin/agent-manager")
    assert page.status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    form = {"agent_id": alice.agent_id, "identity": "rod/alice", "task": GCE_QUERY_TASK,
            "action": "grant", "csrf": csrf}
    endpoint = "/admin/agent-manager/permissions"
    assert http.post(endpoint, data={**form, "csrf": "wrong"}).status_code == 403
    assert http.post(endpoint, data={**form, "identity": "rod/bob"}).status_code == 409
    assert http.post(endpoint, data={**form, "task": GCE_PIPELINE_TASK}).status_code == 422
    monkeypatch.setattr(routes, "is_admin", lambda _: False)
    assert http.post(endpoint, data=form).status_code == 403
    monkeypatch.setattr(routes, "is_admin", lambda _: True)

    older = legacy._post("/api/local-agent/v1/claim", {"capabilities": ["system.ping"]})
    assert older == {"job": None, "lease_seconds": 120}
    modern = AgentClient(legacy.settings, session=legacy.session, agent_version="0.2.1")
    assert GCE_QUERY_TASK not in modern.claim(["system.ping", GCE_QUERY_TASK])["permissions"]["tasks"]
    assert http.post(endpoint, data=form, follow_redirects=False).status_code == 303
    assert "Sincronização pendente" in http.get("/admin/agent-manager").text

    def enqueue():
        return repo.enqueue(task=GCE_QUERY_TASK, payload={}, target_agent_id=alice.agent_id, max_attempts=1)

    running = enqueue()
    assert modern.claim(["system.ping", GCE_QUERY_TASK])["job"] is None
    offer = modern.claim(["system.ping", GCE_QUERY_TASK])["job"]
    assert offer["id"] == running
    waiting = enqueue()
    assert "Sincronizado" in http.get("/admin/agent-manager").text
    assert http.post(endpoint, data={**form, "action": "remove"}, follow_redirects=False).status_code == 303
    assert repo.get_job(waiting)["status"] == "CANCELLED"
    assert repo.get_job(running)["status"] == "LEASED"
    assert repo.authenticate_dynamic_agent(agent_id=alice.agent_id, token=legacy.settings.token) is not None
    assert GCE_PIPELINE_TASK in repo.dynamic_agent_tasks(agent_id=bob.agent_id)
    for kind in ("op455", "op930"):
        modern.upload_gce_report(running, offer["lease_token"], kind, b"synthetic")
    assert modern.complete(running, offer["lease_token"], succeeded=True)["status"] == "SUCCEEDED"
    assert modern.claim(["system.ping", GCE_QUERY_TASK])["job"] is None
    assert modern.claim(["system.ping", GCE_QUERY_TASK])["job"] is None
    assert [event["action"] for event in repo.permission_audit()] == ["remove", "grant"]


def test_agent_handles_bad_network_restart_and_unauthorized_response(setup, monkeypatch):
    from modules.local_agent.executor import AgentProtocolError

    repo, _, ((alice, legacy), _) = setup
    modern = AgentClient(legacy.settings, session=legacy.session, agent_version="0.2.1")
    tasks = ["system.ping", GCE_QUERY_TASK]
    first = modern.claim(tasks)
    assert first["job"] is None and modern.permission_revision
    real_post = modern._post

    def offline(*_):
        raise AgentProtocolError("offline")

    monkeypatch.setattr(modern, "_post", offline)
    with pytest.raises(AgentProtocolError, match="offline"):
        modern.claim(tasks)
    monkeypatch.setattr(modern, "_post", real_post)
    assert modern.claim(tasks)["job"] is None
    restarted = AgentClient(legacy.settings, session=legacy.session, agent_version="0.2.1")
    assert restarted.permission_revision is None
    assert restarted.claim(tasks)["job"] is None
    assert restarted.claim(tasks)["job"] is None
    state = repo.permission_state(alice.agent_id, individual_permission_ceiling(load_server_settings()))
    assert state["version"] == "0.2.1" and state["synchronized"]
    assert GCE_QUERY_TASK in state["capabilities"] and GCE_QUERY_TASK not in state["permissions"]
    monkeypatch.setattr(restarted, "_post", lambda *_: {**first, "job": {"task": GCE_QUERY_TASK}})
    with pytest.raises(AgentProtocolError, match="permissões sincronizadas"):
        restarted.claim(tasks)


def test_claim_rechecks_permissions_after_concurrent_revocation(setup, monkeypatch):
    repo, http, ((_, _), (bob, agent)) = setup
    job_id = repo.enqueue(task=GCE_QUERY_TASK, payload={}, target_agent_id=bob.agent_id, max_attempts=1)
    identify = routes._agent

    def revoke_between_authentication_and_claim(settings, repository, agent_id, auth):
        result = identify(settings, repository, agent_id, auth)
        assert GCE_QUERY_TASK in result[1]
        ceiling = individual_permission_ceiling(settings)
        with ThreadPoolExecutor(max_workers=1) as thread:
            assert thread.submit(
                repo.change_permission, agent_id=bob.agent_id, identity_key="rod/bob",
                actor="rod/admin", task=GCE_QUERY_TASK, grant=False, ceiling=ceiling,
            ).result()
        with pytest.raises(RuntimeError, match="não tem permissão"):
            repo.enqueue(task=GCE_QUERY_TASK, payload={}, target_agent_id=bob.agent_id,
                         max_attempts=1, paired_ceiling=ceiling)
        return result

    monkeypatch.setattr(routes, "_agent", revoke_between_authentication_and_claim)
    response = http.post("/api/local-agent/v1/claim", headers=agent.headers, json={
        "capabilities": [GCE_QUERY_TASK],
    })
    assert response.status_code == 200 and response.json()["job"] is None
    assert (repo.get_job(job_id)["status"], repo.get_job(job_id)["attempts"]) == ("CANCELLED", 0)


def test_new_pairing_does_not_inherit_gce_from_old_pending_code(setup):
    repo, http, _ = setup
    code = repo.create_pairing(identity_key="rod/carol", tasks=frozenset({"system.ping", GCE_QUERY_TASK}),
                               created_by="old-server", ttl_seconds=120)
    response = http.post("/api/local-agent/v1/pair", json={"code": code, "machine_name": "carol-pc"})
    assert response.status_code == 201
    assert response.json()["tasks"] == ["system.ping"]


@pytest.mark.parametrize("op455_size", [46_689_234, 134_217_728], ids=["producao", "limite"])
def test_pipeline_adapter_reuses_one_session_and_transfers_only_reports(setup, tmp_path, monkeypatch, op455_size):
    repo, _, ((alice, alice_agent), (_, bob_agent)) = setup
    events = []

    class Transport:
        valid = True
        observed_unit = "CWB"

        def acquire_operation(self, _):
            return SimpleNamespace(release=lambda: events.append("release"))

        def close(self):
            pytest.fail("sessão emprestada foi fechada")

    transport = Transport()
    identity = browser_transport.BrowserIdentity.checked("ROD", "alice", alice.session_binding)
    monkeypatch.setattr(browser_transport, "_registry", {identity: transport})
    monkeypatch.setattr(SSWClient, "login", lambda *_: pytest.fail("segundo login"))
    monkeypatch.setattr(SSWClient, "open_menu", lambda *_: pytest.fail("segunda abertura de menu"))
    monkeypatch.setattr(
        "modules.documentos.config.DocumentosSettings.validate_ssw",
        lambda *_: pytest.fail("credenciais de serviço solicitadas"),
    )

    def stub_report(kind):
        def download(report, *, output_dir, **kwargs):
            assert report.client.session is transport
            assert kwargs["data_inicial"] == "201126"
            path = output_dir / f"{kind}.sswweb"
            if kind == "op455":
                with path.open("wb") as stream:
                    stream.truncate(op455_size)
            else:
                path.write_bytes(kind.encode())
            events.append(path)
            return path
        return download

    monkeypatch.setattr("operations.op455.report.OP455Report.gerar_e_baixar_documentos", stub_report("op455"))
    monkeypatch.setattr("operations.op930.report.OP930Report.gerar_e_baixar_por_cnpj", stub_report("op930"))

    # Formato persistido pelo agente anterior. O grant de GCE nunca existiu no disco ou na VPS.
    legacy = ["system.ping", "ssw.op001.nfd", "ssw.op001.transporte", "ssw.session.close"]
    assert alice_agent._post("/api/local-agent/v1/claim", {"capabilities": legacy})["job"] is None
    config_path = tmp_path / "config.json"
    protected = protect_token(alice_agent.settings.token) if os.name == "nt" else "stored-dpapi"
    save_config(config_path, {
        "agent_id": alice.agent_id, "server_url": "https://testserver",
        "token_dpapi": protected, "tasks": legacy,
    })
    before = config_path.read_bytes()
    if os.name != "nt":
        monkeypatch.setattr(windows_app, "unprotect_token", lambda blob: (
            alice_agent.settings.token if blob == protected else pytest.fail("DPAPI alterado")
        ))
    monkeypatch.setattr(windows_app, "default_config_path", lambda: config_path)
    monkeypatch.setattr(windows_app, "_proxy_session", lambda url: alice_agent.session)
    # A confirmação de configuração pode usar um browser previamente autenticado.
    monkeypatch.setattr(browser_transport, "browser_session_snapshot", lambda: [])
    with patch.dict(os.environ):
        windows_app._apply_config(load_config(config_path))
        runtime = windows_app.AgentRuntime()
    assert config_path.read_bytes() == before
    assert GCE_QUERY_TASK in runtime.executor.registry.capabilities()
    assert not runtime.executor.run_once()
    assert not runtime.executor.run_once()

    admin = SimpleNamespace(dominio="ROD", usuario="admin")
    request = SimpleNamespace(session={"local_agent_csrf": "csrf"})
    monkeypatch.setattr(routes, "is_admin", lambda _: True)
    monkeypatch.setattr(routes, "obter_client", lambda _: admin)
    assert routes.agent_manager_permission(
        request, alice.agent_id, "rod/alice", GCE_QUERY_TASK, "grant", "csrf",
    ).status_code == 303
    assert not runtime.executor.run_once()
    assert not runtime.executor.run_once()
    assert config_path.read_bytes() == before

    def run_agent(_seconds):
        assert bob_agent._post("/api/local-agent/v1/claim", {"capabilities": [GCE_QUERY_TASK]})["job"] is None
        job_id = repo.list_jobs()[0]["id"]
        job = repo.get_job(job_id)
        assert job["task"] == GCE_QUERY_TASK
        assert set(job["payload"]) == {
            "dominio", "usuario", "unidade", "session_binding", "period_start", "period_end",
            "cnpj", "group", "timeout",
        }
        assert runtime.executor.run_once()
        assert repo.get_job(job_id)["status"] == "SUCCEEDED"
        with pytest.raises(LookupError):
            repo.take_gce_reports(job_id=job_id, agent_id=bob_agent.settings.agent_id)
        events.append(job_id)

    monkeypatch.setattr(remote_ssw, "time", SimpleNamespace(monotonic=lambda: 0, sleep=run_agent))
    download = remote_ssw.remote_downloader(client=alice, root=tmp_path, execution_id="execution-1", progress=lambda *_: None)
    reports = download(tmp_path, date(2026, 11, 20), date(2026, 11, 26))
    assert reports.op455.stat().st_size == op455_size
    with reports.op455.open("rb") as report:
        digest = hashlib.file_digest(report, "sha256").hexdigest()
    assert reports.op930.read_bytes() == b"op930"
    assert events[2] == "release"
    assert all(not path.exists() for path in events[:2])
    assert browser_transport.require_browser_session(
        domain="ROD", user="alice", session_binding=alice.session_binding
    ) is transport
    with pytest.raises(LookupError):
        repo.take_gce_reports(job_id=events[3], agent_id=alice.agent_id)
    assert repo.get_job(events[3])["result"]["reports"] == {
        "op455": {"bytes": op455_size, "sha256": digest},
        "op930": {"bytes": 5, "sha256": hashlib.sha256(b"op930").hexdigest()},
    }


def test_upload_rejects_cross_access_invalid_digest_and_partial_completion(setup, monkeypatch):
    repo, http, ((alice, agent), (_, other)) = setup
    job_id = repo.enqueue(task=GCE_QUERY_TASK, payload={}, target_agent_id=alice.agent_id, max_attempts=1)
    job = repo.claim(agent_id=alice.agent_id, capabilities={GCE_QUERY_TASK}, lease_seconds=60)
    url = f"/api/local-agent/v1/gce-reports/{job_id}/op455"
    digest = hashlib.sha256(b"report").hexdigest()
    headers = {"X-Nexus-Lease": job["lease_token"], "X-Nexus-SHA256": digest}
    assert http.put(url, headers={**other.headers, **headers}, content=b"report").status_code == 409
    assert http.put(url, headers={**agent.headers, **headers}, content=b"corrupt").status_code == 422
    monkeypatch.setattr(routes, "GCE_MAX_REPORT_BYTES", 6)
    assert http.put(url, headers={**agent.headers, **headers}, content=b"too big").status_code == 413
    assert http.put(url, headers={**agent.headers, **headers}, content=b"report").status_code == 204
    assert http.put(url, headers={**agent.headers, **headers}, content=b"report").status_code == 204
    assert agent.complete(job_id, job["lease_token"], succeeded=True)["status"] == "FAILED"
    with repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM local_agent_gce_reports").fetchone()[0] == 0
    assert repo.claim(agent_id=alice.agent_id, capabilities={GCE_QUERY_TASK}, lease_seconds=60) is None


def test_upload_rejects_one_byte_above_128_mib(setup):
    repo, http, ((alice, agent), _) = setup
    job_id = repo.enqueue(task=GCE_QUERY_TASK, payload={}, target_agent_id=alice.agent_id, max_attempts=1)
    job = repo.claim(agent_id=alice.agent_id, capabilities={GCE_QUERY_TASK}, lease_seconds=60)
    url = f"/api/local-agent/v1/gce-reports/{job_id}/op455"
    headers = {**agent.headers, "X-Nexus-Lease": job["lease_token"], "X-Nexus-SHA256": "0" * 64}
    assert GCE_MAX_REPORT_BYTES == 134_217_728
    assert http.put(url, headers={**headers, "Content-Length": "134217729"}).status_code == 413

    def chunks():
        chunk = bytes(1024 * 1024)
        yield from (chunk for _ in range(128))
        yield b"x"

    assert http.put(url, headers=headers, content=chunks()).status_code == 413


@pytest.mark.parametrize(("case", "stage"), [
    ("vazio", "arquivo_vazio"),
    ("inexistente", "validar_caminho"),
    ("fora_da_raiz", "validar_caminho"),
    ("diretorio", "validar_caminho"),
    ("exato", "https_confirmado"),
    ("acima", "acima_do_limite"),
    ("valido", "https_confirmado"),
    ("erro_https", "transferir_https"),
])
def test_gce_post_download_diagnostics_preserve_the_borrowed_session(
    case, stage, tmp_path, monkeypatch, caplog,
):
    binding = "a" * 48
    released = []
    transport = SimpleNamespace(
        valid=True, observed_unit="CWB",
        acquire_operation=lambda _: SimpleNamespace(release=lambda: released.append(True)),
        close=lambda: pytest.fail("sessão emprestada fechada"),
        shutdown=lambda: pytest.fail("Chromium da sessão emprestada encerrado"),
    )
    identity = browser_transport.BrowserIdentity.checked("ROD", "alice", binding)
    monkeypatch.setattr(browser_transport, "_registry", {identity: transport})
    monkeypatch.setattr(SSWClient, "login", lambda *_: pytest.fail("novo login"))
    monkeypatch.setattr(SSWClient, "open_menu", lambda *_: pytest.fail("nova abertura de menu"))

    private_marker = "PRIVATE_REPORT_MARKER"
    outside = tmp_path / f"{private_marker}.sswweb"
    outside.write_bytes(b"confidential report")
    first_size = {
        "vazio": 0, "exato": GCE_MAX_REPORT_BYTES, "acima": GCE_MAX_REPORT_BYTES + 1,
    }.get(case, 4)

    def op455(report, *, output_dir, **_):
        assert report.client.session is transport
        if case == "fora_da_raiz":
            return outside
        path = output_dir / f"{private_marker}.sswweb"
        if case == "diretorio":
            path.mkdir()
        elif case != "inexistente":
            with path.open("wb") as stream:
                stream.truncate(first_size)
        return path

    def op930(report, *, output_dir, **_):
        assert report.client.session is transport
        path = output_dir / "op930.sswweb"
        path.write_bytes(b"op930")
        return path

    monkeypatch.setattr("operations.op455.report.OP455Report.gerar_e_baixar_documentos", op455)
    monkeypatch.setattr("operations.op930.report.OP930Report.gerar_e_baixar_por_cnpj", op930)
    transfers = []

    def put(url, *, headers, data, **_):
        assert headers["X-Nexus-SHA256"] == hashlib.sha256(data).hexdigest()
        transfers.append((url.rsplit("/", 1)[-1], len(data)))
        if case == "erro_https":
            raise ValueError(private_marker)
        return SimpleNamespace(status_code=204)

    agent = AgentClient(SimpleNamespace(
        server_url="https://test.invalid", agent_id="test-agent", token="test", request_timeout_seconds=5,
    ), session=SimpleNamespace(put=put))
    queue_logs = []
    context = ExecutionContext(
        lambda _, message: queue_logs.append(message),
        lambda kind, data: agent.upload_gce_report("job", "lease", kind, data),
    )
    payload = {
        "dominio": "ROD", "usuario": "alice", "unidade": "CWB", "session_binding": binding,
        "period_start": "2026-11-21", "period_end": "2026-11-27",
        "cnpj": "0" * 14, "group": "TESTE", "timeout": 1,
    }
    with caplog.at_level("INFO", logger="modules.local_agent.ssw_tasks.gce"):
        if case in {"valido", "exato"}:
            assert ssw_tasks._collect_gce_ssw(payload, context) == {"success": True}
            assert transfers == [("op455", first_size), ("op930", 5)]
            assert "relatorio=op930 etapa=https_confirmado" in caplog.text
        else:
            with pytest.raises(ValueError):
                ssw_tasks._collect_gce_ssw(payload, context)
            assert transfers == ([("op455", first_size)] if case == "erro_https" else [])

    assert "Relatório OP930 baixado com sucesso." in queue_logs[-1]
    assert all("tamanho_bytes" not in message for message in queue_logs)
    assert f"relatorio=op455 etapa={stage}" in caplog.text
    assert f"limite_bytes={GCE_MAX_REPORT_BYTES}" in caplog.text
    assert private_marker not in caplog.text and "confidential report" not in caplog.text
    if case == "fora_da_raiz":
        assert "dentro_da_raiz=False existe=None arquivo_regular=None tamanho_bytes=None" in caplog.text
    elif case in {"inexistente", "diretorio"}:
        assert f"dentro_da_raiz=True existe={case == 'diretorio'} arquivo_regular=False" in caplog.text
    else:
        assert f"dentro_da_raiz=True existe=True arquivo_regular=True tamanho_bytes={first_size}" in caplog.text
        assert f"bytes_lidos={min(first_size, GCE_MAX_REPORT_BYTES + 1)}" in caplog.text
    assert released == [True]
    assert browser_transport.require_browser_session(
        domain="ROD", user="alice", session_binding=binding,
    ) is transport


def test_expired_or_cross_session_cannot_start_browser(setup, monkeypatch):
    _, _, ((alice, _), _) = setup
    monkeypatch.setattr(browser_transport, "_registry", {})
    monkeypatch.setattr(browser_transport, "_BrowserWorker", lambda **_: pytest.fail("novo Chromium"))
    with pytest.raises(browser_transport.SSWBrowserAuthenticationError):
        ssw_tasks._collect_gce_ssw({
            **alice.identity_payload(), "period_start": "2026-11-20", "period_end": "2026-11-26",
            "cnpj": "05117268000806", "group": "WHIRLPOOL", "timeout": 1,
        }, ExecutionContext(lambda *_: None))


def test_central_pipeline_audits_failed_transfer_before_import(tmp_path, monkeypatch):
    from modules.documentos.services.daily_pipeline import DailyDocumentsPipeline

    events = []
    monkeypatch.setattr("modules.documentos.pipeline_repository.create_pipeline_run", lambda **_: 42)
    monkeypatch.setattr("modules.documentos.pipeline_repository.update_step", lambda *args: events.append(args))
    monkeypatch.setattr(
        "modules.documentos.pipeline_repository.fail_pipeline_run",
        lambda run_id, *, step, error: events.append((run_id, step, type(error).__name__)),
    )
    monkeypatch.setattr(
        "modules.documentos.collectors.portal.GCEPortalCollector.download_daily",
        lambda *_: events.append("portal central"),
    )
    monkeypatch.setattr(
        "modules.documentos.service.DocumentImportService.import_month",
        lambda **_: pytest.fail("importação após transferência incompleta"),
    )

    def failed_transfer(*_):
        raise RuntimeError("Transferência incompleta.")

    pipeline = DailyDocumentsPipeline(progress=lambda *_: None, ssw_download=failed_transfer)
    with pytest.raises(RuntimeError, match="Transferência incompleta"):
        pipeline.run(reference_date=date(2026, 11, 26), output_root=tmp_path)
    assert events == [
        (42, "portal", "RUNNING"), "portal central", (42, "portal", "SUCCESS"),
        (42, "ssw", "RUNNING"), (42, "ssw", "ERROR"), (42, "SSW", "RuntimeError"),
    ]


def test_sqlite_serializes_claims_and_closes_connections(setup):
    repo, _, ((alice, _), _) = setup
    job_id = repo.enqueue(task=GCE_QUERY_TASK, payload={}, target_agent_id=alice.agent_id, max_attempts=1)

    def claim(_):
        fresh = LocalAgentRepository(repo.database_path)
        return fresh.claim(agent_id=alice.agent_id, capabilities={GCE_QUERY_TASK}, lease_seconds=60)

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = [job for job in pool.map(claim, range(12)) if job]
    assert [job["id"] for job in claims] == [job_id]
    with repo._connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")


def test_sqlite_recovery_discards_an_expired_partial_upload_without_requeue(setup):
    repo, _, ((alice, agent), _) = setup
    now = utc_now()
    job_id = repo.enqueue(
        task=GCE_QUERY_TASK, payload={}, target_agent_id=alice.agent_id, max_attempts=1, now=now,
    )
    offer = repo.claim(agent_id=alice.agent_id, capabilities={GCE_QUERY_TASK}, lease_seconds=60, now=now)
    agent.upload_gce_report(job_id, offer["lease_token"], "op455", b"partial")
    assert repo.claim(
        agent_id=alice.agent_id, capabilities={GCE_QUERY_TASK}, lease_seconds=60,
        now=now + timedelta(seconds=61),
    ) is None
    job = repo.get_job(job_id)
    assert (job["status"], job["attempts"]) == ("FAILED", 1)
    with repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM local_agent_gce_reports").fetchone()[0] == 0


def test_full_gce_stays_central_and_waits_for_manual_lock(setup):
    repo, http, ((_, agent), _) = setup
    full = repo.enqueue(task=GCE_PIPELINE_TASK, payload={}, target_agent_id="windows-central")
    assert http.post("/api/local-agent/v1/claim", json={
        "capabilities": [GCE_PIPELINE_TASK],
    }, headers=agent.headers).status_code == 422
    for task in ("ssw.op001.nfd", "ssw.op001.transporte"):
        assert http.post("/api/local-agent/v1/claim", json={"capabilities": [task]}, headers=agent.headers).status_code == 200

    from modules.documentos.services.daily_pipeline import acquire_execution_lock
    headers = {"X-Nexus-Agent-Id": "windows-central", "Authorization": "Bearer " + "c" * 48}
    with acquire_execution_lock():
        response = http.post("/api/local-agent/v1/claim", json={"capabilities": [GCE_PIPELINE_TASK]}, headers=headers)
        assert response.json()["job"] is None
    response = http.post("/api/local-agent/v1/claim", json={"capabilities": [GCE_PIPELINE_TASK]}, headers=headers)
    assert response.json()["job"]["id"] == full
    assert repo.gce_pipeline_running()


@pytest.mark.parametrize("task", [ssw_tasks.TASK_OP001_NFD, ssw_tasks.TASK_OP001_TRANSPORTE])
def test_both_op001_handlers_keep_the_existing_contract(task, monkeypatch):
    import base64

    opened = []
    lease = SimpleNamespace(release=lambda: opened.append("release"))
    client = SimpleNamespace(acquire_operation=lambda name: lease)
    monkeypatch.setattr(ssw_tasks, "_authenticated_client", lambda clean: client)
    monkeypatch.setattr("operations.op001.coleta.OP001Coleta", lambda client: SimpleNamespace(
        open=lambda unidade: opened.append(unidade),
    ))

    def process(*, op001, input_file, output_file, job):
        assert input_file.read_bytes() == b"synthetic workbook"
        output_file.write_bytes(b"mocked result")
        return output_file

    def other(**_):
        pytest.fail("variante OP001 incorreta")

    nfd = task == ssw_tasks.TASK_OP001_NFD
    monkeypatch.setattr("operations.op001.batch.processar_planilha_nfd", process if nfd else other)
    monkeypatch.setattr("operations.op001.batch_transporte.processar_planilha_transporte", other if nfd else process)
    result = ssw_tasks._op001({
        "dominio": "ROD", "usuario": "alice", "unidade": "CWB", "session_binding": "a" * 48,
        "filename": "input.xlsx", "workbook_base64": base64.b64encode(b"synthetic workbook").decode(),
    }, ExecutionContext(lambda *_: None), task=task)
    assert result["filename"] == "processado_input.xlsx"
    assert base64.b64decode(result["workbook_base64"]) == b"mocked result"
    assert opened == ["CWB", "release"]
