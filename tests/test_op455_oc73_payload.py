from types import SimpleNamespace

import pytest

from operations.op455.report import OP455Report


class ClienteSimulado:
    def __init__(self, falhar=False):
        self.falhar = falhar
        self.chamadas = []

    def post(self, caminho, *args, **kwargs):
        assert caminho == "/bin/ssw0230"
        self.chamadas.append(kwargs)

        if self.falhar:
            raise RuntimeError("Falha simulada no POST")

        return SimpleNamespace(text="<html>SIMULADO</html>")


def test_payload_exclusivo_oc73_cwb():
    cliente = ClienteSimulado()
    report = OP455Report(cliente)
    report._contexto_oc73_confirmado_unidade = "CWB"

    html = report.gerar_relatorio_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
        unidade="CWB",
    )

    assert html == "<html>SIMULADO</html>"
    assert len(cliente.chamadas) == 1

    chamada = cliente.chamadas[0]
    payload = chamada["data"]

    assert chamada["retries"] == 1
    assert payload["act"] == "E1"
    assert payload["f2"] == "CWB"
    assert payload["f3"] == "R"
    assert payload["reg_tipo"] == "R"
    assert payload["f9"] == "191026"
    assert payload["f10"] == "191026"
    assert payload["f35"] == "E"
    assert payload["f37"] == "B"
    assert payload["f38"] == ""
    assert payload["f39"] == ""

    assert report._contexto_oc73_confirmado_unidade is None


@pytest.mark.parametrize("falhar", [False, True])
def test_autorizacao_oc73_uso_unico(falhar):
    cliente = ClienteSimulado(falhar=falhar)
    report = OP455Report(cliente)
    report._contexto_oc73_confirmado_unidade = "CWB"

    if falhar:
        with pytest.raises(RuntimeError, match="Falha simulada"):
            report.gerar_relatorio_ocorrencia_73(
                "191026", "191026", "CWB",
            )
    else:
        report.gerar_relatorio_ocorrencia_73(
            "191026", "191026", "CWB",
        )

    assert report._contexto_oc73_confirmado_unidade is None
    assert len(cliente.chamadas) == 1

    with pytest.raises(ValueError, match="Contexto CWB"):
        report.gerar_relatorio_ocorrencia_73(
            "191026", "191026", "CWB",
        )

    assert len(cliente.chamadas) == 1
