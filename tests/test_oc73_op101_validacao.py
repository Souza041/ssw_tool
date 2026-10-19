from datetime import date
from inspect import Parameter, signature
from pathlib import Path

from operations.op101.ocorrencias import (
    OP101Ocorrencias,
    OcorrenciaHistorico,
)

import pytest


FIXTURE_OP101_SINTETICA = (
    Path(__file__).parent
    / "fixtures"
    / "op101"
    / "historico_oc73_sintetico.xml"
)


def test_parametros_de_seguranca_nao_possuem_default():
    parametros_envio = signature(
        OP101Ocorrencias.lancar_ocorrencia_73
    ).parameters
    parametros_confirmacao = signature(
        OP101Ocorrencias.confirmar_ocorrencia_73
    ).parameters

    assert parametros_envio["observacao"].default is Parameter.empty
    assert parametros_confirmacao["referencia"].default is Parameter.empty

def test_historico_sem_oc73():
    historico = [
        OcorrenciaHistorico(
            codigo="63",
            descricao="Ocorrencia anterior",
        ),
    ]

    assert OP101Ocorrencias.encontrar_ocorrencia(
        historico, 73
    ) is None


def test_historico_com_oc73_antiga():
    historico = [
        OcorrenciaHistorico(
            codigo="73",
            descricao="ENTREGA SERA REALIZADA AMANHA",
            data_hora="01/10/2026 10:30",
        ),
    ]

    encontrada = OP101Ocorrencias.encontrar_ocorrencia(
        historico, 73
    )

    # Documenta o comportamento atual:
    # uma OC73 antiga ainda é considerada existente.
    assert encontrada is not None
    assert encontrada.data_hora == "01/10/2026 10:30"


def test_parser_preserva_campos_do_historico():
    html = FIXTURE_OP101_SINTETICA.read_text(encoding="utf-8")

    ocorrencias = OP101Ocorrencias.listar_ocorrencias(
        html
    )

    assert len(ocorrencias) == 2

    antiga, referenciada = ocorrencias

    assert antiga.codigo == "73"
    assert antiga.data_hora == "14/10/2026 10:30"
    assert antiga.familia == "ROD"
    assert antiga.unidade == "CWB"
    assert antiga.usuario == "USUARIO_ANTIGO"
    assert antiga.complemento == "BOT OC73 LEGADO SEM REFERENCIA"
    assert antiga.ocorrencia_ssw == "73"

    assert referenciada.codigo == "73"
    assert referenciada.data_hora == "15/10/2026 09:45"
    assert referenciada.familia == "ROD"
    assert referenciada.unidade == "BIG"
    assert referenciada.usuario == "USUARIO_TESTE"
    assert referenciada.complemento == (
        "BOT OC73 | REF:OC73-A1B2C3D4E5F60718293A"
    )
    assert referenciada.ocorrencia_ssw == "73"

import pytest

from modules.ocorrencia_73.gate import PortaoOC73


REFERENCIA_TESTE = "OC73-A1B2C3D4E5F60718293A"


@pytest.mark.parametrize(
    "codigo,complemento,referencia,esperado",
    [
        (
            "73",
            f"BOT OCORRENCIA 73 | REF:{REFERENCIA_TESTE}",
            REFERENCIA_TESTE,
            True,
        ),
        (
            "73",
            "BOT OCORRENCIA 73 | REF:OC73-FFFFFFFFFFFFFFFFFFFF",
            REFERENCIA_TESTE,
            False,
        ),
        (
            "73",
            "OCORRENCIA ANTIGA",
            REFERENCIA_TESTE,
            False,
        ),
        (
            "73",
            f"REF:{REFERENCIA_TESTE}999",
            REFERENCIA_TESTE,
            False,
        ),
        (
            "63",
            f"REF:{REFERENCIA_TESTE}",
            REFERENCIA_TESTE,
            False,
        ),
        (
            "73",
            "",
            REFERENCIA_TESTE,
            False,
        ),
        (
            "73",
            "BOT OCORRENCIA 73 | REF:OC73-123",
            "OC73-123",
            False,
        ),
    ],
)
def test_confirmacao_por_referencia(
    codigo, complemento, referencia, esperado
):
    ocorrencia = OcorrenciaHistorico(
        codigo=codigo,
        descricao="TESTE",
        complemento=complemento,
    )

    assert PortaoOC73._confirmacao_valida(
        ocorrencia,
        referencia,
    ) is esperado


class ClienteSemRede:
    def get(self, *args, **kwargs):
        raise AssertionError("GET não autorizado neste teste")

    def post(self, *args, **kwargs):
        raise AssertionError("POST não autorizado neste teste")


@pytest.mark.parametrize(
    "observacao",
    [
        "",
        "A" * 71,
        "BOT OC73 SEM REFERENCIA",
        "BOT OC73 | REF:OC73-123",
        "BOT OC73 | REF:OC73-A1B2C3D4E5F60718293A999",
        (
            "BOT OC73 | REF:OC73-A1B2C3D4E5F60718293A "
            "REF:INVALIDA"
        ),
    ],
)
def test_lancamento_rejeita_observacao_invalida_sem_rede(
    observacao,
):
    op101 = OP101Ocorrencias(ClienteSemRede())

    with pytest.raises(ValueError):
        op101.lancar_ocorrencia_73(
            seq_ctrc="123456",
            familia="ROD",
            data_ocorrencia=date(2026, 10, 15),
            observacao=observacao,
        )
