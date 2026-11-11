from operations.debitos.common import (
    competencia_ssw,
    data_ssw,
    somente_digitos,
    valor_ssw,
)
from operations.debitos.op506 import OP506Indenizacao
from operations.debitos.batch import normalizar_cnpj

from operations.debitos.op475 import OP475Despesas

def test_parse_ctrc_com_digito():
    assert OP506Indenizacao.parse_ctrc(
        "CWB193958-1"
    ) == ("CWB", "1939581")


def test_parse_ctrc_sem_digito():
    assert OP506Indenizacao.parse_ctrc(
        "JOI795316"
    ) == ("JOI", "795316")


def test_formatadores_base():
    assert somente_digitos(
        "05.117.268/0008-06\xa0"
    ) == "05117268000806"

    assert valor_ssw("1.05") == "1,05"
    assert valor_ssw("175,23") == "175,23"
    assert data_ssw("31/07/2026") == "310726"
    assert competencia_ssw("", "310726") == "0726"
    assert competencia_ssw("0826", "310726") == "0826"

def test_normalizar_cnpj_preserva_zero_inicial():
    assert normalizar_cnpj(
        "5117268000806"
    ) == "05117268000806"

    assert normalizar_cnpj(
        "05117268000806"
    ) == "05117268000806"

    assert normalizar_cnpj("") == ""

def test_historico_op475_sem_acentos():
    original = (
        "7855/2026/MULTA POR EXTRAVIO "
        "DE PEÇAS - JUNHO 26"
    )

    esperado = (
        "7855/2026/MULTA POR EXTRAVIO "
        "DE PECAS - JUNHO 26"
    )

    assert (
        OP475Despesas._historico_compativel_ssw(original)
        == esperado
    )

    assert (
        OP475Despesas._historico_compativel_ssw(
            "INDENIZAÇÃO E DEVOLUÇÃO"
        )
        == "INDENIZACAO E DEVOLUCAO"
    )
