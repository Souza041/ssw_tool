import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from operations.op455.report import OP455Report
from ssw.client import SSWClient


OPCAO = "455 - Fretes Expedidos/Recebidos - CTRCs"


def abertura_sintetica(
    unidade="CWB", dominio="ROD CWB", tipo="A", form=True,
    method="POST", action="/bin/ssw0230", extras="", fechar=True,
):
    campos = (
        f'<input name="f2" id="2" value="{unidade}">'
        f'<input name="f3" id="3" value="{tipo}">'
        '<input name="reg_sigla" value="">'
        '<input name="reg_tipo" value="E">'
        '<input name="cod_emp_ctb" value="00" readonly>'
        f'{extras}'
    )
    formulario = (
        f'<form id="frm" method="{method}" action="{action}">'
        f'{campos}{"</form>" if fechar else ""}'
        if form else campos
    )
    return resposta(f'<div id="infodiv">Domínio: {dominio}</div>{formulario}')


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
        return self.resposta_abertura or abertura_sintetica()

    def get(self, path, params=None, retries=None):
        self.chamadas.append(("get", path, params, retries))
        return SimpleNamespace(text="")

    def post(self, path, data=None, retries=None):
        self.chamadas.append(("post", path, data, retries))
        if path == "/bin/menu01":
            return SimpleNamespace(text="")
        if path == "/bin/ssw0230" and data.get("sequencia") == "455":
            return self.resposta_abertura or abertura_sintetica()
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
    assert [item[1] for item in cliente.chamadas[:3]] == [
        "/bin/ssw0082", "/bin/menu01", "/bin/ssw0230",
    ]
    assert resultado["op156"]["consultas_depois"] == 1
    assert resultado["op156"]["novos_relatorios"] == 1
    assert resultado["download_executado"] is False
    assert [item[1] for item in cliente.chamadas if item[0] == "post"] == [
        "/bin/menu01",
        "/bin/ssw0230",
        "/bin/ssw1440",
        "/bin/ssw0230",
        "/bin/ssw1440",
    ]
    assert [item[3] for item in cliente.chamadas if item[0] == "post"] == [
        1,
        1,
        1,
        1,
        1,
    ]


def test_transicao_do_menu_preserva_identificacao_cwb():
    fluxo = "CWB"
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
    assert cliente.chamadas[1][0:2] == ("post", "/bin/menu01")
    assert cliente.chamadas[2][0:2] == ("post", "/bin/ssw0230")
    submit = next(
        item for item in cliente.chamadas
        if item[0] == "post" and item[1] == "/bin/ssw0230"
        and item[2].get("act") == "E1"
    )
    assert submit[2]["act"] == "E1"
    assert submit[2]["cod_emp_ctb"] == "00"
    assert "f2" not in submit[2]
    assert "f3" not in submit[2]
    assert "reg_tipo" not in submit[2]
    assert submit[2]["f21"] == "X"
    assert submit[2]["f37"] == "F"
    assert submit[2]["f38"] == "B"
    assert "f5" not in submit[2]
    assert submit[3] == 1


def test_big_nao_e_confirmado_sem_evidencia_real():
    cliente = ClienteFalso(
        resposta("<html><body>Solicitação recebida</body></html>"),
        [fila(unidade="BIG")],
    )

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026", data_final="191026", unidade="BIG",
    )

    assert resultado["resultado"] == "contexto_op455_nao_confirmado"
    assert resultado["contexto_op455"]["status"] == "nao_confirmado"
    assert resultado["op156"]["consultas_antes"] == 0
    assert resultado["op156"]["consultas_depois"] == 0
    assert resultado["download_executado"] is False

    # Não houve exceção: o contexto foi rejeitado de forma controlada.
    assert "erro_tipo" not in resultado

    # Nenhuma unidade foi confirmada.
    assert resultado["unidade_contexto_op455"] is None

    # Somente a navegação é permitida.
    # Não pode consultar OP156 nem solicitar geração do relatório.
    assert len(cliente.chamadas) == 3
    assert [chamada[1] for chamada in cliente.chamadas] == [
        "/bin/ssw0082",
        "/bin/menu01",
        "/bin/ssw0230",
    ]


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


def test_abertura_oc73_cwb_usa_endpoint_payload_e_ordem_do_har():
    chamadas = Mock()
    abertura = abertura_sintetica()
    chamadas.post.return_value = abertura
    client = SimpleNamespace(
        get=chamadas.get, post=chamadas.post,
        open_option=Mock(),
    )

    retorno = OP455Report(client)._abrir_op455_oc73_cwb()

    assert retorno is abertura
    assert [(call[0], call.args[0]) for call in chamadas.mock_calls] == [
        ("get", "/bin/ssw0082"),
        ("post", "/bin/menu01"),
        ("post", "/bin/ssw0230"),
    ]
    assert chamadas.mock_calls[0].kwargs["params"]["quadro_menu01"] == "CWB"
    assert chamadas.mock_calls[1].kwargs["data"]["act"] == "TRO"
    assert chamadas.mock_calls[1].kwargs["data"]["f2"] == "CWB"
    assert chamadas.mock_calls[1].kwargs["data"]["f3"] == "455"
    assert chamadas.mock_calls[2].kwargs["data"]["sequencia"] == "455"
    assert all(call.kwargs["retries"] == 1 for call in chamadas.mock_calls)
    client.open_option.assert_not_called()


@pytest.mark.parametrize("etapa", ["/bin/ssw0082", "/bin/menu01", "/bin/ssw0230"])
@pytest.mark.parametrize("entrada", ["diagnostico", "operacional"])
def test_falha_na_abertura_impede_op156_e_geracao(etapa, entrada, tmp_path):
    cliente = ClienteFalso(resposta("<html/>"), [fila("100")])
    get_original = cliente.get
    post_original = cliente.post

    def get_com_falha(path, params=None, retries=None):
        if path == etapa:
            cliente.chamadas.append(("get", path, params, retries))
            raise RuntimeError("falha simulada")
        return get_original(path, params=params, retries=retries)

    def post_com_falha(path, data=None, retries=None):
        if path == etapa:
            cliente.chamadas.append(("post", path, data, retries))
            raise RuntimeError("falha simulada")
        return post_original(path, data=data, retries=retries)

    cliente.get = get_com_falha
    cliente.post = post_com_falha
    report = OP455Report(cliente)
    if entrada == "diagnostico":
        resultado = report.diagnosticar_geracao_ocorrencia_73("191026", "191026")
        assert resultado["resultado"] == "erro_pre_voo"
        assert resultado["op156"]["consultas_antes"] == 0
    else:
        with pytest.raises(RuntimeError, match="falha simulada"):
            report.gerar_e_baixar_ocorrencia_73(tmp_path, "191026", "191026")
    assert cliente.chamadas[-1][1] == etapa
    assert not any(item[1] == "/bin/ssw1440" for item in cliente.chamadas)
    assert not any(item[2] and item[2].get("act") == "E1"
                   for item in cliente.chamadas if item[0] == "post")
    assert list(tmp_path.iterdir()) == []


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


CONTEXTOS_INVALIDOS = [
        (resposta('<div id="infodiv">Sem domínio</div>'), "dominio_ausente"),
        (abertura_sintetica(dominio="ROD BIG"), "dominio_divergente"),
        (abertura_sintetica(dominio="ROD CWB-BIG"), "dominio_divergente"),
        (abertura_sintetica(unidade="BIG"), "unidade_divergente"),
        (abertura_sintetica(tipo="E"), "tipo_divergente"),
        (abertura_sintetica(form=False), "formulario_ausente"),
        (abertura_sintetica(method="GET"), "metodo_invalido"),
        (abertura_sintetica(action="/bin/outra"), "action_invalida"),
        (abertura_sintetica(extras='<input name="f2" id="2" value="CWB">'),
         "campo_unidade_duplicado"),
        (abertura_sintetica(extras='<input name="f3" id="3" value="A">'),
         "campo_tipo_duplicado"),
        (abertura_sintetica(fechar=False), "html_incompleto"),
        (resposta(abertura_sintetica().text.replace('id="2"', 'id="outro"')),
         "unidade_divergente"),
        (resposta(abertura_sintetica().text.replace('id="3"', 'id="outro"')),
         "tipo_divergente"),
        (resposta(abertura_sintetica().text.replace(
            '<input name="f2" id="2" value="CWB">', '', 1,
        )), "campo_unidade_ausente"),
        (resposta(abertura_sintetica().text.replace(
            '<input name="f3" id="3" value="A">', '', 1,
        )), "campo_tipo_ausente"),
        (resposta(abertura_sintetica().text +
                  '<form id="frm" method="POST" action="/bin/ssw0230"></form>'),
         "formulario_duplicado"),
        (resposta(abertura_sintetica().text + abertura_sintetica().text),
         "estrutura_ambigua"),
        (resposta(abertura_sintetica().text.replace(
            'id="frm"', 'id="frm" id="outro"', 1,
        )), "estrutura_ambigua"),
        (resposta(abertura_sintetica().text.replace(
            '</div>', '', 1,
        )), "html_incompleto"),
        (resposta('<div>Domínio: ROD CWB</div>'
                 '<form id="frm" method="POST" action="/bin/ssw0230">'
                 '<input name="f2" id="2" value="CWB">'
                 '<input name="f3" id="3" value="A"></form>'),
         "dominio_ausente"),
]


@pytest.mark.parametrize(("abertura", "motivo"), CONTEXTOS_INVALIDOS)
def test_validador_rejeita_contexto_invalido(abertura, motivo):
    resultado = OP455Report.validar_contexto_abertura_ocorrencia_73(
        abertura, "CWB",
    )
    assert resultado == {"status": "nao_confirmado", "motivo": motivo}


def test_validador_confirma_cwb_apenas_no_contexto_correto():
    resultado = OP455Report.validar_contexto_abertura_ocorrencia_73(
        abertura_sintetica(), "CWB",
    )
    assert resultado == {"status": "confirmado"}


def test_texto_fora_do_infodiv_nao_confirma_dominio():
    html = abertura_sintetica(dominio="ROD BIG").text
    abertura = resposta("<div>Domínio: ROD CWB</div>" + html)
    assert OP455Report.validar_contexto_abertura_ocorrencia_73(
        abertura, "CWB",
    )["status"] == "nao_confirmado"


def test_campos_secundarios_nao_substituem_f2_e_f3():
    html = abertura_sintetica().text.replace('name="f2"', 'name="outro"')
    html = html.replace('name="f3"', 'name="outro_tipo"')
    abertura = resposta(html)
    assert OP455Report.validar_contexto_abertura_ocorrencia_73(
        abertura, "CWB",
    )["motivo"] == "campo_unidade_ausente"


def test_abertura_incompleta_bloqueia_op156_e_submit():
    cliente = ClienteFalso(resposta("<html/>"), [fila("100")], resposta("<html/>"))

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026", data_final="191026", unidade="CWB",
    )

    assert resultado["resultado"] == "contexto_op455_nao_confirmado"
    assert resultado["unidade_contexto_op455"] is None
    assert resultado["contexto_op455"]["status"] == "nao_confirmado"
    assert resultado["op156"]["consultas_antes"] == 0
    assert cliente.chamadas[0][0] == "get"
    assert [item[1] for item in cliente.chamadas] == [
        "/bin/ssw0082", "/bin/menu01", "/bin/ssw0230",
    ]


@pytest.mark.parametrize(("abertura", "motivo"), CONTEXTOS_INVALIDOS)
def test_contexto_nao_confirmado_impede_toda_chamada_posterior(abertura, motivo):
    cliente = ClienteFalso(resposta("<html/>"), [fila("100")], abertura)

    resultado = OP455Report(cliente).diagnosticar_geracao_ocorrencia_73(
        data_inicial="191026", data_final="191026", unidade="CWB",
    )

    assert resultado["resultado"] == "contexto_op455_nao_confirmado"
    assert resultado["contexto_op455"]["motivo"] == motivo
    assert resultado["op156"]["consultas_antes"] == 0
    assert [item[1] for item in cliente.chamadas] == [
        "/bin/ssw0082", "/bin/menu01", "/bin/ssw0230",
    ]


def test_motivo_operacional_e_sanitizado_e_bloqueia_continuacao(tmp_path):
    segredo = "segredo-token-pessoal-123"
    html = abertura_sintetica(unidade=segredo).text.replace(
        'Domínio: ROD CWB', f'Domínio: ROD CWB {segredo}',
    )
    cliente = ClienteFalso(resposta("<html/>"), [fila("100")], resposta(html))

    with pytest.raises(ValueError) as falha:
        OP455Report(cliente).gerar_e_baixar_ocorrencia_73(
            tmp_path, "191026", "191026", unidade="CWB",
        )

    mensagem = str(falha.value)
    assert "contexto_op455_nao_confirmado:unidade_divergente" in mensagem
    assert segredo not in mensagem
    assert [chamada[1] for chamada in cliente.chamadas] == [
        "/bin/ssw0082", "/bin/menu01", "/bin/ssw0230",
    ]
    assert list(tmp_path.iterdir()) == []


def test_diagnostico_divergente_nao_expoe_valores_do_html():
    segredo = "segredo-token-pessoal-123"
    abertura = abertura_sintetica(unidade=segredo)
    contexto = OP455Report.validar_contexto_abertura_ocorrencia_73(
        abertura, "CWB",
    )

    assert contexto == {"status": "nao_confirmado", "motivo": "unidade_divergente"}
    assert segredo not in json.dumps(contexto)
