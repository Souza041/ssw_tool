import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from operations.op455.report import OP455Report
from ssw.client import SSWClient


OPCAO = "455 - Fretes Expedidos/Recebidos - CTRCs"


def resposta(html, *, status=200, content_type="text/html; charset=utf-8"):
    return SimpleNamespace(
        text=html,
        content=html.encode("utf-8"),
        status_code=status,
        url="https://usuario:senha@ssw.exemplo/bin/ssw0230?token=secreto",
        headers={
            "Content-Type": content_type,
            "Set-Cookie": "sessao=segredo",
            "Authorization": "Bearer segredo",
        },
    )


def fila(*ids, unidade="CWB"):
    return "".join(
        f"<r><f0>{item}</f0><f1>{OPCAO}</f1><f2>data</f2>"
        f"<f3>tester</f3><f4>{unidade}</f4>"
        "<f6>Processando</f6><f8></f8></r>"
        for item in ids
    )


class ClienteFalso:
    usuario = "tester"

    def __init__(self, resposta_submit, snapshots, resposta_abertura=None):
        self.resposta_submit = resposta_submit
        self.snapshots = list(snapshots)
        self.resposta_abertura = resposta_abertura
        self.chamadas = []

    def open_option(self, opcao, unidade):
        self.chamadas.append(("open", opcao, unidade))
        return self.resposta_abertura

    def get(self, path, params=None):
        self.chamadas.append(("get", path, params))
        return SimpleNamespace(text="")

    def post(self, path, data=None, retries=None):
        self.chamadas.append(("post", path, data, retries))
        if path == "/bin/ssw1440":
            return SimpleNamespace(text=self.snapshots.pop(0))
        if path == "/bin/ssw0230":
            return self.resposta_submit
        raise AssertionError(f"Endpoint não autorizado no teste: {path}")


@pytest.mark.parametrize(
    ("html", "localizacao"),
    [
        ("<script>const aviso = 'Informe a unidade';</script>", "script"),
        ("<style>.erro::after { content: 'Informe a unidade'; }</style>", "style"),
        ("<div class='erro'>Informe a unidade</div>", "texto_visivel"),
        ("<!-- Informe a unidade -->", "comentario"),
        ("<input title='Informe a unidade'>", "atributo"),
    ],
)
def test_classifica_local_da_frase_sem_confundir_com_texto_visivel(
    html, localizacao
):
    resumo = OP455Report.resumir_resposta_diagnostica(resposta(html))

    assert resumo["frase_informe_unidade"][localizacao] is True
    assert sum(resumo["frase_informe_unidade"].values()) == 1


def test_classifica_ausencia_da_frase():
    resumo = OP455Report.resumir_resposta_diagnostica(
        resposta("<html><body>Solicitação recebida</body></html>")
    )

    assert not any(resumo["frase_informe_unidade"].values())


def test_formulario_expoe_valores_somente_da_allowlist():
    html = """
        <form>
          <input type="hidden" name="act" value="E1">
          <select name="f3"><option value="E" selected>Emitidos</option></select>
          <input type="text" name="reg_tipo" value="">
          <input type="password" name="senha" value="segredo-123">
          <input type="hidden" name="csrf_token" value="token-ultrassecreto">
          <select name="documento"><option value="12345678900" selected>CPF</option></select>
        </form>
    """

    resumo = OP455Report.resumir_resposta_diagnostica(resposta(html))
    serializado = json.dumps(resumo, ensure_ascii=False)
    campos = {campo["nome"]: campo for campo in resumo["campos_formulario"]}

    assert campos["act"]["valor"] == "E1"
    assert campos["f3"]["opcoes_selecionadas"] == [
        {"selecionada": True, "valor": "E", "texto": "Emitidos"}
    ]
    assert campos["reg_tipo"]["valor"] == ""
    assert "valor" not in campos["senha"]
    assert "valor" not in campos["csrf_token"]
    assert campos["documento"]["opcoes_selecionadas"] == [
        {"selecionada": True}
    ]
    assert "segredo-123" not in serializado
    assert "token-ultrassecreto" not in serializado
    assert "12345678900" not in serializado
    assert "sessao=segredo" not in serializado
    assert "Bearer segredo" not in serializado
    assert "token=secreto" not in serializado
    assert resumo["url_final"] == "https://ssw.exemplo/bin/ssw0230"


def test_diagnostico_detecta_um_relatorio_novo_sem_download():
    cliente = ClienteFalso(
        resposta("<script>const x = 'Informe a unidade';</script>"),
        [fila("100"), fila("100", "101")],
    )

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
        unidade="CWB",
    )

    assert resultado["resultado"] == "submit_aceito"
    assert resultado["unidade"] == "CWB"
    assert resultado["unidade_contexto_op455"] == "CWB"
    assert cliente.chamadas[0][0:2] == ("get", "/bin/ssw0082")
    assert cliente.chamadas[0][2]["quadro_menu01"] == "CWB"
    assert cliente.chamadas[1] == ("open", "455", "CWB")
    assert resultado["op156"]["consultas_depois"] == 1
    assert resultado["op156"]["novos_relatorios"] == 1
    assert resultado["download_executado"] is False
    assert [item[1] for item in cliente.chamadas if item[0] == "post"] == [
        "/bin/ssw1440",
        "/bin/ssw0230",
        "/bin/ssw1440",
    ]
    assert [item[3] for item in cliente.chamadas if item[0] == "post"] == [
        1,
        1,
        1,
    ]


@pytest.mark.parametrize("fluxo", ["CWB", "BIG"])
def test_transicao_do_menu_preserva_identificacao_do_fluxo(fluxo):
    cliente = ClienteFalso(
        resposta("<html><body>Solicitação recebida</body></html>"),
        [fila(unidade=fluxo), fila(unidade=fluxo)],
    )

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
        unidade=fluxo,
    )

    assert resultado["unidade"] == fluxo
    assert resultado["unidade_contexto_op455"] == fluxo
    assert cliente.chamadas[0][0:2] == ("get", "/bin/ssw0082")
    assert cliente.chamadas[0][2]["quadro_menu01"] == fluxo
    assert cliente.chamadas[1] == ("open", "455", fluxo)
    submit = next(
        item for item in cliente.chamadas
        if item[0] == "post" and item[1] == "/bin/ssw0230"
    )
    assert submit[2]["act"] == "E1"
    assert submit[2]["f3"] == "E"
    assert "reg_tipo" not in submit[2]


@pytest.mark.parametrize("unidade", ["CWB", "BIG", "MTZ"])
def test_abertura_reproduz_onchange_antes_de_abrir_op455(unidade):
    cliente = ClienteFalso(resposta(""), [])

    OP455Report(cliente).open(unidade)

    assert cliente.chamadas[0][0:2] == ("get", "/bin/ssw0082")
    assert cliente.chamadas[0][2]["quadro_menu01"] == unidade
    assert cliente.chamadas[1] == ("open", "455", unidade)


def test_transicao_completa_usa_parametros_documentados_do_menu():
    chamadas = Mock()
    client = object.__new__(SSWClient)
    client.unidade = "CWB"
    client.get = chamadas.get
    client.post = chamadas.post

    OP455Report(client).open("BIG")

    assert chamadas.mock_calls[0].args[0] == "/bin/ssw0082"
    assert chamadas.mock_calls[0].kwargs["params"]["quadro_menu01"] == "BIG"
    assert chamadas.mock_calls[1].args[0] == "/bin/menu01"
    assert chamadas.mock_calls[1].args[1]["act"] == "TRO"
    assert chamadas.mock_calls[1].args[1]["f2"] == "BIG"
    assert chamadas.mock_calls[1].args[1]["f3"] == "455"
    assert chamadas.mock_calls[2].args[0] == "/bin/ssw0053"
    assert chamadas.mock_calls[2].args[1]["sequencia"] == "455"


def test_mtz_nao_injeta_unidade_no_formulario_retornado_vazio():
    abertura = SimpleNamespace(text='<input name="unidade" value="">')
    cliente = ClienteFalso(resposta(""), [], resposta_abertura=abertura)

    retorno = OP455Report(cliente).open("MTZ")

    assert retorno is abertura
    assert retorno.text == '<input name="unidade" value="">'


def test_abertura_rejeita_unidade_vazia_antes_de_navegar():
    cliente = ClienteFalso(resposta(""), [])

    with pytest.raises(ValueError, match="Unidade não informada"):
        OP455Report(cliente).open("  ")

    assert cliente.chamadas == []


def test_diagnostico_sem_relatorio_novo_permanece_inconclusivo():
    cliente = ClienteFalso(
        resposta("<html><body>Solicitação recebida</body></html>"),
        [fila("100"), fila("100")],
    )

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
    )

    assert resultado["resultado"] == "inconclusivo"
    assert resultado["op156"]["novo_relatorio_detectado"] is False
    assert resultado["op156"]["novos_relatorios"] == 0


def test_erro_http_interrompe_antes_da_consulta_op156_posterior():
    cliente = ClienteFalso(
        resposta("<html>erro</html>", status=500),
        [fila("100")],
    )

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
    )

    assert resultado["resultado"] == "erro_http"
    assert resultado["op156"]["consultas_depois"] == 0
    assert len(cliente.snapshots) == 0


def test_resposta_inesperada_interrompe_antes_da_consulta_op156_posterior():
    cliente = ClienteFalso(
        resposta("conteúdo binário", content_type="application/octet-stream"),
        [fila("100")],
    )

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
    )

    assert resultado["resultado"] == "resposta_inesperada"
    assert resultado["op156"]["consultas_depois"] == 0
    assert resultado["download_executado"] is False


def test_erro_visivel_continua_classificado_como_erro_real():
    cliente = ClienteFalso(
        resposta("<div class='erro'>Informe a unidade</div>"),
        [fila("100"), fila("100")],
    )

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026",
        data_final="191026",
    )

    assert resultado["resultado"] == "erro_unidade_visivel"
