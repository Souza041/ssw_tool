from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from modules.ocorrencia_73 import observabilidade
from modules.ocorrencia_73 import routes as rotas_oc73
from scripts.diagnostico_oc73_mysql import (
    validar_laboratorio_lock,
    validar_privilegios_somente_leitura,
    validar_sql_somente_leitura,
)
from web.session_store import (
    CLIENTS,
    SESSOES_CRIADAS_EM,
    criar_sessao,
    obter_client,
    remover_sessao,
)
from web.routes import router as web_router


class CursorFalso:
    def __init__(self, respostas):
        self.respostas = iter(respostas)
        self.atual = []
        self.execucoes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, parametros=()):
        self.execucoes.append((sql, parametros))
        self.atual = next(self.respostas)

    def fetchone(self):
        return self.atual[0] if self.atual else None

    def fetchall(self):
        return self.atual


class ConexaoFalsa:
    def __init__(self, respostas):
        self.cursor_falso = CursorFalso(respostas)
        self.fechada = False
        self.commit_chamado = False

    def cursor(self):
        return self.cursor_falso

    def commit(self):
        self.commit_chamado = True

    def close(self):
        self.fechada = True


def request_com_sessao(session_id="sessao-teste"):
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/oc73/observabilidade",
        "headers": [],
        "query_string": b"",
        "session": {"ssw_session_id": session_id},
    })


def registrar_cliente(usuario):
    cliente = SimpleNamespace(usuario=usuario)
    return criar_sessao(cliente)


def remover_cliente(session_id):
    CLIENTS.pop(session_id, None)
    SESSOES_CRIADAS_EM.pop(session_id, None)


def execucao_fake(**sobrescrever):
    item = {
        "id": 9,
        "data_referencia": date(2026, 10, 15),
        "triggered_by": "scheduler:CWB",
        "status": "concluido",
        "dry_run": True,
        "iniciado_em": datetime(2026, 10, 15, 23, 30),
        "finalizado_em": datetime(2026, 10, 15, 23, 31),
        "total_relatorio": 10,
        "total_filtrado": 2,
        "total_lancado": 0,
        "total_erros": 0,
        "duracao_segundos": 60,
    }
    item.update(sobrescrever)
    return item


def test_estado_seguranca_desabilitado_por_padrao(monkeypatch):
    monkeypatch.delenv("OCORRENCIA_73_ENABLED", raising=False)
    monkeypatch.setattr("modules.ocorrencia_73.config.DRY_RUN", True)
    monkeypatch.setattr(
        "modules.ocorrencia_73.service.OC73_ENVIO_HABILITADO",
        False,
    )

    assert observabilidade.estado_seguranca_oc73() == {
        "scheduler_configurado": False,
        "scheduler_habilitado": False,
        "dry_run": True,
        "gate_envio_habilitado": False,
    }


def test_proximas_execucoes_sao_teoricas_com_scheduler_desligado(monkeypatch):
    monkeypatch.setenv("OCORRENCIA_73_ENABLED", "false")
    agora = datetime(2026, 10, 15, 23, 31, tzinfo=observabilidade.TIMEZONE)

    proximas = observabilidade.proximas_execucoes_teoricas(agora)

    assert {item["fluxo"] for item in proximas} == {"CWB", "BIG"}
    assert all(item["job_ativo"] is False for item in proximas)
    assert all("teórica" in item["rotulo"] for item in proximas)
    por_fluxo = {item["fluxo"]: item["proxima_execucao"] for item in proximas}
    assert por_fluxo["CWB"] == datetime(
        2026, 10, 16, 23, 30, tzinfo=observabilidade.TIMEZONE
    )
    assert por_fluxo["BIG"] == datetime(
        2026, 10, 19, 23, 30, tzinfo=observabilidade.TIMEZONE
    )


def test_historico_parametrizado_paginado_e_somente_select():
    conexao = ConexaoFalsa([
        [{"total": 1}],
        [{
            "id": 9,
            "iniciado_em": datetime(2026, 10, 15, 23, 30),
            "finalizado_em": datetime(2026, 10, 15, 23, 31),
        }],
    ])
    repo = observabilidade.Ocorrencia73ObservabilidadeRepository(
        connection_factory=lambda: conexao
    )

    resultado = repo.listar_execucoes(
        pagina=2,
        por_pagina=500,
        fluxo="CWB",
        status="concluido",
        data_inicio=date(2026, 10, 1),
        data_fim=date(2026, 10, 31),
    )

    assert resultado["por_pagina"] == 100
    assert resultado["itens"][0]["duracao_segundos"] == 60
    assert conexao.fechada is True
    assert conexao.commit_chamado is False
    assert len(conexao.cursor_falso.execucoes) == 2
    for sql, _ in conexao.cursor_falso.execucoes:
        assert sql.lstrip().upper().startswith("SELECT ")
    _, parametros = conexao.cursor_falso.execucoes[1]
    assert parametros == (
        "scheduler:CWB",
        "concluido",
        date(2026, 10, 1),
        date(2026, 10, 31),
        100,
        100,
    )


def test_tentativas_exigem_execucao_e_possuem_limite():
    conexao = ConexaoFalsa([[{"total": 0}], []])
    repo = observabilidade.Ocorrencia73ObservabilidadeRepository(
        connection_factory=lambda: conexao
    )

    resultado = repo.listar_tentativas(77, pagina=1, por_pagina=1000)

    assert resultado["por_pagina"] == 100
    assert all(
        parametros[0] == 77
        for _, parametros in conexao.cursor_falso.execucoes
    )
    assert "LIMIT %s OFFSET %s" in conexao.cursor_falso.execucoes[1][0]


@pytest.mark.parametrize("pagina", [0, -1, 1001, 10**12, 1.5, True])
def test_paginacao_recusa_paginas_fora_do_limite(pagina):
    repo = observabilidade.Ocorrencia73ObservabilidadeRepository(
        connection_factory=lambda: (_ for _ in ()).throw(
            AssertionError("não deveria conectar")
        )
    )
    with pytest.raises(ValueError):
        repo.listar_execucoes(pagina=pagina)


@pytest.mark.parametrize("execucao_id", [0, -1, True, 1.5, "1", "invalido"])
def test_ids_invalidos_sao_recusados_antes_da_conexao(execucao_id):
    repo = observabilidade.Ocorrencia73ObservabilidadeRepository(
        connection_factory=lambda: (_ for _ in ()).throw(
            AssertionError("não deveria conectar")
        )
    )
    with pytest.raises(ValueError, match="ID de execução inválido"):
        repo.obter_execucao(execucao_id)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE oc73_execucoes SET status='x'",
        "SELECT * FROM oc73_execucoes; DELETE FROM oc73_execucoes",
        "SELECT * FROM oc73_execucoes FOR UPDATE",
    ],
)
def test_guard_observabilidade_recusa_escrita_e_lock(sql):
    with pytest.raises(ValueError):
        observabilidade._validar_select(sql)


def test_guard_diagnostico_recusa_dml_e_multiplas_instrucoes():
    validar_sql_somente_leitura("SHOW INDEX FROM oc73_execucoes")
    validar_sql_somente_leitura("SHOW CREATE TABLE oc73_execucoes")
    validar_sql_somente_leitura("SELECT VERSION()")
    with pytest.raises(ValueError):
        validar_sql_somente_leitura("DELETE FROM oc73_execucoes")
    with pytest.raises(ValueError):
        validar_sql_somente_leitura("SELECT 1; DROP TABLE oc73_execucoes")


def test_diagnostico_aceita_somente_grants_de_leitura():
    validar_privilegios_somente_leitura([
        {"grant": "GRANT USAGE ON *.* TO 'auditor'@'127.0.0.1'"},
        {
            "grant": (
                "GRANT SELECT, SHOW VIEW ON `automationx`.* "
                "TO 'auditor'@'127.0.0.1'"
            )
        },
    ])


@pytest.mark.parametrize(
    "grant",
    [
        "GRANT ALL PRIVILEGES ON *.* TO 'auditor'@'127.0.0.1'",
        "GRANT SELECT, INSERT ON `automationx`.* TO 'auditor'@'127.0.0.1'",
        "GRANT SELECT ON `automationx`.* TO 'auditor'@'127.0.0.1' WITH GRANT OPTION",
    ],
)
def test_diagnostico_recusa_grant_mutavel_sem_expor_conteudo(grant):
    with pytest.raises(RuntimeError) as erro:
        validar_privilegios_somente_leitura([{"grant": grant}])

    assert "auditor" not in str(erro.value)
    assert "automationx" not in str(erro.value)


def test_advisory_lock_diagnostico_recusa_banco_operacional(monkeypatch):
    monkeypatch.setenv("OC73_DIAGNOSTICO_DB_HOST", "servidor-producao")
    monkeypatch.setenv("OC73_DIAGNOSTICO_DB_PORT", "3306")
    monkeypatch.setenv("OC73_DIAGNOSTICO_DB_NAME", "automationx")
    monkeypatch.setenv("OC73_DIAGNOSTICO_DB_USER", "app")

    with pytest.raises(RuntimeError, match="somente no laboratório"):
        validar_laboratorio_lock({
            "porta": 3306,
            "banco": "automationx",
            "usuario": "app@%",
            "diretorio": "/var/lib/mysql",
        })


def test_acesso_exige_sessao_e_allowlist(monkeypatch):
    session_id = registrar_cliente("AUDITOR.OC73")
    monkeypatch.setenv("OC73_OBSERVABILIDADE_USUARIOS", "AUDITOR.OC73")
    try:
        assert (
            rotas_oc73._exigir_acesso_observabilidade(
                request_com_sessao(session_id)
            )
            is None
        )
    finally:
        remover_cliente(session_id)


def test_allowlist_normaliza_mas_nao_aceita_correspondencia_parcial(monkeypatch):
    monkeypatch.setenv(
        "OC73_OBSERVABILIDADE_USUARIOS",
        " auditor.oc73 , OUTRO ",
    )
    assert observabilidade.usuario_pode_observar(" AUDITOR.OC73 ") is True
    assert observabilidade.usuario_pode_observar("AUDITOR") is False
    assert observabilidade.usuario_pode_observar("AUDITOR.OC73.EXTRA") is False


def test_acesso_negado_por_padrao(monkeypatch):
    session_id = registrar_cliente("QUALQUER")
    monkeypatch.delenv("OC73_OBSERVABILIDADE_USUARIOS", raising=False)
    try:
        with pytest.raises(HTTPException) as erro:
            rotas_oc73._exigir_acesso_observabilidade(
                request_com_sessao(session_id)
            )
        assert erro.value.status_code == 403
    finally:
        remover_cliente(session_id)


def test_sem_sessao_redireciona_login():
    request = Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [], "query_string": b"", "session": {},
    })
    resposta = rotas_oc73._exigir_acesso_observabilidade(request)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/login"


def test_indicador_antigo_sem_cliente_nao_autoriza_e_limpa_sessao(monkeypatch):
    monkeypatch.setenv("OC73_OBSERVABILIDADE_USUARIOS", "AUDITOR")
    request = Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [], "query_string": b"",
        "session": {
            "ssw_session_id": "sessao-expirada",
            "oc73_observabilidade": True,
        },
    })

    resposta = rotas_oc73._exigir_acesso_observabilidade(request)

    assert resposta.status_code == 303
    assert request.session == {}


def test_sessao_expirada_no_servidor_e_inutilizavel(monkeypatch):
    session_id = registrar_cliente("AUDITOR")
    request = request_com_sessao(session_id)
    SESSOES_CRIADAS_EM[session_id] = 10.0
    monkeypatch.setattr(
        "web.session_store.time.monotonic",
        lambda: 10.0 + (8 * 60 * 60) + 1,
    )

    assert obter_client(request) is None
    assert request.session == {}
    assert session_id not in CLIENTS
    assert session_id not in SESSOES_CRIADAS_EM


def test_troca_de_usuario_revalida_allowlist(monkeypatch):
    session_id = registrar_cliente("AUTORIZADO")
    monkeypatch.setenv("OC73_OBSERVABILIDADE_USUARIOS", "AUTORIZADO")
    request = request_com_sessao(session_id)
    try:
        assert rotas_oc73._exigir_acesso_observabilidade(request) is None
        CLIENTS[session_id] = SimpleNamespace(usuario="NAO-AUTORIZADO")
        with pytest.raises(HTTPException) as erro:
            rotas_oc73._exigir_acesso_observabilidade(request)
        assert erro.value.status_code == 403
    finally:
        remover_cliente(session_id)


def test_logout_invalida_acesso_mesmo_com_indicador_antigo(monkeypatch):
    session_id = registrar_cliente("AUDITOR")
    monkeypatch.setenv("OC73_OBSERVABILIDADE_USUARIOS", "AUDITOR")
    request = request_com_sessao(session_id)

    remover_sessao(request)
    resposta = rotas_oc73._exigir_acesso_observabilidade(request)

    assert resposta.status_code == 303
    assert session_id not in CLIENTS


def test_acesso_direto_ao_detalhe_revalida_antes_do_banco(monkeypatch):
    session_id = registrar_cliente("SEM-PERMISSAO")
    monkeypatch.setenv("OC73_OBSERVABILIDADE_USUARIOS", "AUDITOR")
    monkeypatch.setattr(
        rotas_oc73,
        "Ocorrencia73ObservabilidadeRepository",
        lambda: (_ for _ in ()).throw(AssertionError("banco acessado")),
    )
    try:
        with pytest.raises(HTTPException) as erro:
            rotas_oc73.detalhe_execucao(
                request_com_sessao(session_id),
                execucao_id=1,
                pagina=1,
                por_pagina=25,
            )
        assert erro.value.status_code == 403
    finally:
        remover_cliente(session_id)


def test_falha_banco_retorna_painel_sem_acao(monkeypatch, caplog):
    session_id = registrar_cliente("AUDITOR")
    monkeypatch.setenv("OC73_OBSERVABILIDADE_USUARIOS", "AUDITOR")
    monkeypatch.setenv("OCORRENCIA_73_ENABLED", "false")
    monkeypatch.setenv("OCORRENCIA_73_DRY_RUN", "true")
    monkeypatch.setenv("OC73_ENVIO_HABILITADO", "NAO")

    class RepoIndisponivel:
        def listar_execucoes(self, **kwargs):
            raise ConnectionError("senha-secreta-nunca-deve-ir-para-o-html")

    monkeypatch.setattr(
        rotas_oc73,
        "Ocorrencia73ObservabilidadeRepository",
        RepoIndisponivel,
    )
    try:
        resposta = rotas_oc73.painel_observabilidade(
            request_com_sessao(session_id),
            pagina=1,
            por_pagina=25,
            fluxo=None,
            status=None,
            data_inicio=None,
            data_fim=None,
        )
        assert resposta.status_code == 503
        assert b"senha-secreta" not in resposta.body
        assert b"Nenhuma a" in resposta.body
        assert "senha-secreta" not in caplog.text
    finally:
        remover_cliente(session_id)


def test_templates_escapam_dados_originados_do_banco(monkeypatch):
    session_id = registrar_cliente("AUDITOR")
    monkeypatch.setenv("OC73_OBSERVABILIDADE_USUARIOS", "AUDITOR")
    payload = "<script>alert('xss')</script>"

    class RepoXss:
        def listar_execucoes(self, **kwargs):
            return {
                "itens": [execucao_fake(triggered_by=payload)],
                "total": 1, "pagina": 1, "por_pagina": 25,
                "total_paginas": 1,
            }

        def ultimas_execucoes_por_fluxo(self):
            return {"CWB": execucao_fake(status=payload), "BIG": None}

        def execucoes_anormais(self, minutos):
            return []

    monkeypatch.setattr(
        rotas_oc73,
        "Ocorrencia73ObservabilidadeRepository",
        RepoXss,
    )
    try:
        resposta = rotas_oc73.painel_observabilidade(
            request_com_sessao(session_id),
            pagina=1,
            por_pagina=25,
            fluxo=None,
            status=None,
            data_inicio=None,
            data_fim=None,
        )
        assert payload.encode() not in resposta.body
        assert b"&lt;script&gt;" in resposta.body
    finally:
        remover_cliente(session_id)


def test_fluxos_observabilidade_nao_criam_cliente_ssw(monkeypatch):
    chamado = False

    def post_proibido(*args, **kwargs):
        nonlocal chamado
        chamado = True
        raise AssertionError("POST SSW não permitido")

    monkeypatch.setattr("ssw.client.SSWClient.post", post_proibido)
    observabilidade.estado_seguranca_oc73()
    observabilidade.proximas_execucoes_teoricas(
        datetime(2026, 10, 15, 12, tzinfo=observabilidade.TIMEZONE)
    )
    assert chamado is False


def test_rotas_observabilidade_sao_exclusivamente_get():
    caminhos = {
        rota.path: rota.methods
        for rota in rotas_oc73.router.routes
    }
    assert caminhos == {
        "/oc73/observabilidade": {"GET"},
        "/oc73/observabilidade/execucoes/{execucao_id}": {"GET"},
    }


def test_existe_uma_unica_rota_logout():
    rotas_logout = [
        rota for rota in web_router.routes if rota.path == "/logout"
    ]
    assert len(rotas_logout) == 1
