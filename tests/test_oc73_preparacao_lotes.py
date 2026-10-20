from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from modules.ocorrencia_73.service import Ocorrencia73Service
from operations.op156.queue import FilaOP156Invalida
from operations.op455.report import OP455Report


class OP455Simulada:
    def __init__(self):
        self.chamadas = []

    def gerar_e_baixar_ocorrencia_73(self, **kwargs):
        self.chamadas.append(kwargs)
        return Path(f"{kwargs['unidade'].lower()}.sswweb")


def test_payload_oc73_restaura_formato_confirmado_por_har():
    op455 = OP455Report(SimpleNamespace())
    payload = op455._payload_relatorio_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
    )

    assert payload["act"] == "E1"
    assert payload["cod_emp_ctb"] == "00"
    assert payload["f2"] == "CWB"
    assert payload["f3"] == "e"
    assert payload["reg_tipo"] == "E"
    assert "f5" not in payload
    assert payload["f21"] == "T"
    assert payload["f35"] == "E"
    assert payload["f37"] == "B"


def test_submit_direto_sem_contexto_confirmado_falha_antes_de_post():
    client = SimpleNamespace(post=Mock())
    op455 = OP455Report(client)

    try:
        op455.gerar_relatorio_ocorrencia_73("191026", "191026")
    except ValueError as erro:
        assert "Contexto da OP455 não confirmado" in str(erro)
    else:
        raise AssertionError("Submit direto deveria estar bloqueado.")

    client.post.assert_not_called()


@pytest.mark.parametrize("contexto", [None, "BIG", "MTZ"])
def test_submit_rejeita_contexto_diferente_de_cwb(contexto):
    client = SimpleNamespace(post=Mock())
    op455 = OP455Report(client)
    op455._contexto_oc73_confirmado_unidade = contexto

    with pytest.raises(ValueError, match="Contexto da OP455 não confirmado"):
        op455.gerar_relatorio_ocorrencia_73("191026", "191026")

    client.post.assert_not_called()


def test_fluxo_operacional_estabelece_cwb_antes_de_abrir_op455():
    client = SimpleNamespace(get=Mock(), post=Mock(), open_option=Mock())
    client.post.return_value = SimpleNamespace(
        text=(
            '<div id="infodiv">Domínio: ROD CWB</div>'
            '<form id="frm" method="POST" action="/bin/ssw0230">'
            '<input name="f2" id="2" value="CWB">'
            '<input name="f3" id="3" value="A"></form>'
        ),
        status_code=200,
        headers={"Content-Type": "text/html"},
    )
    op455 = OP455Report(client)
    fila = Mock()
    fila.baixar_por_opcao.return_value = Path("relatorio-cwb.sswweb")

    with (
        patch("operations.op455.report.OP156Queue", return_value=fila),
        patch.object(op455, "capturar_ids_fila", return_value={"anterior"})
        as capturar,
        patch.object(
            op455,
            "gerar_relatorio_ocorrencia_73",
            return_value="solicitacao aceita",
        ),
        patch.object(op455, "validar_layout_ocorrencia_73") as validar,
    ):
        arquivo = op455.gerar_e_baixar_ocorrencia_73(
            output_dir=Path("downloads/teste"),
            data_inicial="191026",
            data_final="191026",
            unidade="CWB",
        )

    assert arquivo == Path("relatorio-cwb.sswweb")
    client.get.assert_called_once()
    assert client.get.call_args.args[0] == "/bin/ssw0082"
    assert client.get.call_args.kwargs["params"]["quadro_menu01"] == "CWB"
    assert [call.args[0] for call in client.post.call_args_list] == [
        "/bin/menu01", "/bin/ssw0230",
    ]
    client.open_option.assert_not_called()
    assert capturar.call_args.kwargs["unidade"] == "CWB"
    assert capturar.call_args.kwargs["estrito"] is True
    assert fila.baixar_por_opcao.call_args.kwargs["unidade"] == "CWB"
    assert fila.baixar_por_opcao.call_args.kwargs["estrito"] is True
    assert fila.baixar_por_opcao.call_args.kwargs["ignorar_ids"] == {"anterior"}
    validar.assert_called_once_with(Path("relatorio-cwb.sswweb"))


def test_fluxo_operacional_com_html_incompleto_nao_consulta_op156_nem_submete():
    client = SimpleNamespace(get=Mock(), post=Mock(), open_option=Mock())
    client.post.return_value = SimpleNamespace(
        text='<div id="infodiv">Domínio: ROD CWB</div>',
        status_code=200,
        headers={"Content-Type": "text/html"},
    )
    op455 = OP455Report(client)

    with patch("operations.op455.report.OP156Queue") as fila:
        try:
            op455.gerar_e_baixar_ocorrencia_73(
                output_dir=Path("downloads/teste"),
                data_inicial="191026",
                data_final="191026",
                unidade="CWB",
            )
        except ValueError as erro:
            assert "Contexto da OP455 não confirmado" in str(erro)
        else:
            raise AssertionError("Pré-voo deveria bloquear o fluxo.")

    fila.return_value.abrir_fila.assert_not_called()
    fila.return_value.baixar_por_opcao.assert_not_called()
    client.get.assert_called_once()
    assert [call.args[0] for call in client.post.call_args_list] == [
        "/bin/menu01", "/bin/ssw0230",
    ]
    client.open_option.assert_not_called()


def test_snapshot_op156_malformado_bloqueia_geracao():
    client = SimpleNamespace(get=Mock(), open_option=Mock(), post=Mock(), usuario="tester")
    abertura = SimpleNamespace(
        text=(
            '<div id="infodiv">Domínio: ROD CWB</div>'
            '<form id="frm" method="POST" action="/bin/ssw0230">'
            '<input name="f2" id="2" value="CWB">'
            '<input name="f3" id="3" value="A"></form>'
        ),
        status_code=200,
        headers={"Content-Type": "text/html"},
    )
    client.post.side_effect = lambda path, data=None, retries=None: (
        abertura if path == "/bin/ssw0230"
        else SimpleNamespace(text="<html>login</html>")
    )
    op455 = OP455Report(client)

    with patch.object(op455, "gerar_relatorio_ocorrencia_73") as gerar:
        with pytest.raises(FilaOP156Invalida):
            op455.gerar_e_baixar_ocorrencia_73(
                Path("downloads/teste"), "191026", "191026", unidade="CWB",
            )

    gerar.assert_not_called()
    assert [call.args[0] for call in client.post.call_args_list] == [
        "/bin/menu01", "/bin/ssw0230", "/bin/ssw1440",
    ]
    assert client.post.call_args.args[0] == "/bin/ssw1440"
    assert client.post.call_args.kwargs["retries"] == 1


def test_big_permanece_bloqueado_antes_da_op156():
    client = SimpleNamespace(get=Mock(), open_option=Mock(), post=Mock())
    client.open_option.return_value = SimpleNamespace(
        text='<div id="infodiv">Domínio: ROD BIG</div>',
        status_code=200,
        headers={"Content-Type": "text/html"},
    )
    with pytest.raises(ValueError, match="Abertura OC73 disponível apenas para CWB"):
        OP455Report(client).gerar_e_baixar_ocorrencia_73(
            Path("downloads/teste"), "191026", "191026", unidade="BIG",
        )
    client.post.assert_not_called()


def executar_preparacao(data, fluxo=None):
    op455 = OP455Simulada()
    service = Ocorrencia73Service()

    with patch(
        "modules.ocorrencia_73.service.carregar_relatorio",
        return_value=[],
    ):
        resultado = service.preparar_lotes_oc73(
            op455=op455,
            data_referencia=data,
            diretorio_original=Path("downloads/ocorrencia_73/teste"),
            fluxo=fluxo,
        )

    return op455.chamadas, resultado


def test_segunda_gera_cwb_e_big():
    chamadas, resultado = executar_preparacao(
        date(2026, 10, 12)
    )

    assert len(chamadas) == 2
    assert [c["unidade"] for c in chamadas] == ["CWB", "BIG"]

    for chamada in chamadas:
        assert chamada["data_inicial"] == "121026"
        assert chamada["data_final"] == "121026"

    assert resultado["total_filtrado"] == 0


def test_jobs_separados_processam_apenas_o_proprio_fluxo():
    chamadas_cwb, _ = executar_preparacao(
        date(2026, 10, 12),
        fluxo="CWB",
    )
    chamadas_big, _ = executar_preparacao(
        date(2026, 10, 12),
        fluxo="BIG",
    )

    assert [item["unidade"] for item in chamadas_cwb] == ["CWB"]
    assert [item["unidade"] for item in chamadas_big] == ["BIG"]


def test_sabado_nao_gera_relatorios():
    chamadas, resultado = executar_preparacao(
        date(2026, 10, 10)
    )

    assert chamadas == []
    assert resultado["lotes"] == []


def test_domingo_consulta_sabado_e_domingo():
    chamadas, resultado = executar_preparacao(
        date(2026, 10, 11)
    )

    assert len(chamadas) == 1
    assert chamadas[0]["unidade"] == "CWB"
    assert chamadas[0]["data_inicial"] == "101026"
    assert chamadas[0]["data_final"] == "111026"
    assert resultado["total_filtrado"] == 0


def test_falha_download_nao_vira_zero_elegiveis():
    service = Ocorrencia73Service()

    class OP455ComErro:
        def gerar_e_baixar_ocorrencia_73(self, **kwargs):
            raise RuntimeError("Falha simulada na OP455")

    try:
        service.preparar_lotes_oc73(
            op455=OP455ComErro(),
            data_referencia=date(2026, 10, 12),
            diretorio_original=Path(
                "downloads/ocorrencia_73/teste"
            ),
        )
    except RuntimeError as erro:
        assert "Falha simulada" in str(erro)
    else:
        raise AssertionError(
            "A falha da OP455 não foi propagada."
        )
