from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from modules.ocorrencia_73.service import Ocorrencia73Service


DATA = date(2026, 10, 12)


def item(ctrc):
    return {
        "serie": "CWB",
        "numero": ctrc,
        "digito": "0",
        "ctrc_original": f"CWB{ctrc}-0",
        "fluxo": "CWB",
    }


def preparacao(itens):
    return {
        "lotes": [
            {
                "fluxo": "CWB",
                "arquivo": "cwb.sswweb",
                "data_inicial": "2026-10-12",
                "data_final": "2026-10-12",
                "total_relatorio": len(itens),
                "total_filtrado": len(itens),
            }
        ],
        "total_relatorio": len(itens),
        "total_filtrado": len(itens),
        "filtrados": itens,
    }


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    service = Ocorrencia73Service()
    service.base_output_dir = tmp_path

    client = MagicMock()
    op101 = MagicMock()

    monkeypatch.setattr(
        service,
        "criar_client_logado",
        MagicMock(return_value=client),
    )

    with (
        patch("modules.ocorrencia_73.service.OP455Report"),
        patch(
            "modules.ocorrencia_73.service.OP101Ocorrencias",
            return_value=op101,
        ),
        patch(
            "modules.ocorrencia_73.service.DRY_RUN",
            True,
        ),
    ):
        yield service, op101


def configurar_op101(op101, ocorrencia_existente=None):
    consulta = SimpleNamespace(
        encontrado=True,
        seq_ctrc="123",
        local="CWB",
        familia="1",
        to_dict=lambda: {"encontrado": True},
    )

    op101.consultar_ctrc.return_value = consulta
    op101.abrir_ocorrencias.return_value = "<html></html>"
    op101.listar_ocorrencias.return_value = []
    op101.encontrar_ocorrencia.return_value = (
        ocorrencia_existente
    )


def test_sem_elegiveis(ambiente, monkeypatch):
    service, op101 = ambiente

    monkeypatch.setattr(
        service,
        "preparar_lotes_oc73",
        lambda **kwargs: preparacao([]),
    )

    resultado = service.executar(DATA)

    assert resultado["status"] == "sem_elegiveis"
    assert resultado["total_filtrado"] == 0
    op101.consultar_ctrc.assert_not_called()
    op101.lancar_ocorrencia_73.assert_not_called()


def test_oc73_existente_nao_lanca(ambiente, monkeypatch):
    service, op101 = ambiente

    monkeypatch.setattr(
        service,
        "preparar_lotes_oc73",
        lambda **kwargs: preparacao([item("100001")]),
    )

    ocorrencia = SimpleNamespace(
        to_dict=lambda: {"codigo": 73}
    )
    configurar_op101(op101, ocorrencia)

    resultado = service.executar(DATA)

    assert resultado["total_ja_existia"] == 1
    assert resultado["itens"][0]["status"] == "ja_existia"
    op101.lancar_ocorrencia_73.assert_not_called()


def test_dry_run_nao_lanca(ambiente, monkeypatch):
    service, op101 = ambiente

    monkeypatch.setattr(
        service,
        "preparar_lotes_oc73",
        lambda **kwargs: preparacao([item("100002")]),
    )

    configurar_op101(op101)

    resultado = service.executar(DATA)

    assert resultado["total_pendente_lancamento"] == 1
    assert resultado["total_lancado"] == 0
    op101.lancar_ocorrencia_73.assert_not_called()


def test_sabado_nao_faz_login(ambiente):
    service, op101 = ambiente

    resultado = service.executar(date(2026, 10, 10))

    assert resultado["status"] == "sem_execucao_programada"
    service.criar_client_logado.assert_not_called()
    op101.consultar_ctrc.assert_not_called()


def test_falha_preparacao_interrompe(ambiente, monkeypatch):
    service, op101 = ambiente

    def falhar(**kwargs):
        raise RuntimeError("Falha simulada no BIG")

    monkeypatch.setattr(
        service,
        "preparar_lotes_oc73",
        falhar,
    )

    with pytest.raises(
        RuntimeError,
        match="Falha simulada no BIG",
    ):
        service.executar(DATA)

    op101.consultar_ctrc.assert_not_called()
    op101.lancar_ocorrencia_73.assert_not_called()

def test_limite_global_entre_cwb_e_big(
    ambiente,
    monkeypatch,
):
    service, op101 = ambiente

    itens = [
        {**item("100001"), "fluxo": "CWB"},
        {**item("100002"), "fluxo": "BIG"},
        {**item("100003"), "fluxo": "BIG"},
    ]

    monkeypatch.setattr(
        service,
        "preparar_lotes_oc73",
        lambda **kwargs: preparacao(itens),
    )

    configurar_op101(op101)

    monkeypatch.setattr(
        "modules.ocorrencia_73.service.DRY_RUN",
        False,
    )
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.MAX_LANCAMENTOS",
        1,
    )
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.CTRC_TESTE",
        "",
    )

    op101.lancar_ocorrencia_73.return_value = (
        SimpleNamespace(
            success=True,
            to_dict=lambda: {"success": True},
        )
    )

    op101.confirmar_ocorrencia_73.return_value = (
        SimpleNamespace(
            success=True,
            to_dict=lambda: {"success": True},
        )
    )

    resultado = service.executar(DATA)

    assert op101.lancar_ocorrencia_73.call_count == 1
    assert resultado["total_lancado"] == 1
    assert resultado["total_ignorado_limite"] == 2

def test_lancamento_confirmado(ambiente, monkeypatch):
    service, op101 = ambiente

    monkeypatch.setattr(
        service,
        "preparar_lotes_oc73",
        lambda **kwargs: preparacao([item("100010")]),
    )

    configurar_op101(op101)

    monkeypatch.setattr(
        "modules.ocorrencia_73.service.DRY_RUN",
        False,
    )
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.MAX_LANCAMENTOS",
        1,
    )
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.CTRC_TESTE",
        "",
    )

    op101.lancar_ocorrencia_73.return_value = (
        SimpleNamespace(
            success=True,
            to_dict=lambda: {"success": True},
        )
    )

    op101.confirmar_ocorrencia_73.return_value = (
        SimpleNamespace(
            codigo="73",
            to_dict=lambda: {"codigo": "73"},
        )
    )

    resultado = service.executar(DATA)

    assert resultado["total_lancado"] == 1
    assert resultado["itens"][0]["status"] == "lancada"


def test_lancamento_sem_confirmacao(ambiente, monkeypatch):
    service, op101 = ambiente

    monkeypatch.setattr(
        service,
        "preparar_lotes_oc73",
        lambda **kwargs: preparacao([item("100011")]),
    )

    configurar_op101(op101)

    monkeypatch.setattr(
        "modules.ocorrencia_73.service.DRY_RUN",
        False,
    )
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.MAX_LANCAMENTOS",
        1,
    )
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.CTRC_TESTE",
        "",
    )

    op101.lancar_ocorrencia_73.return_value = (
        SimpleNamespace(
            success=True,
            to_dict=lambda: {"success": True},
        )
    )

    op101.confirmar_ocorrencia_73.return_value = None

    resultado = service.executar(DATA)

    assert resultado["total_lancado"] == 0
    assert resultado["total_erro_lancamento"] == 1
    assert resultado["itens"][0]["status"] == "erro_lancamento"


def test_ssw_rejeitou_lancamento(ambiente, monkeypatch):
    service, op101 = ambiente

    monkeypatch.setattr(
        service,
        "preparar_lotes_oc73",
        lambda **kwargs: preparacao([item("100012")]),
    )

    configurar_op101(op101)

    monkeypatch.setattr(
        "modules.ocorrencia_73.service.DRY_RUN",
        False,
    )
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.MAX_LANCAMENTOS",
        1,
    )
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.CTRC_TESTE",
        "",
    )

    op101.lancar_ocorrencia_73.return_value = (
        SimpleNamespace(
            success=False,
            to_dict=lambda: {"success": False},
        )
    )

    resultado = service.executar(DATA)

    assert resultado["total_lancado"] == 0
    assert resultado["total_erro_lancamento"] == 1
    op101.confirmar_ocorrencia_73.assert_not_called()