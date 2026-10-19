from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from modules.ocorrencia_73.service import Ocorrencia73Service
from operations.op455.report import OP455Report


class OP455Simulada:
    def __init__(self):
        self.chamadas = []

    def gerar_e_baixar_ocorrencia_73(self, **kwargs):
        self.chamadas.append(kwargs)
        return Path(f"{kwargs['unidade'].lower()}.sswweb")


def test_payload_oc73_restaura_formato_confirmado_por_har():
    class ClientCaptura:
        def __init__(self):
            self.chamadas = []

        def post(self, path, data=None):
            self.chamadas.append((path, data))
            return SimpleNamespace(text="relatorio solicitado")

    client = ClientCaptura()
    op455 = OP455Report(client)

    resposta = op455.gerar_relatorio_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
    )

    assert resposta == "relatorio solicitado"
    assert len(client.chamadas) == 1
    path, payload = client.chamadas[0]
    assert path == "/bin/ssw0230"
    assert payload["act"] == "E1"
    assert payload["f3"] == "E"
    assert "reg_tipo" not in payload


def test_fluxo_operacional_estabelece_cwb_antes_de_abrir_op455():
    client = SimpleNamespace(get=Mock(), open_option=Mock())
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
    client.open_option.assert_called_once_with("455", "CWB")
    assert capturar.call_args.kwargs["unidade"] == "CWB"
    assert fila.baixar_por_opcao.call_args.kwargs["unidade"] == "CWB"
    validar.assert_called_once_with(Path("relatorio-cwb.sswweb"))


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
