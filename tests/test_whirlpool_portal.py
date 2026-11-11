from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import ast
import hashlib
import time

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from modules.whirlpool.config import WhirlpoolPortalSettings, WhirlpoolSettings
from modules.whirlpool.portal import (
    ConcorrenciaPortalBloqueada,
    DocumentoIndisponivel,
    DownloadPortalFalhou,
    PlaywrightPortalSession,
    PortalAutenticacaoRecusada,
    PortalTimeout,
    PortalTransportLocks,
    ResultadoTransportePortal,
    TransporteNaoEncontrado,
    TransportePortal,
    WhirlpoolPortal,
    _safe_component,
)
from modules.whirlpool.portal_service import WhirlpoolPortalDryRunService
from modules.whirlpool.portal_jobs import (
    PORTAL_JOBS,
    PortalJobRun,
    consumir_logs,
    executar_portal_job,
)
from modules.whirlpool.repository import WhirlpoolRepository
from modules.whirlpool.service import WhirlpoolService, owner_hash
from modules.whirlpool import routes as whirlpool_routes
from web.jobs import Job


def portal_settings(**overrides):
    values = {
        "enabled": True,
        "dry_run": True,
        "first_url": "https://portal.example/login-1",
        "second_url": "https://portal.example/login-2",
        "first_user": "user-one",
        "first_password": "secret-one",
        "second_user": "user-two",
        "second_password": "secret-two",
        "authorized_users": frozenset({"operador"}),
        "allowed_hosts": frozenset({"portal.example"}),
        "max_transports": 10,
    }
    values.update(overrides)
    return WhirlpoolPortalSettings(**values)


class FakeSession:
    behavior = "success"
    instances = []

    def __init__(self, settings, log):
        self.settings = settings
        self.log = log
        self.closed = False
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def autenticar(self):
        if self.behavior == "login_denied":
            raise PortalAutenticacaoRecusada("Autenticação recusada.")
        if self.behavior == "timeout":
            raise PortalTimeout("Timeout durante autenticação.")

    def consultar_e_baixar(self, item, output_dir):
        errors = {
            "not_found": TransporteNaoEncontrado("Transporte inexistente."),
            "unavailable": DocumentoIndisponivel("Documento indisponível."),
            "download_error": DownloadPortalFalhou("Falha durante download."),
        }
        if self.behavior in errors:
            raise errors[self.behavior]
        output = output_dir / f"documento_{item.transporte}.pdf"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4\nmock")
        return [output]


def session_factory(behavior):
    class Session(FakeSession):
        pass
    Session.behavior = behavior
    Session.instances = []
    return Session


def test_login_bem_sucedido_e_download_concluido(tmp_path):
    factory = session_factory("success")
    portal = WhirlpoolPortal(portal_settings(), session_factory=factory)
    results = portal.baixar_documentos(
        [TransportePortal("PLANTA", "TR-1")], tmp_path,
    )
    assert results[0].status == "DOCUMENTOS_BAIXADOS"
    assert results[0].documentos[0].read_bytes().startswith(b"%PDF-")
    assert factory.instances[0].closed is True


@pytest.mark.parametrize(
    ("behavior", "exception"),
    [("login_denied", PortalAutenticacaoRecusada), ("timeout", PortalTimeout)],
)
def test_falha_de_login_ou_timeout_fecha_sessao(tmp_path, behavior, exception):
    factory = session_factory(behavior)
    portal = WhirlpoolPortal(portal_settings(), session_factory=factory)
    with pytest.raises(exception):
        portal.baixar_documentos(
            [TransportePortal("PLANTA", "TR-1")], tmp_path,
        )
    assert factory.instances[0].closed is True


@pytest.mark.parametrize(
    ("behavior", "message"),
    [
        ("not_found", "Transporte inexistente"),
        ("unavailable", "Documento indisponível"),
        ("download_error", "Falha durante download"),
    ],
)
def test_falhas_de_consulta_sao_registradas_sem_retentativa(
    tmp_path, behavior, message,
):
    factory = session_factory(behavior)
    result = WhirlpoolPortal(
        portal_settings(), session_factory=factory,
    ).baixar_documentos([TransportePortal("PLANTA", "TR-1")], tmp_path)[0]
    assert result.status == "ERRO"
    assert message in result.erro
    assert len(factory.instances) == 1
    assert factory.instances[0].closed is True


def test_trava_impede_mesmo_transporte_em_execucoes_concorrentes(tmp_path):
    first = PortalTransportLocks(tmp_path, ["planta:tr-1"])
    with first:
        with pytest.raises(ConcorrenciaPortalBloqueada):
            with PortalTransportLocks(tmp_path, ["planta:tr-1"]):
                pass


def test_nome_de_transporte_nao_permite_path_traversal():
    assert _safe_component("../../TR 123") == "TR_123"
    with pytest.raises(ValueError):
        _safe_component("../..")


def test_segredos_nao_aparecem_na_representacao_da_configuracao():
    representation = repr(portal_settings())
    assert "secret-one" not in representation
    assert "secret-two" not in representation
    assert "user-one" not in representation
    assert "portal.example" not in representation


def test_configuracao_recusa_url_sem_https():
    settings = portal_settings(first_url="http://portal.example/login")
    with pytest.raises(ValueError, match="HTTPS"):
        settings.validar_homologacao("operador")


class FakeResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self.ok = 200 <= status < 300
        self._body = body
        self.body_called = False
        self.disposed = False

    def body(self):
        self.body_called = True
        return self._body

    def dispose(self):
        self.disposed = True


class FakeRequestContext:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def download_session(responses):
    session = PlaywrightPortalSession(
        portal_settings(), lambda _message: None,
    )
    request = FakeRequestContext(responses)
    session.context = SimpleNamespace(request=request)
    return session, request


def test_download_valida_cada_redirecionamento():
    redirect = FakeResponse(302, {"location": "/pdf/final.pdf"})
    final = FakeResponse(200, {"content-length": "13"}, b"%PDF-1.4\nabc")
    session, request = download_session([redirect, final])
    body = session._obter_pdf(
        "https://portal.example/start", TransportePortal("P", "TR-1"),
    )
    assert body.startswith(b"%PDF-")
    assert [call[0] for call in request.calls] == [
        "https://portal.example/start",
        "https://portal.example/pdf/final.pdf",
    ]
    assert all(call[1]["max_redirects"] == 0 for call in request.calls)
    assert redirect.disposed and final.disposed


def test_download_recusa_redirecionamento_para_host_nao_permitido():
    redirect = FakeResponse(302, {"location": "https://evil.example/file.pdf"})
    session, request = download_session([redirect])
    with pytest.raises(DownloadPortalFalhou, match="não autorizada"):
        session._obter_pdf(
            "https://portal.example/start", TransportePortal("P", "TR-1"),
        )
    assert len(request.calls) == 1
    assert redirect.disposed is True


def test_download_recusa_content_length_antes_de_carregar_corpo():
    oversized = FakeResponse(200, {"content-length": str(30 * 1024 * 1024)})
    session, _request = download_session([oversized])
    with pytest.raises(DownloadPortalFalhou, match="excede o limite"):
        session._obter_pdf(
            "https://portal.example/file.pdf", TransportePortal("P", "TR-1"),
        )
    assert oversized.body_called is False


class FakePage:
    def set_default_timeout(self, _value):
        pass

    def set_default_navigation_timeout(self, _value):
        pass


class FakeContext:
    def __init__(self):
        self.closed = False

    def new_page(self):
        return FakePage()

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()
        self.closed = False

    def new_context(self, **_kwargs):
        return self.context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self):
        self.browser = FakeBrowser()

    def launch(self, **_kwargs):
        return self.browser


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()


class FakeManager:
    def __init__(self):
        self.playwright = FakePlaywright()
        self.stopped = False

    def start(self):
        return self.playwright

    def stop(self):
        self.stopped = True


def test_playwright_fecha_contexto_browser_e_manager():
    manager = FakeManager()
    with PlaywrightPortalSession(
        portal_settings(), lambda _message: None, lambda: manager,
    ):
        pass
    browser = manager.playwright.chromium.browser
    assert browser.context.closed is True
    assert browser.closed is True
    assert manager.stopped is True


def workbook_bytes(transport_count=1):
    workbook = Workbook()
    base = workbook.active
    base.title = "BASE"
    base.append([
        "OI", "Transportes", "COLETAS", "Chave", "plantas",
        "data de coleta", "NF", "Cliente", "Cidade", "UF", "SIGLA",
    ])
    for index in range(transport_count):
        base.append([
            f"OI-{index}", f"TR-{index}", f"C-{index}", f"CH-{index}",
            "PLANTA", "01/11/2026", f"NF-{index}", "Cliente", "Cidade",
            "SP", "ABC",
        ])
    email = workbook.create_sheet("email")
    email.append(["SIGLA", "E-MAIL"])
    email.append(["ABC", "destino@example.com"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class FakePortal:
    def baixar_documentos(self, items, output_dir, log, progress):
        results = []
        progress(0, len(items))
        for item in items:
            path = output_dir / f"documento_{item.transporte}.pdf"
            path.write_bytes(b"%PDF-1.4\nmock")
            log(f"Transporte {item.transporte}: documento baixado.")
            results.append(ResultadoTransportePortal(
                item.planta, item.transporte, "DOCUMENTOS_BAIXADOS", [path],
            ))
            progress(len(results), len(items))
        return results


def test_portal_dry_run_persiste_auditoria_sem_estado_enviado(tmp_path, monkeypatch):
    settings = WhirlpoolSettings(data_dir=tmp_path / "private")
    simulation = WhirlpoolService(settings=settings).simular(
        workbook_bytes(), "whirlpool.xlsx", "owner-a",
    )
    repository = WhirlpoolRepository(settings.data_dir)
    service = WhirlpoolPortalDryRunService(
        settings=settings,
        portal_settings=portal_settings(),
        repository=repository,
        portal=FakePortal(),
    )
    input_path = Path(simulation["input_path"])
    input_hash_before = hashlib.sha256(input_path.read_bytes()).hexdigest()

    def smtp_forbidden(*_args, **_kwargs):
        raise AssertionError("SMTP não pode ser chamado no PORTAL_DRY_RUN")

    monkeypatch.setattr(
        "modules.whirlpool.smtp.WhirlpoolSMTP.enviar", smtp_forbidden,
    )
    results = service.executar(
        simulation["id"], "owner-a", "OPERADOR", "job-1", True,
    )
    record = repository.obter_execucao(simulation["id"])
    audit = record["portal_homologacoes"][0]
    assert results[0].documentos[0].is_file()
    assert record["status"] == "SIMULADO"
    assert audit["modo"] == "PORTAL_DRY_RUN"
    assert audit["smtp_executado"] is False
    assert audit["email_enviado_alterado"] is False
    assert audit["status_envio_alterado"] is False
    assert hashlib.sha256(input_path.read_bytes()).hexdigest() == input_hash_before


def test_limite_de_transportes_e_aplicado_antes_do_portal(tmp_path):
    settings = WhirlpoolSettings(data_dir=tmp_path / "private")
    simulation = WhirlpoolService(settings=settings).simular(
        workbook_bytes(2), "whirlpool.xlsx", "owner-a",
    )
    repository = WhirlpoolRepository(settings.data_dir)
    service = WhirlpoolPortalDryRunService(
        settings=settings,
        portal_settings=portal_settings(max_transports=1),
        repository=repository,
        portal=FakePortal(),
    )
    with pytest.raises(RuntimeError, match="excede o limite"):
        service.executar(
            simulation["id"], "owner-a", "OPERADOR", "job-limit", True,
        )
    assert not (settings.data_dir / "files" / simulation["id"] / "portal").exists()


def test_rota_recusa_usuario_fora_da_allowlist(monkeypatch):
    session_id = "session-a"
    request = SimpleNamespace(session={
        "ssw_session_id": session_id,
        "whirlpool_csrf": "csrf-test",
    })
    client = SimpleNamespace(usuario="NAO-AUTORIZADO")
    monkeypatch.setattr(whirlpool_routes, "obter_client", lambda _request: client)
    monkeypatch.setattr(
        whirlpool_routes,
        "_service",
        lambda: SimpleNamespace(
            obter_do_usuario=lambda run_id, owner: {"id": run_id}
            if owner == owner_hash(session_id) else None,
        ),
    )
    env = {
        "WHIRLPOOL_PORTAL_ENABLED": "true",
        "WHIRLPOOL_PORTAL_DRY_RUN": "true",
        "WHIRLPOOL_PORTAL_AUTHORIZED_USERS": "OPERADOR",
        "WHIRLPOOL_PORTAL_URL_1": "https://portal.example/one",
        "WHIRLPOOL_PORTAL_URL_2": "https://portal.example/two",
        "WHIRLPOOL_PORTAL_USER_1": "user-one",
        "WHIRLPOOL_PORTAL_PASSWORD_1": "password-one",
        "WHIRLPOOL_PORTAL_USER_2": "user-two",
        "WHIRLPOOL_PORTAL_PASSWORD_2": "password-two",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(HTTPException) as error:
        whirlpool_routes.iniciar_portal_dry_run(
            request, "run-a", "csrf-test", "HOMOLOGAR PORTAL SEM ENVIO",
        )
    assert error.value.status_code == 403


def test_download_portal_exige_dono_da_sessao(monkeypatch):
    session_id = "session-current"
    request = SimpleNamespace(session={"ssw_session_id": session_id})
    monkeypatch.setattr(
        whirlpool_routes, "obter_client", lambda _request: object(),
    )
    portal_job = PortalJobRun(
        owner_hash=owner_hash("another-session"),
        run_id="run-a",
        job=Job(id="job-protected"),
    )
    PORTAL_JOBS[portal_job.job.id] = portal_job
    try:
        with pytest.raises(HTTPException) as error:
            whirlpool_routes.download_documento_portal(
                request, portal_job.job.id, 0,
            )
        assert error.value.status_code == 404
    finally:
        PORTAL_JOBS.pop(portal_job.job.id, None)


def test_download_portal_recusa_caminho_fora_da_arvore_privada(
    tmp_path, monkeypatch,
):
    session_id = "session-owner"
    owner = owner_hash(session_id)
    request = SimpleNamespace(session={"ssw_session_id": session_id})
    monkeypatch.setattr(
        whirlpool_routes, "obter_client", lambda _request: object(),
    )
    settings = WhirlpoolSettings(data_dir=tmp_path / "private")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\nmock")
    job = Job(id="job-path-check")
    job.result_files = [{
        "name": "outside.pdf", "path": str(outside), "transporte": "TR-1",
    }]
    run = PortalJobRun(owner, "run-a", job)
    PORTAL_JOBS[job.id] = run
    monkeypatch.setattr(
        whirlpool_routes, "_service",
        lambda: SimpleNamespace(settings=settings),
    )
    try:
        with pytest.raises(HTTPException) as error:
            whirlpool_routes.download_documento_portal(request, job.id, 0)
        assert error.value.status_code == 404
    finally:
        PORTAL_JOBS.pop(job.id, None)


def test_job_sanitiza_excecao_inesperada_com_caminho_e_segredo():
    class FailingService:
        def executar(self, **_kwargs):
            raise OSError(r"C:\private\secret-password\arquivo.pdf")

    run = PortalJobRun("owner", "run-a", Job(id="job-safe-error"))
    executar_portal_job(run, "operador", service=FailingService())
    deadline = time.monotonic() + 2
    while run.job.status == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    logs = "\n".join(consumir_logs(run))
    assert run.job.status == "error"
    assert run.job.error == (
        "Homologação Whirlpool interrompida; nenhum e-mail foi enviado."
    )
    assert "secret-password" not in logs
    assert "C:\\private" not in logs


def test_fluxo_portal_nao_importa_nem_chama_smtp():
    root = Path(__file__).resolve().parents[1] / "modules" / "whirlpool"
    files = (
        root / "portal.py",
        root / "portal_service.py",
        root / "portal_jobs.py",
    )
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not any("smtp" in name.casefold() for name in imported)
        assert not ({"enviar", "marcar_email_ok"} & calls)
