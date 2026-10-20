import json

from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.op455.report import OP455Report
from scripts import pre_voo_op455_cwb as cli


def resposta_abertura(*, unidade="CWB", tipo="A", dominio="ROD CWB"):
    html = (
        f'<div id="infodiv">Domínio: {dominio}</div>'
        '<form id="frm" method="POST" action="/bin/ssw0230">'
        '<input type="hidden" name="csrf_token" value="token-ultrassecreto">'
        '<input type="hidden" name="act" value="">'
        f'<input name="f2" id="2" value="{unidade}">'
        f'<input name="f3" id="3" value="{tipo}">'
        '<select name="reg_tipo">'
        '<option value="E" selected>Expedidora</option>'
        '<option value="R">Recebedora</option>'
        '</select>'
        '<input name="senha" value="segredo-que-nao-pode-sair">'
        '<textarea name="observacao">dado-pessoal-nao-pode-sair</textarea>'
        '<select name="filtro_sensivel">'
        '<option value="documento-secreto" selected>Pessoa sigilosa</option>'
        '</select>'
        '</form>'
    )
    return SimpleNamespace(
        status_code=200,
        text=html,
        content=html.encode("utf-8"),
        headers={"Content-Type": "text/html; charset=utf-8"},
        url="https://usuario:senha@ssw.exemplo/bin/ssw0230?token=segredo",
    )


class ClienteNavegacaoFalso:
    def __init__(self, abertura=None):
        self.abertura = abertura or resposta_abertura()
        self.chamadas = []

    def get(self, path, params=None, retries=None):
        self.chamadas.append(("GET", path, params, retries))
        return SimpleNamespace(status_code=200, text="", content=b"", headers={}, url=path)

    def post(self, path, data=None, retries=None):
        self.chamadas.append(("POST", path, data, retries))
        return self.abertura


class ClienteBaseFalso:
    dominio = "ROD"
    cpf = "00000000000"
    usuario = "operador.aprovado"
    senha = "senha-nao-exposta"

    def __init__(self, abertura=None):
        self.abertura = abertura or resposta_abertura()
        self.chamadas = []

    def post(self, path, data=None, retries=3):
        self.chamadas.append(("POST", path, dict(data or {}), retries))
        if path == "/bin/ssw0230":
            return self.abertura
        return SimpleNamespace(status_code=200, text="", content=b"", headers={}, url=path)

    def get(self, path, params=None, retries=3):
        self.chamadas.append(("GET", path, dict(params or {}), retries))
        return SimpleNamespace(status_code=200, text="", content=b"", headers={}, url=path)


@pytest.fixture
def flags_seguras(monkeypatch):
    for nome, valor in cli.FLAGS_SEGURAS.items():
        monkeypatch.setenv(nome, valor)
    monkeypatch.setenv(cli.VAR_USUARIO_AUTORIZADO, "operador.aprovado")


def test_metodo_pre_voo_apenas_navega_valida_e_nao_libera_submit():
    client = ClienteNavegacaoFalso()
    report = OP455Report(client)

    resultado = report.pre_voo_navegacao_ocorrencia_73_cwb()

    assert resultado["resultado"] == "contexto_confirmado"
    assert resultado["contexto"] == {"status": "confirmado"}
    assert [item[1] for item in client.chamadas] == [
        "/bin/ssw0082", "/bin/menu01", "/bin/ssw0230",
    ]
    assert all(item[3] == 1 for item in client.chamadas)
    assert not any(item[1] in {"/bin/ssw1440", "/bin/ssw0424", "/bin/ssw0122"}
                   for item in client.chamadas)
    assert not any((item[2] or {}).get("act") == "E1" for item in client.chamadas)

    assert report._contexto_oc73_confirmado_unidade is None
    assert len(client.chamadas) == 3


def test_metodo_pre_voo_rejeita_tela_divergente_sem_continuar():
    client = ClienteNavegacaoFalso(resposta_abertura(unidade="BIG"))

    resultado = OP455Report(client).pre_voo_navegacao_ocorrencia_73_cwb()

    assert resultado["resultado"] == "contexto_nao_confirmado"
    assert resultado["contexto"] == {
        "status": "nao_confirmado", "motivo": "unidade_divergente",
    }
    assert len(client.chamadas) == 3


def test_resultado_do_metodo_e_sanitizado():
    resultado = OP455Report(
        ClienteNavegacaoFalso(),
    ).pre_voo_navegacao_ocorrencia_73_cwb()
    serializado = json.dumps(resultado, ensure_ascii=False)

    assert "senha-nao-exposta" not in serializado
    assert "segredo-que-nao-pode-sair" not in serializado
    assert "token-ultrassecreto" not in serializado
    assert "dado-pessoal-nao-pode-sair" not in serializado
    assert "documento-secreto" not in serializado
    assert "Pessoa sigilosa" not in serializado
    assert "token=segredo" not in serializado
    assert resultado["resposta"]["url_final"] == "https://ssw.exemplo/bin/ssw0230"


def test_contrato_sanitizado_identifica_controles_hidden_e_opcoes_permitidas():
    resultado = OP455Report(
        ClienteNavegacaoFalso(),
    ).pre_voo_navegacao_ocorrencia_73_cwb()
    contrato = resultado["contrato_formulario"]

    assert contrato["status"] == "extraido"
    assert contrato["metodo"] == "post"
    assert contrato["action"] == "/bin/ssw0230"
    assert contrato["campos_ocultos"] == ["act", "csrf_token"]
    assert contrato["conclusao_payload_submit"] == "nao_inferida"

    controles = {item["nome"]: item for item in contrato["controles"]}
    assert controles["act"] == {"nome": "act", "tipo": "hidden", "valor": ""}
    assert controles["f2"]["valor"] == "CWB"
    assert controles["f3"]["valor"] == "A"
    assert controles["reg_tipo"]["opcoes"] == [
        {"valor": "E", "selecionada": True},
        {"valor": "R", "selecionada": False},
    ]
    assert controles["csrf_token"] == {"nome": "csrf_token", "tipo": "hidden"}
    assert controles["senha"] == {"nome": "senha", "tipo": "input"}
    assert controles["observacao"] == {"nome": "observacao", "tipo": "textarea"}
    assert controles["filtro_sensivel"] == {
        "nome": "filtro_sensivel", "tipo": "select",
    }


def test_valor_suspeito_em_campo_permitido_tambem_e_omitido():
    html = (
        '<form method="POST" action="/bin/ssw0230">'
        '<input type="hidden" name="act" value="token-secreto-muito-longo">'
        '<select name="reg_tipo">'
        '<option value="segredo-muito-longo" selected>Texto privado</option>'
        '</select></form>'
    )
    contrato = OP455Report.extrair_contrato_formulario_op455(
        SimpleNamespace(text=html),
    )
    serializado = json.dumps(contrato, ensure_ascii=False)

    assert "token-secreto-muito-longo" not in serializado
    assert "segredo-muito-longo" not in serializado
    assert "Texto privado" not in serializado
    assert contrato["controles"][0]["valor_classificacao"] == (
        "omitido_nao_classificado"
    )
    assert contrato["controles"][1]["opcoes"] == [
        {"selecionada": True, "valor_classificacao": "omitido_nao_classificado"},
    ]


def test_contrato_nao_confunde_formulario_alheio_com_op455():
    html = (
        '<form method="POST" action="/outra">'
        '<input type="hidden" name="token" value="segredo"></form>'
    )
    response = SimpleNamespace(text=html)

    assert OP455Report.extrair_contrato_formulario_op455(response) == {
        "status": "inconclusivo", "motivo": "formulario_ausente",
    }


def test_cli_executa_exatamente_cinco_chamadas_sem_retry(flags_seguras):
    base = ClienteBaseFalso()

    resultado = cli.executar_pre_voo(client_factory=lambda: base)

    assert resultado["resultado"] == "contexto_confirmado"
    assert resultado["chamadas_http"] == 5
    assert [(item[0], item[1]) for item in base.chamadas] == [
        ("POST", "/bin/ssw0422"),
        ("POST", "/bin/menu01"),
        ("GET", "/bin/ssw0082"),
        ("POST", "/bin/menu01"),
        ("POST", "/bin/ssw0230"),
    ]
    assert all(item[3] == 1 for item in base.chamadas)
    assert resultado["geracao_relatorio"] is False
    assert resultado["consulta_op156"] is False
    assert resultado["download"] is False


@pytest.mark.parametrize(
    ("metodo", "path", "payload"),
    [
        ("post", "/bin/ssw0122", {"act": "II3", "f3": "73"}),
        ("post", "/bin/ssw1440", {}),
        ("post", "/bin/ssw0424", {}),
        ("post", "/bin/ssw0230", {"act": "E1"}),
    ],
)
def test_allowlist_bloqueia_endpoints_e_acoes_operacionais(metodo, path, payload):
    base = ClienteBaseFalso()
    client = cli.ClientePreVooOP455CWB(base)

    with pytest.raises(cli.PreVooBloqueado):
        getattr(client, metodo)(path, data=payload)

    assert base.chamadas == []


def test_allowlist_bloqueia_payload_extra_antes_da_rede():
    base = ClienteBaseFalso()
    client = cli.ClientePreVooOP455CWB(base)

    with pytest.raises(cli.PreVooBloqueado, match="Payload"):
        client.post(
            "/bin/ssw0422",
            data={
                "act": "L", "f1": "ROD", "f2": "cpf", "f3": "usuario",
                "f4": "senha", "f6": "TRUE", "dummy": "1", "extra": "x",
            },
        )

    assert base.chamadas == []


def test_chamada_adicional_apos_abertura_e_bloqueada(flags_seguras):
    base = ClienteBaseFalso()
    client = cli.ClientePreVooOP455CWB(base)
    client.login()
    client.open_menu()
    resultado = OP455Report(client).pre_voo_navegacao_ocorrencia_73_cwb()
    assert resultado["resultado"] == "contexto_confirmado"

    with pytest.raises(cli.PreVooBloqueado, match="encerrado"):
        client.post("/bin/ssw0230", data={"sequencia": "455", "dummy": "2"})
    assert len(base.chamadas) == 5


def test_flag_insegura_bloqueia_antes_de_criar_cliente(flags_seguras, monkeypatch):
    criado = False

    def factory():
        nonlocal criado
        criado = True
        return ClienteBaseFalso()

    monkeypatch.setenv("OCORRENCIA_73_ENABLED", "true")
    with pytest.raises(cli.PreVooBloqueado, match="OCORRENCIA_73_ENABLED"):
        cli.executar_pre_voo(client_factory=factory)
    assert criado is False


def test_usuario_divergente_bloqueia_antes_do_login(flags_seguras, monkeypatch):
    base = ClienteBaseFalso()
    monkeypatch.setenv(cli.VAR_USUARIO_AUTORIZADO, "outro.operador")

    with pytest.raises(cli.PreVooBloqueado, match="diverge"):
        cli.executar_pre_voo(client_factory=lambda: base)
    assert base.chamadas == []


def test_autorizacao_cli_e_obrigatoria(monkeypatch):
    chamado = False

    def nao_pode_executar():
        nonlocal chamado
        chamado = True
        return {}

    monkeypatch.setattr(cli, "executar_pre_voo", nao_pode_executar)
    with pytest.raises(SystemExit) as saida:
        cli.main([])
    assert saida.value.code == 2
    assert chamado is False


def test_modulo_cli_nao_importa_runtime_operacional():
    fonte = Path(cli.__file__).read_text(encoding="utf-8")
    proibidos = (
        "modules.ocorrencia_73.service",
        "modules.ocorrencia_73.repository",
        "modules.ocorrencia_73.scheduler",
        "operations.op101",
        "web.main",
    )
    assert all(nome not in fonte for nome in proibidos)
