import json
import logging
from types import SimpleNamespace

import pytest

from operations.op156 import queue as modulo_fila
from operations.op156.queue import (
    FilaOP156Ambigua,
    FilaOP156Invalida,
    OP156Queue,
    RelatorioSemDados,
)


OPCAO = "455 - Fretes Expedidos/Recebidos - CTRCs"


def diagnosticos(caplog):
    return [
        json.loads(record.getMessage().partition(" ")[2])
        for record in caplog.records
        if record.getMessage().startswith("op156_selecao_rejeitada ")
    ]


def linha(seq, *, unidade="CWB", usuario="tester", estado="Concluído", acao=None,
          opcao=OPCAO):
    if acao is None:
        acao = f"DOW{seq}"
    return (
        f"<r><f0>{seq}</f0><f1>{opcao}</f1><f2>data</f2>"
        f"<f3>{usuario}</f3><f4>{unidade}</f4><f6>{estado}</f6>"
        f"<f8>{acao}</f8></r>"
    )


class ClienteFalso:
    usuario = "tester"

    def __init__(self, snapshots=(), dow=None, arquivo=None):
        self.snapshots = list(snapshots)
        self.dow = dow
        self.arquivo = arquivo
        self.chamadas = []

    def post(self, path, data=None, retries=None):
        self.chamadas.append(("post", path, data, retries))
        assert path == "/bin/ssw1440"
        if data.get("act"):
            return self.dow
        if len(self.snapshots) > 1:
            html = self.snapshots.pop(0)
        else:
            html = self.snapshots[0]
        return SimpleNamespace(text=html)

    def get(self, path, params=None, retries=None):
        self.chamadas.append(("get", path, params, retries))
        assert path == "/bin/ssw0424"
        return self.arquivo


def resposta(text="", content=b"", tipo="text/html", status=200):
    def raise_for_status():
        if status >= 400:
            raise RuntimeError("HTTP inválido")
    return SimpleNamespace(
        text=text, content=content, headers={"Content-Type": tipo},
        status_code=status, raise_for_status=raise_for_status,
    )


def test_sequencia_antiga_ignorada_e_nova_unica_aceita():
    cliente = ClienteFalso([linha("897080") + linha("897142")])
    fila = OP156Queue(cliente)

    assert fila.aguardar_download_id(
        OPCAO, "CWB", ignorar_ids={"897080"}, estrito=True,
    ) == "897142"
    assert cliente.chamadas[0][3] == 1


def test_quatro_sequencias_reais_concluido_baixar_seleciona_apenas_nova():
    html = "".join(
        linha(seq, acao="<a>Baixar</a>")
        for seq in ("897436", "897432", "897428", "897425")
    )
    cliente = ClienteFalso([html])
    fila = OP156Queue(cliente)
    jobs = fila.extrair_jobs(html, OPCAO, "CWB", estrito=True)

    assert jobs[0]["situacao"] == "Concluído"
    assert jobs[0]["acao"] == "Baixar"
    assert jobs[0]["download_id"] == "897436"
    assert fila.aguardar_download_id(
        OPCAO, "CWB", ignorar_ids={"897432", "897428", "897425"},
        estrito=True,
    ) == "897436"
    assert [chamada[1] for chamada in cliente.chamadas] == ["/bin/ssw1440"]


@pytest.mark.parametrize("alteracao", [
    {"usuario": "outro"}, {"unidade": "BIG"},
])
def test_concluido_baixar_de_outro_contexto_nao_e_selecionado(alteracao):
    html = linha("897432", acao="Baixar") + linha(
        "897436", acao="Baixar", **alteracao,
    )
    cliente = ClienteFalso([html])

    with pytest.raises(TimeoutError):
        OP156Queue(cliente).aguardar_download_id(
            OPCAO, "CWB", timeout_seconds=0.01, intervalo=0.001,
            ignorar_ids={"897432"}, estrito=True,
        )
    assert all(chamada[1] == "/bin/ssw1440" for chamada in cliente.chamadas)


def test_concluido_baixar_com_duas_sequencias_novas_e_ambiguo():
    cliente = ClienteFalso([linha("897436", acao="Baixar") +
                           linha("897432", acao="Baixar")])
    with pytest.raises(FilaOP156Ambigua):
        OP156Queue(cliente).aguardar_download_id(
            OPCAO, "CWB", ignorar_ids={"897428", "897425"},
            estrito=True,
        )


def test_baixar_exige_download_id_da_mesma_sequencia(monkeypatch, caplog):
    cliente = ClienteFalso([linha("897436", acao="Baixar")])
    fila = OP156Queue(cliente)
    jobs = fila.extrair_jobs(cliente.snapshots[0], OPCAO, "CWB", estrito=True)
    jobs[0]["download_id"] = "897432"
    monkeypatch.setattr(fila, "extrair_jobs", lambda *args, **kwargs: jobs)

    with caplog.at_level(logging.WARNING, logger="operations.op156.queue"):
        with pytest.raises(FilaOP156Invalida, match="Download ID"):
            fila.aguardar_download_id(OPCAO, "CWB", ignorar_ids=set(), estrito=True)
    assert diagnosticos(caplog)[-1]["motivo"] == "download_id_invalido"
    assert diagnosticos(caplog)[-1]["download_id"] == "897432"


def test_processando_com_acao_baixar_nao_e_tratado_como_concluido(caplog):
    cliente = ClienteFalso([linha("897436", estado="Processando", acao="Baixar")])
    with caplog.at_level(logging.WARNING, logger="operations.op156.queue"):
        with pytest.raises(FilaOP156Invalida, match="Estado OP156 inválido"):
            OP156Queue(cliente).aguardar_download_id(
                OPCAO, "CWB", ignorar_ids=set(), estrito=True,
            )
    assert diagnosticos(caplog)[-1] == {
        "motivo": "estado_invalido",
        "sequencia": "897436",
        "situacao_original": "Processando",
        "situacao_normalizada": "processando",
        "acao_original": "Baixar",
        "acao_normalizada": "baixar",
        "download_id": "897436",
        "unidade": "CWB",
    }


def test_aguardando_excluir_depois_concluido_baixar_retorna_id():
    cliente = ClienteFalso([
        linha("897582", usuario="rodapi", estado="Aguardando", acao="Excluir"),
        linha("897582", usuario="rodapi", estado="Concluído", acao="Baixar"),
    ])
    cliente.usuario = "rodapi"

    assert OP156Queue(cliente).aguardar_download_id(
        OPCAO, "CWB", timeout_seconds=1, intervalo=0.001,
        ignorar_ids={"897580"}, estrito=True,
    ) == "897582"
    assert len(cliente.chamadas) == 2
    assert all(chamada[1] == "/bin/ssw1440" for chamada in cliente.chamadas)


def test_aguardando_excluir_ate_timeout_continua_aguardando():
    cliente = ClienteFalso([
        linha("897582", usuario="rodapi", estado="Aguardando", acao="Excluir"),
    ])
    cliente.usuario = "rodapi"

    with pytest.raises(TimeoutError, match="não concluiu no prazo"):
        OP156Queue(cliente).aguardar_download_id(
            OPCAO, "CWB", timeout_seconds=0.01, intervalo=0.001,
            ignorar_ids={"897580"}, estrito=True,
        )
    assert len(cliente.chamadas) > 1


def test_aguardando_baixar_permanece_bloqueado(caplog):
    cliente = ClienteFalso([linha("897582", estado="Aguardando", acao="Baixar")])
    with caplog.at_level(logging.WARNING, logger="operations.op156.queue"):
        with pytest.raises(FilaOP156Invalida, match="Estado OP156 inválido"):
            OP156Queue(cliente).aguardar_download_id(
                OPCAO, "CWB", ignorar_ids=set(), estrito=True,
            )
    assert diagnosticos(caplog)[-1]["motivo"] == "estado_invalido"


def test_aguardando_excluir_preserva_snapshot_usuario_e_unidade():
    antigas = linha("897580", usuario="rodapi", estado="Aguardando", acao="Excluir")
    outro_usuario = linha("897583", usuario="outro", estado="Aguardando", acao="Excluir")
    outra_unidade = linha("897584", unidade="BIG", usuario="rodapi",
                          estado="Aguardando", acao="Excluir")
    nova = linha("897582", usuario="rodapi", estado="Aguardando", acao="Excluir")
    concluida = linha("897582", usuario="rodapi", estado="Concluído", acao="Baixar")
    cliente = ClienteFalso([
        antigas + outro_usuario + outra_unidade + nova,
        antigas + outro_usuario + outra_unidade + concluida,
    ])
    cliente.usuario = "rodapi"

    assert OP156Queue(cliente).aguardar_download_id(
        OPCAO, "CWB", timeout_seconds=1, intervalo=0.001,
        ignorar_ids={"897580"}, estrito=True,
    ) == "897582"
    assert len(cliente.chamadas) == 2


def test_multiplas_sequencias_aguardando_continuam_ambiguas():
    cliente = ClienteFalso([
        linha("897582", estado="Aguardando", acao="Excluir") +
        linha("897583", estado="Aguardando", acao="Excluir"),
    ])
    with pytest.raises(FilaOP156Ambigua):
        OP156Queue(cliente).aguardar_download_id(
            OPCAO, "CWB", ignorar_ids={"897580"}, estrito=True,
        )


@pytest.mark.parametrize(
    ("estado", "acao"),
    [
        ("Processando", ""),
        ("Em processamento", ""),
        (
            "Em processamento (iniciado 20/10/26 14:35:12)",
            "Interromper",
        ),
    ],
)
def test_processamento_aguarda_sem_baixar(estado, acao):
    cliente = ClienteFalso([
        linha("897142", estado=estado, acao=acao),
        linha("897142"),
    ])

    assert OP156Queue(cliente).aguardar_download_id(
        OPCAO, "CWB", timeout_seconds=1, intervalo=0.001,
        ignorar_ids={"897080"}, estrito=True,
    ) == "897142"

    assert len(cliente.chamadas) == 2


def test_concluido_sem_registros_nao_baixa():
    cliente = ClienteFalso([
        linha("897142", acao="NENHUM REGISTRO ENCONTRADO"),
    ])
    with pytest.raises(RelatorioSemDados):
        OP156Queue(cliente).aguardar_download_id(
            OPCAO, "CWB", ignorar_ids={"897080"}, estrito=True,
        )
    assert len(cliente.chamadas) == 1


@pytest.mark.parametrize("estado", ["Erro", "Cancelado", "Desconhecido"])
def test_estado_terminal_ou_desconhecido_interrompe(estado, caplog):
    cliente = ClienteFalso([linha("897142", estado=estado)])
    with caplog.at_level(logging.WARNING, logger="operations.op156.queue"):
        with pytest.raises(FilaOP156Invalida):
            OP156Queue(cliente).aguardar_download_id(
                OPCAO, "CWB", ignorar_ids=set(), estrito=True,
            )
    assert diagnosticos(caplog)[-1]["motivo"] == "estado_invalido"
    assert diagnosticos(caplog)[-1]["situacao_original"] == estado


def test_multiplos_candidatos_novos_sao_ambiguos(caplog):
    cliente = ClienteFalso([linha("897142") + linha("897143")])
    with caplog.at_level(logging.WARNING, logger="operations.op156.queue"):
        with pytest.raises(FilaOP156Ambigua):
            OP156Queue(cliente).aguardar_download_id(
                OPCAO, "CWB", ignorar_ids={"897080"}, estrito=True,
            )
    assert len(cliente.chamadas) == 1
    assert diagnosticos(caplog)[-1] == {
        "motivo": "multiplas_sequencias_novas", "candidatos": 2,
    }


def test_sequencia_antiga_ausente_do_snapshot_nao_e_escolhida():
    cliente = ClienteFalso([linha("897080")])
    with pytest.raises(FilaOP156Ambigua):
        OP156Queue(cliente).aguardar_download_id(
            OPCAO, "CWB", ignorar_ids={"897142"}, estrito=True,
        )


def test_usuario_e_unidade_divergentes_nao_sao_candidatos():
    html = (
        linha("897142", usuario="outro")
        + linha("897143", unidade="BIG")
        + linha("897144")
    )
    fila = OP156Queue(ClienteFalso())
    jobs = fila.extrair_jobs(html, OPCAO, "CWB", estrito=True)
    assert [job["sequencia"] for job in jobs] == ["897144"]
    snapshot = fila.extrair_jobs(
        html, OPCAO, "CWB", estrito=True, filtrar_usuario=False,
    )
    assert {job["sequencia"] for job in snapshot} == {"897142", "897144"}


def test_log_de_rejeicao_sanitiza_segredos_html_e_controles(caplog):
    html = linha(
        "897436", estado="Concluído",
        acao="Baixar\nToken=ultrassecreto<script>Senha=123</script>",
    ).replace("</r>", "<f9>Cookie: sessao=abc</f9></r>")
    cliente = ClienteFalso([html])

    with caplog.at_level(logging.WARNING, logger="operations.op156.queue"):
        with pytest.raises(FilaOP156Invalida, match="Ação de conclusão"):
            OP156Queue(cliente).aguardar_download_id(
                OPCAO, "CWB", ignorar_ids=set(), estrito=True,
            )

    registro = caplog.records[-1].getMessage()
    contexto = diagnosticos(caplog)[-1]
    assert contexto["motivo"] == "acao_conclusao_desconhecida"
    assert contexto["acao_original"] == "[redacted]"
    assert contexto["acao_normalizada"] == "[redacted]"
    assert "ultrassecreto" not in registro
    assert "Senha=123" not in registro
    assert "sessao=abc" not in registro
    assert "<script>" not in registro
    assert "\n" not in registro


def test_log_limita_campos_textuais_e_remove_quebras_de_linha(caplog):
    cliente = ClienteFalso([linha("897436", estado="X" * 200 + "\nY", acao="Z" * 200)])
    with caplog.at_level(logging.WARNING, logger="operations.op156.queue"):
        with pytest.raises(FilaOP156Invalida, match="Estado OP156 inválido"):
            OP156Queue(cliente).aguardar_download_id(
                OPCAO, "CWB", ignorar_ids=set(), estrito=True,
            )
    contexto = diagnosticos(caplog)[-1]
    assert len(contexto["situacao_original"]) == 80
    assert len(contexto["acao_original"]) == 80
    assert "\n" not in caplog.records[-1].getMessage()


def test_lista_invalida_registra_apenas_motivo_controlado(caplog):
    cliente = ClienteFalso(["<html>Cookie: segredo</html>"])
    with caplog.at_level(logging.WARNING, logger="operations.op156.queue"):
        with pytest.raises(FilaOP156Invalida, match="Lista OP156"):
            OP156Queue(cliente).aguardar_download_id(
                OPCAO, "CWB", ignorar_ids=set(), estrito=True,
            )
    assert diagnosticos(caplog)[-1] == {"motivo": "lista_op156_invalida"}
    assert "segredo" not in caplog.records[-1].getMessage()


def test_falha_do_logger_preserva_excecao_original(monkeypatch):
    def logger_indisponivel(*args, **kwargs):
        raise RuntimeError("logger indisponível")

    monkeypatch.setattr(modulo_fila.logger, "warning", logger_indisponivel)
    cliente = ClienteFalso([linha("897436", estado="Processando", acao="Baixar")])

    with pytest.raises(FilaOP156Invalida, match="Estado OP156 inválido"):
        OP156Queue(cliente).aguardar_download_id(
            OPCAO, "CWB", ignorar_ids=set(), estrito=True,
        )


@pytest.mark.parametrize("html", [
    "<html>login</html>",
    "<r><f0>897142</f0>",
    linha("897142").replace("<f0>897142</f0>", ""),
    linha("897142").replace("<f0>897142</f0>", "<f0>897142</f0><f0>897142</f0>"),
    linha("٨٩٧١٤٢"),
])
def test_fila_malformada_falha_sem_inferir_lista_vazia(html):
    with pytest.raises(FilaOP156Invalida):
        OP156Queue(ClienteFalso()).extrair_jobs(
            html, OPCAO, "CWB", estrito=True,
        )


def test_download_valido_usa_ssw0424_e_arquivo_seguro(tmp_path):
    nome = "CSVROD00897142.sswweb"
    dow = resposta(text=f"abrir('{nome}','{nome}',1,1,'/usr/aws/jobs/ROD/',1)")
    arquivo = resposta(
        content=(b"Relatorio OP455\nCLIENTE PAGADOR;UNIDADE EMISSORA;"
                 b"SERIE/NUMERO CTRC;CIDADE DE ENTREGA\nTESTE;CWB;CWB1;CURITIBA"),
        tipo="application/ssw",
    )
    cliente = ClienteFalso(dow=dow, arquivo=arquivo)

    caminho = OP156Queue(cliente).baixar_arquivo("897142", tmp_path, estrito=True)

    assert caminho == tmp_path / nome
    assert caminho.read_bytes() == arquivo.content
    assert cliente.chamadas[0][2]["act"] == "DOW897142"
    assert cliente.chamadas[0][3] == 1
    assert cliente.chamadas[1][2] == {
        "act": nome, "filename": nome, "path": "/usr/aws/jobs/ROD/",
        "down": "1", "nw": "1",
    }
    assert cliente.chamadas[1][3] == 1


@pytest.mark.parametrize("arquivo", [
    resposta(content=b"<html>login</html>", tipo="text/html"),
    resposta(content=b"", tipo="application/ssw"),
    resposta(content=b"<html>erro</html>", tipo="application/ssw"),
    resposta(content=b"abc", tipo="application/ssw"),
    resposta(content=b"Relatorio\nA;B\n1;2", tipo="application/ssw"),
    resposta(content=b"A\nB;C", tipo="application/ssw", status=500),
])
def test_download_invalido_nao_grava_arquivo(tmp_path, arquivo):
    nome = "CSVROD00897142.sswweb"
    dow = resposta(text=f"abrir('{nome}','{nome}',1,1,'/usr/aws/jobs/ROD/',1)")
    cliente = ClienteFalso(dow=dow, arquivo=arquivo)
    with pytest.raises((FilaOP156Invalida, RuntimeError)):
        OP156Queue(cliente).baixar_arquivo("897142", tmp_path, estrito=True)
    assert list(tmp_path.iterdir()) == []


def test_nome_inseguro_interrompe_antes_do_get(tmp_path):
    dow = resposta(text="abrir('../x.sswweb','../x.sswweb',1,1,'/usr/aws/jobs/ROD/',1)")
    cliente = ClienteFalso(dow=dow)
    with pytest.raises(FilaOP156Invalida):
        OP156Queue(cliente).baixar_arquivo("897142", tmp_path, estrito=True)
    assert not any(chamada[0] == "get" for chamada in cliente.chamadas)


def test_nome_de_outra_sequencia_interrompe_antes_do_get(tmp_path):
    nome = "CSVROD00897080.sswweb"
    dow = resposta(text=f"abrir('{nome}','{nome}',1,1,'/usr/aws/jobs/ROD/',1)")
    cliente = ClienteFalso(dow=dow)
    with pytest.raises(FilaOP156Invalida):
        OP156Queue(cliente).baixar_arquivo("897142", tmp_path, estrito=True)
    assert not any(chamada[0] == "get" for chamada in cliente.chamadas)


def test_arquivo_local_existente_nao_e_sobrescrito(tmp_path):
    nome = "CSVROD00897142.sswweb"
    destino = tmp_path / nome
    destino.write_bytes(b"conteudo anterior")
    dow = resposta(text=f"abrir('{nome}','{nome}',1,1,'/usr/aws/jobs/ROD/',1)")
    arquivo = resposta(
        content=(b"Relatorio\nCLIENTE PAGADOR;UNIDADE EMISSORA;"
                 b"SERIE/NUMERO CTRC;CIDADE DE ENTREGA\nTESTE;CWB;CWB1;CURITIBA"),
        tipo="application/ssw",
    )
    cliente = ClienteFalso(dow=dow, arquivo=arquivo)
    with pytest.raises(FilaOP156Invalida, match="já existe"):
        OP156Queue(cliente).baixar_arquivo("897142", tmp_path, estrito=True)
    assert destino.read_bytes() == b"conteudo anterior"
