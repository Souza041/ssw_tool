"""Testes por mocks do verificador consultivo; nenhuma conexão real."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from prototypes.oc73_laboratorio_verificador import (
    ConfiguracaoLaboratorio,
    LaboratorioRejeitado,
    SQL_GRANTS,
    SQL_IDENTIDADE,
    SQL_PERMITIDO,
    SQL_TABELAS,
    _interpretar_grant,
    construir_configuracao,
    validar_configuracao,
    verificar_laboratorio,
)


UUID = "11111111-2222-4333-8444-555555555555"
DATADIR = "C:/mysql57-lab/data/"
CONTA = "oc73_tester@127.0.0.1"


def config(**mudancas):
    dados = {
        "host": "127.0.0.1",
        "porta": 3308,
        "schema": "oc73_autorizacao_transacional_test",
        "usuario": "oc73_tester",
        "conta_efetiva_esperada": CONTA,
        "usuario_conexao_esperado": CONTA,
        "server_uuid_esperado": UUID,
        "datadir_esperado": DATADIR,
        "versao_esperada": "5.7.32-log",
        "distribuicao_esperada": "MySQL Community Server (GPL)",
        "hostname_esperado": "mysql57-lab",
        "perfil_privilegios": "consultivo",
        "autorizacao_explicita": True,
        "unix_socket": None,
    }
    dados.update(mudancas)
    return ConfiguracaoLaboratorio(**dados)


class CursorFalso:
    def __init__(self, identidade=None, tabelas=None, grants=None, falhar=None):
        self.identidade = identidade or {
            "schema_efetivo": "oc73_autorizacao_transacional_test",
            "hostname": "mysql57-lab",
            "porta": 3308,
            "versao": "5.7.32-log",
            "comentario_versao": "MySQL Community Server (GPL)",
            "server_uuid": UUID,
            "datadir": DATADIR,
            "usuario_efetivo": CONTA,
            "usuario_conexao": CONTA,
        }
        self.tabelas = tabelas or []
        self.grants = grants or [
            ("GRANT USAGE ON *.* TO 'oc73_tester'@'127.0.0.1'",),
            (
                "GRANT SELECT ON `oc73_autorizacao_transacional_test`.* "
                "TO 'oc73_tester'@'127.0.0.1'",
            ),
        ]
        self.falhar = falhar
        self.executadas = []
        self.atual = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, parametros=None):
        if self.falhar == sql:
            raise OSError("segredo-nao-pode-vazar")
        self.executadas.append((sql, parametros))
        self.atual = sql

    def fetchone(self):
        return self.identidade if self.atual == SQL_IDENTIDADE else None

    def fetchall(self):
        if self.atual == SQL_TABELAS:
            return [{"table_name": nome} for nome in self.tabelas]
        if self.atual == SQL_GRANTS:
            return self.grants
        return []


class ConexaoFalsa:
    def __init__(self, cursor=None):
        self.cursor_obj = cursor or CursorFalso()
        self.rollback_count = 0
        self.close_count = 0

    def cursor(self):
        return self.cursor_obj

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.close_count += 1


def test_verificador_executa_somente_inventario_fixo_e_fecha_conexao():
    conexao = ConexaoFalsa(
        CursorFalso(tabelas=["oc73_lab_tentativas", "oc73_lab_autorizacoes"])
    )
    relatorio = verificar_laboratorio(config(), lambda: conexao)
    assert relatorio.identidade_validada is True
    assert relatorio.versao == "5.7.32-log"
    assert {sql for sql, _ in conexao.cursor_obj.executadas} == SQL_PERMITIDO
    assert conexao.rollback_count == 1
    assert conexao.close_count == 1


@pytest.mark.parametrize(
    "mudancas",
    [
        {"host": "localhost"},
        {"host": "10.0.0.1"},
        {"porta": 3306},
        {"porta": "3308"},
        {"porta": True},
        {"schema": "automationx"},
        {"usuario": "usuario_operacional"},
        {"unix_socket": "/var/run/mysqld.sock"},
        {"unix_socket": ""},
        {"autorizacao_explicita": False},
        {"server_uuid_esperado": ""},
        {"server_uuid_esperado": "uuid-invalido"},
        {"datadir_esperado": ""},
        {"datadir_esperado": "relative/data/"},
    ],
)
def test_configuracao_perigosa_falha_antes_da_fabrica(mudancas):
    chamadas = []
    with pytest.raises(LaboratorioRejeitado):
        verificar_laboratorio(
            config(**mudancas),
            lambda: chamadas.append(True),
        )
    assert chamadas == []


def test_parametro_dsn_inesperado_e_rejeitado():
    valores = config().__dict__ | {"ssl_disabled": True}
    with pytest.raises(LaboratorioRejeitado, match="inesperados"):
        construir_configuracao(valores)


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("schema_efetivo", "outro"),
        ("porta", 3307),
        ("usuario_efetivo", "oc73_tester@localhost"),
        ("usuario_conexao", "oc73_tester@localhost"),
        ("server_uuid", "uuid-divergente"),
        ("datadir", "C:/outro/data/"),
        ("versao", "8.0.40"),
        ("versao", "5.7.32-MariaDB"),
        ("comentario_versao", "MariaDB Server"),
        ("hostname", "outro-host"),
    ],
)
def test_identidade_efetiva_divergente_e_rejeitada(campo, valor):
    cursor = CursorFalso()
    cursor.identidade[campo] = valor
    conexao = ConexaoFalsa(cursor)
    with pytest.raises(LaboratorioRejeitado, match="divergente"):
        verificar_laboratorio(config(), lambda: conexao)
    assert conexao.rollback_count == 1
    assert conexao.close_count == 1


def test_tabela_fora_do_escopo_e_rejeitada():
    conexao = ConexaoFalsa(CursorFalso(tabelas=["op535_cnpj_cache"]))
    with pytest.raises(LaboratorioRejeitado, match="fora do escopo"):
        verificar_laboratorio(config(), lambda: conexao)


@pytest.mark.parametrize(
    "grant",
    [
        "GRANT SELECT ON *.* TO 'oc73_tester'@'127.0.0.1'",
        "GRANT ALL PRIVILEGES ON *.* TO 'oc73_tester'@'127.0.0.1'",
        (
            "GRANT SELECT ON `oc73_autorizacao_transacional_test`.* "
            "TO 'oc73_tester'@'127.0.0.1' WITH GRANT OPTION"
        ),
        "GRANT SELECT ON `automationx`.* TO 'oc73_tester'@'127.0.0.1'",
        "GRANT SELECT ON `outro_schema`.* TO 'oc73_tester'@'127.0.0.1'",
        (
            "GRANT MYSTERY ON `oc73_autorizacao_transacional_test`.* "
            "TO 'oc73_tester'@'127.0.0.1'"
        ),
        "GRANT 'papel_indireto'@'%' TO 'oc73_tester'@'127.0.0.1'",
        (
            "GRANT SELECT ON `oc73_autorizacao_transacional_test`.* "
            "TO 'oc73_tester'@'localhost'"
        ),
    ],
)
def test_grants_excessivos_desconhecidos_ou_de_outro_escopo_falham(grant):
    conexao = ConexaoFalsa(CursorFalso(grants=[(grant,)]))
    with pytest.raises(LaboratorioRejeitado):
        verificar_laboratorio(config(), lambda: conexao)
    assert conexao.close_count == 1


@pytest.mark.parametrize("identificador", ["UPDATE_LOG", "CREATE_X", "DROP_X", "FILE_X"])
def test_identificador_com_palavra_de_privilegio_e_interpretado_sem_substring(identificador):
    grant = f"GRANT SELECT ON `SCHEMA_UPDATE`.`{identificador}` TO 'u'@'h'"
    correspondencia, privilegios = _interpretar_grant(grant)
    assert correspondencia.group("escopo") == f"`SCHEMA_UPDATE`.`{identificador}`"
    assert privilegios == {"SELECT"}


@pytest.mark.parametrize(
    "grant",
    [
        "GRANT SELECT, SELECT ON `x`.* TO 'u'@'h'",
        "GRANT SELECT ON `x`.* TO 'u'@'h' -- comentario",
        "GRANT SELECT ON `x`.* TO 'u'@'h' SUFIXO",
        "GRANT  SELECT ON `x`.* TO 'u'@'h'",
        "GRANT SELECT, ON `x`.* TO 'u'@'h'",
        "GRANT UNKNOWN ON `x`.* TO 'u'@'h'",
        "GRANT CREATE USER ON *.* TO 'u'@'h'",
    ],
)
def test_gramatica_rejeita_repeticao_comentario_whitespace_e_sufixo(grant):
    with pytest.raises(LaboratorioRejeitado):
        _interpretar_grant(grant)


def test_grant_duplicado_e_rejeitado():
    grant = (
        "GRANT SELECT ON `oc73_autorizacao_transacional_test`.* "
        "TO 'oc73_tester'@'127.0.0.1'"
    )
    conexao = ConexaoFalsa(CursorFalso(grants=[(grant,), (grant,)]))
    with pytest.raises(LaboratorioRejeitado, match="duplicado"):
        verificar_laboratorio(config(), lambda: conexao)


def test_perfil_transacional_exige_apenas_select_insert_update():
    grants = [
        ("GRANT USAGE ON *.* TO 'oc73_tester'@'127.0.0.1'",),
        (
            "GRANT SELECT ON "
            "`oc73_autorizacao_transacional_test`.* "
            "TO 'oc73_tester'@'127.0.0.1'",
        ),
        *[
            (
                f"GRANT {privilegios} ON "
                "`oc73_autorizacao_transacional_test`."
                f"`{tabela}` TO 'oc73_tester'@'127.0.0.1'",
            )
            for tabela, privilegios in (
                ("oc73_lab_tentativas", "INSERT, UPDATE"),
                ("oc73_lab_autorizacoes", "INSERT, UPDATE"),
                ("oc73_lab_aprovacoes", "INSERT"),
                ("oc73_lab_consumos", "INSERT"),
                ("oc73_lab_auditoria", "INSERT"),
            )
        ],
    ]
    conexao = ConexaoFalsa(CursorFalso(grants=grants))
    resultado = verificar_laboratorio(
        config(perfil_privilegios="transacional"), lambda: conexao
    )
    assert resultado.grants_validados == 7


def test_perfil_transacional_rejeita_ddl_e_delete():
    grant = (
        "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE ON "
        "`oc73_autorizacao_transacional_test`.* "
        "TO 'oc73_tester'@'127.0.0.1'"
    )
    conexao = ConexaoFalsa(CursorFalso(grants=[(grant,)]))
    with pytest.raises(LaboratorioRejeitado):
        verificar_laboratorio(
            config(perfil_privilegios="transacional"), lambda: conexao
        )


def test_erro_de_conexao_falha_sanitizado_sem_segredo():
    def falhar():
        raise ConnectionError("password=segredo-nao-pode-vazar")

    with pytest.raises(LaboratorioRejeitado) as capturada:
        verificar_laboratorio(config(), falhar)
    assert "segredo-nao-pode-vazar" not in str(capturada.value)
    assert "password" not in str(capturada.value)
    assert capturada.value.__cause__ is None


def test_erro_de_consulta_faz_rollback_fecha_e_nao_vaza_detalhe():
    conexao = ConexaoFalsa(CursorFalso(falhar=SQL_TABELAS))
    with pytest.raises(LaboratorioRejeitado) as capturada:
        verificar_laboratorio(config(), lambda: conexao)
    assert "segredo-nao-pode-vazar" not in str(capturada.value)
    assert conexao.rollback_count == 1
    assert conexao.close_count == 1


def test_sql_fixo_e_estritamente_consultivo():
    proibidos = (
        "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER",
        "DROP", "TRUNCATE", "CALL", "GET_LOCK", "RELEASE_LOCK", ";",
    )
    for sql in SQL_PERMITIDO:
        normalizado = " ".join(sql.upper().split())
        assert normalizado.startswith(("SELECT ", "SHOW "))
        assert not any(item in normalizado for item in proibidos)


def test_verificador_nao_importa_runtime_driver_rede_ou_ambiente():
    caminho = Path("prototypes/oc73_laboratorio_verificador.py")
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    importacoes = {
        alias.name
        for no in ast.walk(arvore)
        if isinstance(no, ast.Import)
        for alias in no.names
    }
    importacoes.update(
        no.module
        for no in ast.walk(arvore)
        if isinstance(no, ast.ImportFrom) and no.module
    )
    proibidos = (
        "modules", "operations", "web", "ssw", "pymysql", "requests",
        "socket", "os", "dotenv",
    )
    assert not any(nome.startswith(proibidos) for nome in importacoes)


def test_ddl_experimental_nao_esta_em_migrations_e_so_contem_tabelas_lab():
    caminho = Path("prototypes/sql/oc73_autorizacao_lab_mysql57.sql")
    texto = caminho.read_text(encoding="utf-8")
    assert "prototypes" in caminho.parts
    assert "migrations" not in caminho.parts
    assert "oc73_execucoes" not in texto
    assert "oc73_tentativas" not in texto
    assert "op535_cnpj_cache" not in texto
    assert "CHECK" not in texto.upper()
    assert texto.upper().count("CREATE TABLE OC73_LAB_") == 5
    assert "UNIQUE KEY uq_oc73_lab_tentativa_contexto" in texto
    assert "UNIQUE KEY uq_oc73_lab_auth_contexto" in texto
    assert "FOREIGN KEY (autorizacao_id, tentativa_id)" in texto
    assert "FOREIGN KEY (tentativa_id, referencia, revisao_codigo)" in texto
