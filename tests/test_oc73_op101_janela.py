from datetime import date
from unittest.mock import MagicMock

from operations.op101.ocorrencias import OP101Ocorrencias


def test_op101_pesquisa_intervalo_sabado_domingo():
    client = MagicMock()

    # HTML simulado contendo um CTRC válido.
    client.post.return_value.text = (
        '<input name="seq_ctrc" value="12345">'
        '<input name="local" value="Q">'
        '<input name="FAMILIA" value="ROD">'
    )

    op101 = OP101Ocorrencias(client)

    resultado = op101.consultar_ctrc(
        serie="CWB",
        numero="123456",
        data_referencia=date(2026, 10, 11),
        data_inicial=date(2026, 10, 10),
        data_final=date(2026, 10, 11),
    )

    assert resultado.encontrado is True

    chamadas_get = client.get.call_args_list

    pesquisas = [
        chamada
        for chamada in chamadas_get
        if chamada.args
        and chamada.args[0] == "/bin/ssw0385"
    ]

    assert len(pesquisas) == 1

    params = pesquisas[0].kwargs["params"]

    assert params["dd_f_t_data_ini"] == "101026"
    assert params["dd_f_t_data_fin"] == "111026"

    chamadas_post = client.post.call_args_list

    aberturas = [
        chamada
        for chamada in chamadas_post
        if chamada.args
        and chamada.args[0] == "/bin/ssw0053"
        and len(chamada.args) > 1
        and chamada.args[1].get("act") == "P1"
    ]

    assert len(aberturas) == 1

    payload = aberturas[0].args[1]

    assert payload["t_data_ini"] == "101026"
    assert payload["t_data_fin"] == "111026"


def test_op101_mantem_compatibilidade_data_unica():
    client = MagicMock()

    client.post.return_value.text = (
        '<input name="seq_ctrc" value="12345">'
    )

    op101 = OP101Ocorrencias(client)

    op101.consultar_ctrc(
        serie="CWB",
        numero="123456",
        data_referencia=date(2026, 10, 12),
    )

    pesquisas = [
        chamada
        for chamada in client.get.call_args_list
        if chamada.args
        and chamada.args[0] == "/bin/ssw0385"
    ]

    params = pesquisas[0].kwargs["params"]

    assert params["dd_f_t_data_ini"] == "121026"
    assert params["dd_f_t_data_fin"] == "121026"