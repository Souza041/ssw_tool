import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, select_autoescape
from openpyxl import Workbook, load_workbook

from modules.whirlpool.config import WhirlpoolSettings
from modules.whirlpool.excel import PlanilhaWhirlpoolInvalida, simular_planilha
from modules.whirlpool.portal import PortalWhirlpoolDesabilitado, WhirlpoolPortal
from modules.whirlpool.repository import WhirlpoolRepository
from modules.whirlpool.schemas import EstadoEnvio
from modules.whirlpool.service import (
    UploadWhirlpoolInvalido,
    WhirlpoolService,
    owner_hash,
)
from modules.whirlpool.smtp import EnvioWhirlpoolDesabilitado, WhirlpoolSMTP
from modules.whirlpool import routes as whirlpool_routes


BASE_HEADERS = [
    "OI", "Transportes", "COLETAS", "Chave", "plantas", "data de coleta",
    "NF", "Cliente", "Cidade", "UF", "SIGLA", "EMAIL ENVIADO",
]


def workbook_bytes(*, sheets=True, missing_column=None, sent=False, recipient=True):
    workbook = Workbook()
    base = workbook.active
    base.title = "BASE"
    headers = [item for item in BASE_HEADERS if item != missing_column]
    base.append(headers)
    values = {
        "OI": "OI-1", "Transportes": "T-100", "COLETAS": "C-1",
        "Chave": "CH-1", "plantas": "PLANTA", "data de coleta": "01/11/2026",
        "NF": "NF-1", "Cliente": "Cliente", "Cidade": "Cidade", "UF": "SP",
        "SIGLA": "ABC", "EMAIL ENVIADO": "SIM" if sent else "",
    }
    base.append([values[name] for name in headers])
    if sheets:
        email = workbook.create_sheet("email")
        email.append(["SIGLA", "E-MAIL"])
        email.append(["ABC", "destino@example.com" if recipient else ""])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def service(tmp_path) -> WhirlpoolService:
    settings = WhirlpoolSettings(data_dir=tmp_path / "private")
    return WhirlpoolService(settings=settings)


def render_results(transport_count: int, recipients_per_transport: int = 2) -> str:
    root = Path(__file__).resolve().parents[1]
    environment = Environment(
        loader=FileSystemLoader([
            root / "modules" / "whirlpool" / "templates",
            root / "web" / "templates",
        ]),
        autoescape=select_autoescape(["html", "xml"]),
    )
    summaries = []
    for index in range(transport_count):
        summaries.append({
            "transporte": f"T-{index:04d}",
            "destinatarios": [
                f"destino-{index}-{recipient}@example.com"
                for recipient in range(recipients_per_transport)
            ],
            "total_linhas": 1,
            "pendentes": 1 if index % 3 == 0 else 0,
            "ja_enviados": 1 if index % 3 == 1 else 0,
            "erros": 1 if index % 3 == 2 else 0,
        })
    execution = {
        "id": "a" * 32,
        "status": "SIMULADO",
        "progresso": transport_count,
        "total": transport_count,
        "result_path": "private/result.xlsx",
        "erro": None,
        "resumo": {
            "total_transportes": transport_count,
            "resumo_transportes": summaries,
        },
    }
    return environment.get_template("whirlpool_emails.html").render(
        request=SimpleNamespace(
            session={"ssw_session_id": "test"},
            url=SimpleNamespace(path="/whirlpool/emails"),
        ),
        csrf_token="csrf-test",
        execucao=execution,
        historico=[],
    )


def test_simulacao_valida_cria_resultado_privado(tmp_path):
    result = service(tmp_path).simular(
        workbook_bytes(), "whirlpool.xlsx", "owner-a",
    )
    assert result["status"] == "SIMULADO"
    assert result["envio_real"] is False
    assert result["resumo"]["total_transportes"] == 1
    assert result["resumo"]["operacoes"][0]["estado"] == "PENDENTE"
    output = Path(result["result_path"])
    assert output.is_file()
    assert "downloads" not in {part.casefold() for part in output.parts}
    workbook = load_workbook(output, read_only=True)
    assert "STATUS WHIRLPOOL" in [cell.value for cell in workbook["BASE"][1]]
    workbook.close()


@pytest.mark.parametrize(
    ("content", "filename"),
    [(b"", "arquivo.xlsx"), (b"nao-e-zip", "arquivo.xlsx"), (b"PKx", "arquivo.xls")],
)
def test_upload_invalido_e_rejeitado_antes_do_processamento(tmp_path, content, filename):
    with pytest.raises(UploadWhirlpoolInvalido):
        service(tmp_path).simular(content, filename, "owner-a")


def test_xlsx_inflado_e_rejeitado_antes_do_openpyxl(tmp_path):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/worksheets/sheet1.xml", b"x" * 10_001)
    settings = WhirlpoolSettings(
        data_dir=tmp_path / "private", max_upload_bytes=1_000,
    )
    with pytest.raises(UploadWhirlpoolInvalido, match="limites seguros"):
        WhirlpoolService(settings=settings).simular(
            output.getvalue(), "inflado.xlsx", "owner-a",
        )


def test_aba_obrigatoria_ausente(tmp_path):
    with pytest.raises(UploadWhirlpoolInvalido, match="Abas obrigatórias"):
        service(tmp_path).simular(workbook_bytes(sheets=False), "base.xlsx", "owner-a")


def test_coluna_obrigatoria_ausente(tmp_path):
    with pytest.raises(UploadWhirlpoolInvalido, match="Transportes"):
        service(tmp_path).simular(
            workbook_bytes(missing_column="Transportes"), "base.xlsx", "owner-a",
        )


def test_destinatario_ausente_fica_em_erro_sem_envio(tmp_path):
    result = service(tmp_path).simular(
        workbook_bytes(recipient=False), "base.xlsx", "owner-a",
    )
    operation = result["resumo"]["operacoes"][0]
    assert operation["estado"] == EstadoEnvio.ERRO.value
    assert operation["destinatarios"] == []


def test_transporte_ja_enviado_nao_volta_a_pendente(tmp_path):
    result = service(tmp_path).simular(
        workbook_bytes(sent=True), "base.xlsx", "owner-a",
    )
    assert result["resumo"]["operacoes"][0]["estado"] == EstadoEnvio.ENVIADO.value


def test_resultado_exige_mesmo_owner(tmp_path):
    svc = service(tmp_path)
    result = svc.simular(workbook_bytes(), "base.xlsx", "owner-a")
    assert svc.resultado_do_usuario(result["id"], "owner-b") is None
    assert svc.resultado_do_usuario(result["id"], "owner-a").is_file()


def test_reserva_idempotente_concorrente_tem_um_vencedor(tmp_path):
    repository = WhirlpoolRepository(tmp_path / "private")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda run: repository.reservar_envio("chave-unica", run),
            ("run-a", "run-b"),
        ))
    assert sorted(results) == [False, True]


def test_resultado_inconclusivo_bloqueia_nova_reserva(tmp_path):
    repository = WhirlpoolRepository(tmp_path / "private")
    assert repository.reservar_envio("chave-incerta", "run-a") is True
    repository.atualizar_estado_envio(
        "chave-incerta", EstadoEnvio.VERIFICAR_ENVIO,
    )
    assert repository.reservar_envio("chave-incerta", "run-b") is False


def test_smtp_e_portal_permanecem_bloqueados():
    with pytest.raises(EnvioWhirlpoolDesabilitado):
        WhirlpoolSMTP().enviar()
    with pytest.raises(PortalWhirlpoolDesabilitado):
        WhirlpoolPortal().baixar_documentos()


def test_auditoria_nao_contem_conteudo_da_planilha(tmp_path):
    result = service(tmp_path).simular(workbook_bytes(), "base.xlsx", "owner-a")
    audit = (tmp_path / "private" / "runs" / f"{result['id']}.json").read_text(
        encoding="utf-8"
    )
    assert "Cliente" not in audit
    assert "CH-1" not in audit
    assert result["owner_hash"] == "owner-a"


def test_pagina_e_download_exigem_autenticacao(tmp_path, monkeypatch):
    request = SimpleNamespace(session={})
    monkeypatch.setattr(whirlpool_routes, "obter_client", lambda request: None)
    response = whirlpool_routes.emails_page(request)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    with pytest.raises(HTTPException) as error:
        whirlpool_routes.download_resultado(request, "inexistente")
    assert error.value.status_code == 401


def test_fluxo_http_simula_e_entrega_resultado_autenticado(tmp_path, monkeypatch):
    svc = service(tmp_path)
    session_id = "sessao-testes"
    record = svc.simular(
        workbook_bytes(), "whirlpool.xlsx", owner_hash(session_id),
    )
    request = SimpleNamespace(session={"ssw_session_id": session_id})
    monkeypatch.setattr(whirlpool_routes, "obter_client", lambda request: object())
    monkeypatch.setattr(whirlpool_routes, "_service", lambda: svc)

    response = whirlpool_routes.download_resultado(request, record["id"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert Path(response.path).read_bytes().startswith(b"PK")


@pytest.mark.parametrize("transport_count", [1, 10, 100, 1_000])
def test_interface_renderiza_volumes_com_paginacao(transport_count):
    html = render_results(transport_count)
    assert html.count('class="whp-result-row"') == transport_count
    assert 'aria-label="Paginação dos transportes"' in html
    assert 'id="whp-page-previous"' in html
    assert 'id="whp-page-next"' in html
    assert "Math.ceil(rows.length / pageSize)" in html


def test_interface_resume_destinatarios_sem_exibi_los_inicialmente():
    html = render_results(1, recipients_per_transport=13)
    assert "13 destinatários" in html
    assert "<details>" in html
    assert "<details open" not in html
    assert "destino-0-0@example.com" in html


def test_interface_tem_cards_colunas_badges_e_download_destacado():
    html = render_results(3)
    for label in (
        "Total de transportes", "Linhas pendentes", "Já enviados", "Erros",
        "Transporte", "Destinatários", "Situação na planilha",
    ):
        assert label in html
    assert "Pendente na simulação" in html
    assert "Já constava enviado" in html
    assert "Com erros" in html
    assert 'class="whp-download"' in html
    assert 'scope="col"' in html
    assert 'aria-live="polite"' in html
