import ast
import json
import re
from pathlib import Path

import pytest
import pymysql

from scripts import diagnostico_oc73_operacional_readonly as diagnostico


UUID_ESPERADO = "12345678-1234-5678-9abc-1234567890ab"


class CursorFalso:
    def __init__(self, respostas):
        self.respostas = respostas
        self.atual = []
        self.executadas = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.executadas.append(sql)
        if sql not in self.respostas:
            raise AssertionError(f"SQL não previsto no teste: {sql}")
        resposta = self.respostas[sql]
        if isinstance(resposta, Exception):
            raise resposta
        self.atual = resposta

    def fetchall(self):
        return self.atual


class ConexaoFalsa:
    def __init__(self, respostas):
        self.cursor_falso = CursorFalso(respostas)
        self.rollback_chamado = False
        self.close_chamado = False

    def cursor(self):
        return self.cursor_falso

    def rollback(self):
        self.rollback_chamado = True

    def close(self):
        self.close_chamado = True


def config(password="segredo-que-nao-pode-vazar"):
    return diagnostico.ConfiguracaoBanco(
        host="mysql-operacional.interno",
        port=3306,
        database="automationx",
        user="conta-operacional",
        password=password,
    )


def identidade(**alteracoes):
    item = {
        "versao": "5.7.32-log",
        "distribuicao": "MySQL Community Server (GPL)",
        "server_uuid": UUID_ESPERADO,
        "hostname": "mysql-prod-01",
        "porta": 3306,
        "banco": "automationx",
    }
    item.update(alteracoes)
    return item


def coluna(
    tabela,
    nome,
    tipo,
    *,
    nullable="NO",
    default=None,
    extra="",
    collation=None,
):
    return {
        "TABLE_NAME": tabela,
        "COLUMN_NAME": nome,
        "COLUMN_TYPE": tipo,
        "IS_NULLABLE": nullable,
        "COLUMN_DEFAULT": default,
        "EXTRA": extra,
        "CHARACTER_SET_NAME": "utf8mb4" if collation else None,
        "COLLATION_NAME": collation,
        "GENERATION_EXPRESSION": "",
    }


def colunas_validas():
    e = "oc73_execucoes"
    t = "oc73_tentativas"
    return [
        coluna(e, "id", "bigint(20) unsigned", extra="auto_increment"),
        coluna(e, "data_referencia", "date"),
        coluna(e, "triggered_by", "varchar(32)", collation="utf8mb4_unicode_ci"),
        coluna(e, "status", "varchar(32)", collation="utf8mb4_unicode_ci"),
        coluna(e, "dry_run", "tinyint(1)"),
        coluna(e, "iniciado_em", "datetime"),
        coluna(e, "finalizado_em", "datetime", nullable="YES"),
        coluna(e, "total_relatorio", "int(11)", default="0"),
        coluna(e, "total_filtrado", "int(11)", default="0"),
        coluna(e, "total_lancado", "int(11)", default="0"),
        coluna(e, "total_erros", "int(11)", default="0"),
        coluna(e, "mensagem_erro", "text", nullable="YES", collation="utf8mb4_unicode_ci"),
        coluna(t, "id", "bigint(20) unsigned", extra="auto_increment"),
        coluna(t, "execucao_id", "bigint(20) unsigned"),
        coluna(t, "serie", "varchar(4)", collation="utf8mb4_unicode_ci"),
        coluna(t, "numero", "varchar(20)", collation="utf8mb4_unicode_ci"),
        coluna(t, "codigo_ocorrencia", "int(11)"),
        coluna(t, "fluxo", "varchar(3)", collation="utf8mb4_unicode_ci"),
        coluna(t, "data_inicial_pesquisa", "date"),
        coluna(t, "data_final_pesquisa", "date"),
        coluna(t, "status", "varchar(20)", collation="utf8mb4_unicode_ci"),
        coluna(t, "reservado_em", "datetime"),
        coluna(t, "referencia", "varchar(25)", nullable="YES", collation="utf8mb4_bin"),
        coluna(t, "envio_iniciado_em", "datetime", nullable="YES"),
        coluna(t, "confirmado_em", "datetime", nullable="YES"),
        coluna(t, "resposta_ssw", "text", nullable="YES", collation="utf8mb4_unicode_ci"),
        coluna(t, "mensagem_erro", "text", nullable="YES", collation="utf8mb4_unicode_ci"),
    ]


def respostas_validas():
    sql = diagnostico.SQL_FIXO
    respostas = {
        sql["iniciar_transacao_read_only"]: [],
        sql["identidade"]: [identidade()],
        sql["tabelas"]: [
            {
                "TABLE_NAME": "oc73_execucoes",
                "ENGINE": "InnoDB",
                "TABLE_COLLATION": "utf8mb4_unicode_ci",
                "TABLE_ROWS": 10,
            },
            {
                "TABLE_NAME": "oc73_tentativas",
                "ENGINE": "InnoDB",
                "TABLE_COLLATION": "utf8mb4_unicode_ci",
                "TABLE_ROWS": 20,
            },
        ],
        sql["colunas"]: colunas_validas(),
        sql["indices"]: [
            {
                "TABLE_NAME": "oc73_execucoes",
                "INDEX_NAME": "PRIMARY",
                "NON_UNIQUE": 0,
                "SEQ_IN_INDEX": 1,
                "COLUMN_NAME": "id",
            },
            {
                "TABLE_NAME": "oc73_tentativas",
                "INDEX_NAME": "PRIMARY",
                "NON_UNIQUE": 0,
                "SEQ_IN_INDEX": 1,
                "COLUMN_NAME": "id",
            },
            {
                "TABLE_NAME": "oc73_tentativas",
                "INDEX_NAME": "uq_oc73_ctrc",
                "NON_UNIQUE": 0,
                "SEQ_IN_INDEX": 1,
                "COLUMN_NAME": "serie",
            },
            {
                "TABLE_NAME": "oc73_tentativas",
                "INDEX_NAME": "uq_oc73_ctrc",
                "NON_UNIQUE": 0,
                "SEQ_IN_INDEX": 2,
                "COLUMN_NAME": "numero",
            },
            {
                "TABLE_NAME": "oc73_tentativas",
                "INDEX_NAME": "uq_oc73_ctrc",
                "NON_UNIQUE": 0,
                "SEQ_IN_INDEX": 3,
                "COLUMN_NAME": "codigo_ocorrencia",
            },
            {
                "TABLE_NAME": "oc73_tentativas",
                "INDEX_NAME": "uq_oc73_referencia",
                "NON_UNIQUE": 0,
                "SEQ_IN_INDEX": 1,
                "COLUMN_NAME": "referencia",
            },
        ],
        sql["constraints"]: [
            {
                "TABLE_NAME": "oc73_execucoes",
                "CONSTRAINT_NAME": "PRIMARY",
            },
            {
                "TABLE_NAME": "oc73_tentativas",
                "CONSTRAINT_NAME": "fk_oc73_execucao",
                "CONSTRAINT_TYPE": "FOREIGN KEY",
                "COLUMN_NAME": "execucao_id",
                "REFERENCED_TABLE_NAME": "oc73_execucoes",
                "REFERENCED_COLUMN_NAME": "id",
            },
        ],
        sql["foreign_keys"]: [
            {
                "TABLE_NAME": "oc73_tentativas",
                "CONSTRAINT_NAME": "fk_oc73_execucao",
                "REFERENCED_TABLE_NAME": "oc73_execucoes",
                "UPDATE_RULE": "RESTRICT",
                "DELETE_RULE": "RESTRICT",
            }
        ],
        sql["ddl_execucoes"]: [
            {
                "Table": "oc73_execucoes",
                "Create Table": "CREATE TABLE `oc73_execucoes` (`id` bigint)",
            }
        ],
        sql["ddl_tentativas"]: [
            {
                "Table": "oc73_tentativas",
                "Create Table": "CREATE TABLE `oc73_tentativas` (`id` bigint)",
            }
        ],
        sql["duplicidades_scheduler"]: [
            {"grupos_ate_101": 0, "linhas_excedentes_ate_101": 0}
        ],
        sql["duplicidades_ctrc"]: [
            {"grupos_ate_101": 0, "linhas_excedentes_ate_101": 0}
        ],
        sql["duplicidades_referencia"]: [
            {"grupos_ate_101": 0, "linhas_excedentes_ate_101": 0}
        ],
        sql["tentativas_orfas"]: [{"total_ate_101": 0}],
        sql["integridade_estados"]: [
            {
                "execucoes_status_invalido": 0,
                "tentativas_status_invalido": 0,
            }
        ],
    }
    for item in respostas[sql["indices"]]:
        item["SUB_PART"] = None
    return respostas


def avaliar_schema_das_respostas(respostas):
    sql = diagnostico.SQL_FIXO
    return diagnostico.avaliar_conformidade_schema(
        tabelas=respostas[sql["tabelas"]],
        colunas=respostas[sql["colunas"]],
        indices=respostas[sql["indices"]],
        constraints=respostas[sql["constraints"]],
        foreign_keys=respostas[sql["foreign_keys"]],
    )


def obter_coluna(respostas, tabela, nome):
    return next(
        item
        for item in respostas[diagnostico.SQL_FIXO["colunas"]]
        if item["TABLE_NAME"] == tabela and item["COLUMN_NAME"] == nome
    )


def test_inventario_contem_apenas_leitura_e_transacao_controlada():
    assert set(diagnostico.SQL_FIXO) == {
        "iniciar_transacao_read_only",
        "identidade",
        "tabelas",
        "colunas",
        "indices",
        "constraints",
        "foreign_keys",
        "ddl_execucoes",
        "ddl_tentativas",
        "duplicidades_scheduler",
        "duplicidades_ctrc",
        "duplicidades_referencia",
        "tentativas_orfas",
        "integridade_estados",
    }

    for nome, sql in diagnostico.SQL_FIXO.items():
        normalizado = " ".join(sql.upper().split())
        assert ";" not in normalizado
        assert "OP535_CNPJ_CACHE" not in normalizado
        assert "%S" not in normalizado
        if nome == "iniciar_transacao_read_only":
            assert normalizado == "START TRANSACTION READ ONLY"
        else:
            assert normalizado.startswith(("SELECT ", "SHOW CREATE TABLE "))
        assert not re.search(
            r"\b(CALL|GET_LOCK|RELEASE_LOCK)\s*\(",
            normalizado,
        )
        assert "INTO OUTFILE" not in normalizado
        assert "INTO DUMPFILE" not in normalizado


def test_sql_arbitrario_nao_e_aceito():
    with pytest.raises(
        diagnostico.DiagnosticoSeguroErro,
        match="inventário fixo",
    ):
        diagnostico.obter_sql("SELECT_FORNECIDO_PELO_USUARIO")

    with pytest.raises(TypeError):
        diagnostico.SQL_FIXO["injetado"] = "SELECT 1"


def test_conexao_nao_habilita_multiplas_instrucoes(monkeypatch):
    recebido = {}
    sentinela = object()

    def conectar_falso(**kwargs):
        recebido.update(kwargs)
        return sentinela

    monkeypatch.setattr(diagnostico.pymysql, "connect", conectar_falso)

    assert diagnostico.abrir_conexao(config()) is sentinela
    assert recebido["autocommit"] is False
    assert "client_flag" not in recebido
    assert "init_command" not in recebido


@pytest.mark.parametrize(
    "alteracao",
    [
        {"banco": "outro"},
        {"porta": 3307},
        {"versao": "8.0.40"},
        {"server_uuid": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
    ],
)
@pytest.mark.parametrize("apenas_schema", [False, True])
def test_identidade_inesperada_falha_antes_das_tabelas(alteracao, apenas_schema):
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["identidade"]] = [
        identidade(**alteracao)
    ]
    conexao = ConexaoFalsa(respostas)

    with pytest.raises(diagnostico.DiagnosticoSeguroErro):
        diagnostico.diagnosticar(
            config(),
            server_uuid_esperado=UUID_ESPERADO,
            connection_factory=lambda _: conexao,
            apenas_schema=apenas_schema,
            verificacao=None if apenas_schema else "duplicidades_scheduler",
            confirmar_agregacao=not apenas_schema,
        )

    assert conexao.cursor_falso.executadas == [
        diagnostico.SQL_FIXO["iniciar_transacao_read_only"],
        diagnostico.SQL_FIXO["identidade"],
    ]
    assert conexao.rollback_chamado is True
    assert conexao.close_chamado is True


def test_diagnostico_fracionado_nao_exporta_registros_ou_segredos():
    senha = "senha-operacional-ultrassecreta"
    conexao = ConexaoFalsa(respostas_validas())

    resultado = diagnostico.diagnosticar(
        config(password=senha),
        server_uuid_esperado=UUID_ESPERADO,
        connection_factory=lambda _: conexao,
        verificacao="duplicidades_scheduler",
        confirmar_agregacao=True,
    )
    serializado = json.dumps(resultado, default=str)

    assert senha not in serializado
    assert "conta-operacional" not in serializado
    assert resultado["modo"] == "somente_leitura"
    assert resultado["estado_coleta_integridade"] == "completo"
    assert resultado["conformidade_schema"]["contrato_minimo"] == "conforme"
    assert resultado["conformidade_schema"]["unique_fluxo_data"]["estado"] == "ausente"
    assert resultado["tabelas"]["oc73_execucoes"]["metadados"][0][
        "TABLE_ROWS_NATUREZA"
    ] == "estimativa_aproximada_innodb"
    assert resultado["tabelas"]["oc73_execucoes"]["metadados"][0][
        "TABLE_ROWS_EXATO"
    ] is False
    assert resultado["limites"]["registros_operacionais_exportados"] == 0
    assert conexao.cursor_falso.executadas == [
        diagnostico.SQL_FIXO[nome] for nome in (
            "iniciar_transacao_read_only", "identidade", "tabelas", "colunas",
            "indices", "constraints", "foreign_keys", "ddl_execucoes",
            "ddl_tentativas", "duplicidades_scheduler",
        )
    ]
    assert conexao.rollback_chamado is True
    assert conexao.close_chamado is True


def test_erro_de_conexao_nao_vaza_segredo(monkeypatch, capsys):
    senha = "senha-na-excecao-nao-pode-vazar"
    monkeypatch.setenv("AUTOMATION_DB_HOST", "mysql-operacional.interno")
    monkeypatch.setenv("AUTOMATION_DB_PORT", "3306")
    monkeypatch.setenv("AUTOMATION_DB_NAME", "automationx")
    monkeypatch.setenv("AUTOMATION_DB_USER", "usuario-sensivel")
    monkeypatch.setenv("AUTOMATION_DB_PASSWORD", senha)

    def falhar_conexao(**kwargs):
        raise pymysql.OperationalError(
            1045,
            f"Access denied for usuario-sensivel password={senha}",
        )

    monkeypatch.setattr(diagnostico.pymysql, "connect", falhar_conexao)
    retorno = diagnostico.main([
        "--somente-leitura",
        "--server-uuid-esperado",
        UUID_ESPERADO,
    ])
    saida = capsys.readouterr()

    assert retorno == diagnostico.SAIDA_ERRO
    assert senha not in saida.out + saida.err
    assert "usuario-sensivel" not in saida.out + saida.err
    assert "Traceback" not in saida.out + saida.err


def test_sem_confirmacao_explica_nao_conecta(monkeypatch):
    chamado = False

    def conexao_proibida(**kwargs):
        nonlocal chamado
        chamado = True
        raise AssertionError("não deveria conectar")

    monkeypatch.setattr(diagnostico.pymysql, "connect", conexao_proibida)
    assert diagnostico.main([
        "--server-uuid-esperado",
        UUID_ESPERADO,
    ]) == diagnostico.SAIDA_ERRO
    assert chamado is False


def test_script_nao_importa_servicos_operacionais():
    caminho = Path(diagnostico.__file__)
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    importacoes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importacoes.update(alias.name for alias in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importacoes.add(no.module)

    prefixos_proibidos = (
        "web.main",
        "modules.ocorrencia_73",
        "operations",
        "ssw",
    )
    assert not any(
        modulo.startswith(prefixos_proibidos)
        for modulo in importacoes
    )


def test_inventario_e_compativel_com_mysql_57_32():
    inventario = "\n".join(diagnostico.SQL_FIXO.values()).upper()

    assert "START TRANSACTION READ ONLY" in inventario
    assert "MAX_EXECUTION_TIME(5000)" in inventario
    assert "@@SERVER_UUID" in inventario
    assert "JSON_TABLE" not in inventario
    assert " RETURNING " not in inventario
    assert " SKIP LOCKED" not in inventario
    assert " ROW_NUMBER(" not in inventario
    assert " WITH RECURSIVE " not in inventario

    for nome in (
        "duplicidades_scheduler",
        "duplicidades_ctrc",
        "duplicidades_referencia",
        "tentativas_orfas",
        "integridade_estados",
    ):
        sql = diagnostico.SQL_FIXO[nome].lstrip().upper()
        assert sql.startswith("SELECT /*+ MAX_EXECUTION_TIME(5000) */")
        assert sql.count("SELECT") > 1


def test_fixture_schema_totalmente_conforme():
    resultado = avaliar_schema_das_respostas(respostas_validas())

    assert resultado["estado_coleta"] == "completo"
    assert resultado["contrato_minimo"] == "conforme"
    assert resultado["estrutura_colunas"]["estado"] == "conforme"


def test_modo_apenas_schema_nao_consulta_registros_operacionais():
    conexao = ConexaoFalsa(respostas_validas())

    resultado = diagnostico.diagnosticar(
        config(),
        server_uuid_esperado=UUID_ESPERADO,
        connection_factory=lambda _: conexao,
        apenas_schema=True,
    )

    assert resultado["escopo"] == "apenas_schema"
    assert resultado["conformidade_schema"]["contrato_minimo"] == "conforme"
    assert resultado["estado_coleta_integridade"] == "nao_consultada"
    assert resultado["integridade"] is None
    assert conexao.cursor_falso.executadas == [
        diagnostico.SQL_FIXO[nome]
        for nome in (
            "iniciar_transacao_read_only", "identidade", "tabelas",
            "colunas", "indices", "constraints", "foreign_keys",
            "ddl_execucoes", "ddl_tentativas",
        )
    ]
    assert conexao.rollback_chamado is True
    assert conexao.close_chamado is True


@pytest.mark.parametrize(
    "colunas_indice,nao_unico",
    [
        (("serie", "fluxo", "codigo_ocorrencia"), 0),
        (("serie", "numero", "codigo_ocorrencia"), 1),
        (("numero", "serie", "codigo_ocorrencia"), 0),
    ],
)
def test_indice_ctrc_com_composicao_ordem_ou_unicidade_incorreta(
    colunas_indice, nao_unico,
):
    respostas = respostas_validas()
    partes = [
        item for item in respostas[diagnostico.SQL_FIXO["indices"]]
        if item["INDEX_NAME"] == "uq_oc73_ctrc"
    ]
    for item, coluna_esperada in zip(partes, colunas_indice):
        item["COLUMN_NAME"] = coluna_esperada
        item["NON_UNIQUE"] = nao_unico

    resultado = avaliar_schema_das_respostas(respostas)
    assert resultado["unique_ctrc"]["estado"] == "nao_conforme"
    assert resultado["contrato_minimo"] == "nao_conforme"


def test_indice_equivalente_sem_nome_exato_nao_libera_reserva():
    respostas = respostas_validas()
    for item in respostas[diagnostico.SQL_FIXO["indices"]]:
        if item["INDEX_NAME"] == "uq_oc73_ctrc":
            item["INDEX_NAME"] = "indice_equivalente"

    resultado = avaliar_schema_das_respostas(respostas)
    assert resultado["unique_ctrc"]["indices_compativeis"] == ["indice_equivalente"]
    assert resultado["unique_ctrc"]["nome_esperado_presente"] is False
    assert resultado["contrato_minimo"] == "nao_conforme"


def test_unique_por_prefixo_nao_satisfaz_contrato():
    respostas = respostas_validas()
    for item in respostas[diagnostico.SQL_FIXO["indices"]]:
        if item["INDEX_NAME"] == "uq_oc73_ctrc" and item["COLUMN_NAME"] == "numero":
            item["SUB_PART"] = 8

    resultado = avaliar_schema_das_respostas(respostas)
    assert resultado["unique_ctrc"]["estado"] == "nao_conforme"
    assert resultado["contrato_minimo"] == "nao_conforme"


def test_engine_diferente_de_innodb_bloqueia_conformidade():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["tabelas"]][1]["ENGINE"] = "MyISAM"

    resultado = avaliar_schema_das_respostas(respostas)
    assert resultado["engine"]["estado"] == "nao_conforme"
    assert resultado["contrato_minimo"] == "nao_conforme"


def test_fk_ausente_bloqueia_conformidade():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["constraints"]] = [
        item for item in respostas[diagnostico.SQL_FIXO["constraints"]]
        if item.get("CONSTRAINT_TYPE") != "FOREIGN KEY"
    ]
    respostas[diagnostico.SQL_FIXO["foreign_keys"]] = []

    resultado = avaliar_schema_das_respostas(respostas)
    assert resultado["fk_execucao"]["estado_relacao_minima"] == "nao_conforme"
    assert resultado["contrato_minimo"] == "nao_conforme"


def test_cli_apenas_schema_falha_fechada_quando_nao_conforme(monkeypatch, capsys):
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["indices"]] = [
        item for item in respostas[diagnostico.SQL_FIXO["indices"]]
        if item["INDEX_NAME"] != "uq_oc73_ctrc"
    ]
    conexao = ConexaoFalsa(respostas)
    monkeypatch.setattr(
        diagnostico.ConfiguracaoBanco,
        "carregar",
        lambda: config(),
    )
    monkeypatch.setattr(
        diagnostico,
        "abrir_conexao",
        lambda _: (_ for _ in ()).throw(AssertionError("conexao real proibida")),
    )
    diagnosticar_original = diagnostico.diagnosticar
    monkeypatch.setattr(
        diagnostico,
        "diagnosticar",
        lambda cfg, **kwargs: diagnosticar_original(
            cfg, connection_factory=lambda _: conexao, **kwargs
        ),
    )

    codigo = diagnostico.main([
        "--somente-leitura", "--apenas-schema",
        "--server-uuid-esperado", UUID_ESPERADO,
    ])

    assert codigo == 2
    assert json.loads(capsys.readouterr().out)["verificacoes"]["contrato_minimo_schema"]["estado"] == "nao_conforme"
    assert conexao.rollback_chamado is True
    assert conexao.close_chamado is True


def test_coluna_obrigatoria_ausente_e_nao_conforme():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["colunas"]] = [
        item
        for item in respostas[diagnostico.SQL_FIXO["colunas"]]
        if not (
            item["TABLE_NAME"] == "oc73_execucoes"
            and item["COLUMN_NAME"] == "total_erros"
        )
    ]

    resultado = avaliar_schema_das_respostas(respostas)

    assert resultado["contrato_minimo"] == "nao_conforme"
    assert "oc73_execucoes.total_erros" in resultado["estrutura_colunas"][
        "colunas_obrigatorias_ausentes"
    ]


def test_tipo_incompativel_e_nao_conforme():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_execucoes", "data_referencia")[
        "COLUMN_TYPE"
    ] = "varchar(10)"

    resultado = avaliar_schema_das_respostas(respostas)

    problemas = resultado["estrutura_colunas"]["colunas_incompativeis"]
    assert {"coluna": "oc73_execucoes.data_referencia", "problemas": ["tipo_incompativel"]} in problemas


def test_varchar_insuficiente_e_nao_conforme():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_tentativas", "referencia")[
        "COLUMN_TYPE"
    ] = "varchar(24)"

    resultado = avaliar_schema_das_respostas(respostas)

    assert "capacidade_insuficiente" in next(
        item["problemas"]
        for item in resultado["estrutura_colunas"]["colunas_incompativeis"]
        if item["coluna"] == "oc73_tentativas.referencia"
    )


def test_campo_obrigatorio_permitindo_null_e_nao_conforme():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_execucoes", "data_referencia")[
        "IS_NULLABLE"
    ] = "YES"

    resultado = avaliar_schema_das_respostas(respostas)

    assert "campo_obrigatorio_permite_null" in next(
        item["problemas"]
        for item in resultado["estrutura_colunas"]["colunas_incompativeis"]
        if item["coluna"] == "oc73_execucoes.data_referencia"
    )


def test_campo_opcional_exigindo_valor_e_nao_conforme():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_execucoes", "finalizado_em")[
        "IS_NULLABLE"
    ] = "NO"

    resultado = avaliar_schema_das_respostas(respostas)

    assert "campo_opcional_exige_valor" in next(
        item["problemas"]
        for item in resultado["estrutura_colunas"]["colunas_incompativeis"]
        if item["coluna"] == "oc73_execucoes.finalizado_em"
    )


def test_pk_ausente_e_nao_conforme():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["indices"]] = [
        item
        for item in respostas[diagnostico.SQL_FIXO["indices"]]
        if not (
            item["TABLE_NAME"] == "oc73_execucoes"
            and item["INDEX_NAME"] == "PRIMARY"
        )
    ]

    resultado = avaliar_schema_das_respostas(respostas)

    assert resultado["estrutura_colunas"]["chaves_primarias"][
        "oc73_execucoes"
    ]["estado"] == "nao_conforme"


def test_auto_increment_ausente_e_nao_conforme():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_tentativas", "id")["EXTRA"] = ""

    resultado = avaliar_schema_das_respostas(respostas)

    assert resultado["estrutura_colunas"]["chaves_primarias"][
        "oc73_tentativas"
    ]["motivo"] == "auto_increment_ausente"


def test_unique_ctrc_ausente_e_nao_conforme():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["indices"]] = [
        item
        for item in respostas[diagnostico.SQL_FIXO["indices"]]
        if item["INDEX_NAME"] != "uq_oc73_ctrc"
    ]

    resultado = avaliar_schema_das_respostas(respostas)

    assert resultado["unique_ctrc"]["estado"] == "nao_conforme"


def test_collation_de_referencia_inadequada_e_nao_conforme():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_tentativas", "referencia")[
        "COLLATION_NAME"
    ] = "utf8mb4_unicode_ci"

    resultado = avaliar_schema_das_respostas(respostas)

    assert "collation_nao_case_sensitive" in next(
        item["problemas"]
        for item in resultado["estrutura_colunas"]["colunas_incompativeis"]
        if item["coluna"] == "oc73_tentativas.referencia"
    )


def test_fk_com_tipo_incompativel_e_nao_conforme():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_tentativas", "execucao_id")[
        "COLUMN_TYPE"
    ] = "int(11) unsigned"

    resultado = avaliar_schema_das_respostas(respostas)

    assert resultado["fk_execucao"]["estado_compatibilidade_tipos"] == "nao_conforme"
    assert resultado["contrato_minimo"] == "nao_conforme"


def test_status_persistido_invalido_e_reportado_sem_dados_operacionais():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["integridade_estados"]] = [{
        "execucoes_status_invalido": 2,
        "tentativas_status_invalido": 3,
    }]
    conexao = ConexaoFalsa(respostas)

    resultado = diagnostico.diagnosticar(
        config(),
        server_uuid_esperado=UUID_ESPERADO,
        connection_factory=lambda _: conexao,
        verificacao="integridade_estados",
        confirmar_agregacao=True,
    )

    estados = resultado["integridade"]["integridade_estados"]
    assert estados["estado_coleta"] == "completo"
    assert estados["estado_conformidade"] == "nao_conforme"
    assert estados["execucoes_status_invalido"] == 2
    assert estados["tentativas_status_invalido"] == 3


def test_metadados_incompletos_resultam_inconclusivos():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_execucoes", "data_referencia").pop(
        "COLUMN_TYPE"
    )

    resultado = avaliar_schema_das_respostas(respostas)

    assert resultado["estado_coleta"] == "inconclusivo"
    assert resultado["contrato_minimo"] == "inconclusivo"
    assert resultado["estrutura_colunas"]["metadados_inconclusivos"]


def test_falha_da_consulta_de_estados_e_inconclusiva():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["integridade_estados"]] = (
        pymysql.OperationalError(3024, "detalhe-nao-deve-vazar")
    )
    conexao = ConexaoFalsa(respostas)

    resultado = diagnostico.diagnosticar(
        config(),
        server_uuid_esperado=UUID_ESPERADO,
        connection_factory=lambda _: conexao,
        verificacao="integridade_estados",
        confirmar_agregacao=True,
    )

    assert resultado["estado_coleta_integridade"] == "inconclusivo"
    assert resultado["integridade"]["integridade_estados"] == {
        "estado_coleta": "inconclusivo",
        "motivo": "erro_consulta",
        "execucoes_status_invalido": None,
        "tentativas_status_invalido": None,
    }


def test_inconclusivo_nao_e_convertido_em_conformidade_ou_zero():
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_tentativas", "referencia").pop(
        "COLLATION_NAME"
    )

    resultado = avaliar_schema_das_respostas(respostas)

    assert resultado["contrato_minimo"] == "inconclusivo"
    assert resultado["estrutura_colunas"]["estado"] == "inconclusivo"
    assert resultado["estrutura_colunas"]["metadados_inconclusivos"] != []


def test_schema_incompleto_falha_sem_consultar_integridade():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["tabelas"]] = [
        respostas[diagnostico.SQL_FIXO["tabelas"]][0]
    ]
    conexao = ConexaoFalsa(respostas)

    with pytest.raises(
        diagnostico.DiagnosticoSeguroErro,
        match="Conjunto de tabelas OC73 inesperado",
    ):
        diagnostico.diagnosticar(
            config(),
            server_uuid_esperado=UUID_ESPERADO,
            connection_factory=lambda _: conexao,
            verificacao="duplicidades_scheduler",
            confirmar_agregacao=True,
        )

    assert conexao.cursor_falso.executadas == [
        diagnostico.SQL_FIXO["iniciar_transacao_read_only"],
        diagnostico.SQL_FIXO["identidade"],
        diagnostico.SQL_FIXO["tabelas"],
    ]
    assert conexao.rollback_chamado is True
    assert conexao.close_chamado is True


def test_indice_unique_ausente_e_reportado_como_nao_conforme():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["indices"]] = [
        item
        for item in respostas[diagnostico.SQL_FIXO["indices"]]
        if item["INDEX_NAME"] != "uq_oc73_referencia"
    ]
    conexao = ConexaoFalsa(respostas)

    resultado = diagnostico.diagnosticar(
        config(),
        server_uuid_esperado=UUID_ESPERADO,
        connection_factory=lambda _: conexao,
        verificacao="duplicidades_scheduler",
        confirmar_agregacao=True,
    )

    assert resultado["conformidade_schema"]["contrato_minimo"] == "nao_conforme"
    assert resultado["conformidade_schema"]["unique_referencia"]["estado"] == "nao_conforme"
    assert resultado["integridade"]["duplicidades_scheduler"]["estado_coleta"] == "nao_executada"


def test_limite_atingido_nao_e_reportado_como_quantidade_exata():
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["duplicidades_ctrc"]] = [{
        "grupos_ate_101": 101,
        "linhas_excedentes_ate_101": 407,
    }]
    conexao = ConexaoFalsa(respostas)

    resultado = diagnostico.diagnosticar(
        config(),
        server_uuid_esperado=UUID_ESPERADO,
        connection_factory=lambda _: conexao,
        verificacao="duplicidades_ctrc",
        confirmar_agregacao=True,
    )
    ctrc = resultado["integridade"]["duplicidades_ctrc"]

    assert resultado["estado_coleta_integridade"] == "limitado"
    assert ctrc["estado_coleta"] == "limitado"
    assert ctrc["quantidade_exata"] is None
    assert ctrc["pelo_menos"] == 101
    assert ctrc["excedentes_sao_parciais"] is True


def test_erro_de_consulta_torna_resultados_inconclusivos_sem_vazar_erro():
    segredo = "valor-operacional-sensivel"
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["duplicidades_ctrc"]] = (
        pymysql.OperationalError(3024, segredo)
    )
    conexao = ConexaoFalsa(respostas)

    resultado = diagnostico.diagnosticar(
        config(),
        server_uuid_esperado=UUID_ESPERADO,
        connection_factory=lambda _: conexao,
        verificacao="duplicidades_ctrc",
        confirmar_agregacao=True,
    )
    serializado = json.dumps(resultado)

    assert resultado["estado_coleta_integridade"] == "inconclusivo"
    assert resultado["integridade"]["duplicidades_ctrc"] == {
        "estado_coleta": "inconclusivo",
        "motivo": "erro_consulta",
        "quantidade_exata": None,
    }
    assert resultado["integridade"]["duplicidades_referencia"][
        "motivo"
    ] == "nao_selecionada"
    assert diagnostico.SQL_FIXO["duplicidades_referencia"] not in (
        conexao.cursor_falso.executadas
    )
    assert segredo not in serializado


@pytest.mark.parametrize("codigo", [1142, 3024])
def test_falha_em_metadados_aborta_antes_dos_agregados(codigo):
    segredo = "detalhe_sensivel_sintetico"
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["indices"]] = pymysql.OperationalError(
        codigo, segredo
    )
    conexao = ConexaoFalsa(respostas)

    with pytest.raises(diagnostico.DiagnosticoSeguroErro) as erro:
        diagnostico.diagnosticar(
            config(),
            server_uuid_esperado=UUID_ESPERADO,
            connection_factory=lambda _: conexao,
            verificacao="duplicidades_scheduler",
            confirmar_agregacao=True,
        )

    assert segredo not in str(erro.value)
    assert all(
        diagnostico.SQL_FIXO[nome] not in conexao.cursor_falso.executadas
        for nome in (
            "duplicidades_scheduler", "duplicidades_ctrc",
            "duplicidades_referencia", "tentativas_orfas",
            "integridade_estados",
        )
    )
    assert conexao.rollback_chamado is True
    assert conexao.close_chamado is True


def test_permissao_negada_no_primeiro_agregado_interrompe_os_demais():
    segredo = "detalhe_sensivel_sintetico"
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["duplicidades_scheduler"]] = (
        pymysql.OperationalError(1142, segredo)
    )
    conexao = ConexaoFalsa(respostas)

    resultado = diagnostico.diagnosticar(
        config(),
        server_uuid_esperado=UUID_ESPERADO,
        connection_factory=lambda _: conexao,
        verificacao="duplicidades_scheduler",
        confirmar_agregacao=True,
    )

    assert resultado["estado_coleta_integridade"] == "inconclusivo"
    assert resultado["integridade"]["duplicidades_scheduler"]["motivo"] == "erro_consulta"
    assert resultado["integridade"]["duplicidades_ctrc"]["motivo"] == "nao_selecionada"
    assert diagnostico.SQL_FIXO["duplicidades_ctrc"] not in conexao.cursor_falso.executadas
    assert segredo not in json.dumps(resultado)
    assert conexao.rollback_chamado is True
    assert conexao.close_chamado is True


def executar_cli_com_respostas(monkeypatch, capsys, respostas, *opcoes):
    conexao = ConexaoFalsa(respostas)
    monkeypatch.setattr(diagnostico.ConfiguracaoBanco, "carregar", lambda: config())
    original = diagnostico.diagnosticar
    monkeypatch.setattr(
        diagnostico, "diagnosticar",
        lambda cfg, **kwargs: original(
            cfg, connection_factory=lambda _: conexao, **kwargs
        ),
    )
    codigo = diagnostico.main([
        "--somente-leitura", "--server-uuid-esperado", UUID_ESPERADO,
        *opcoes,
    ])
    saida = capsys.readouterr()
    return codigo, json.loads(saida.out), conexao


@pytest.mark.parametrize(
    "alteracao,esperado,estado",
    [
        ("nenhuma", 0, "conforme"),
        ("duplicidade", 2, "nao_conforme"),
        ("timeout", 2, "inconclusivo"),
        ("permissao", 2, "inconclusivo"),
        ("parcial", 2, "inconclusivo"),
        ("limite", 2, "nao_conforme"),
        ("estado_invalido", 2, "nao_conforme"),
    ],
)
def test_cli_fracionado_classifica_verificacao_selecionada(
    monkeypatch, capsys, alteracao, esperado, estado,
):
    respostas = respostas_validas()
    sql = diagnostico.SQL_FIXO
    if alteracao == "duplicidade":
        respostas[sql["duplicidades_ctrc"]] = [
            {"grupos_ate_101": 1, "linhas_excedentes_ate_101": 1}
        ]
    elif alteracao in ("timeout", "permissao"):
        codigo_mysql = 3024 if alteracao == "timeout" else 1142
        respostas[sql["duplicidades_scheduler"]] = pymysql.OperationalError(
            codigo_mysql, "SEGREDO-DO-SERVIDOR"
        )
    elif alteracao == "parcial":
        respostas[sql["integridade_estados"]] = pymysql.OperationalError(
            3024, "SEGREDO-DO-SERVIDOR"
        )
    elif alteracao == "limite":
        respostas[sql["duplicidades_referencia"]] = [
            {"grupos_ate_101": 101, "linhas_excedentes_ate_101": 101}
        ]
    elif alteracao == "estado_invalido":
        respostas[sql["integridade_estados"]] = [
            {"execucoes_status_invalido": 1, "tentativas_status_invalido": 0}
        ]

    selecionada = {
        "duplicidade": "duplicidades_ctrc",
        "parcial": "integridade_estados",
        "limite": "duplicidades_referencia",
        "estado_invalido": "integridade_estados",
    }.get(alteracao, "duplicidades_scheduler")
    codigo, resumo, conexao = executar_cli_com_respostas(
        monkeypatch, capsys, respostas, "--verificacao", selecionada,
        "--confirmar-agregacao", "--resumo"
    )
    assert codigo == esperado
    assert resumo["conformidade"] == estado
    assert set(resumo["verificacoes"]) == {
        "contrato_minimo_schema", "coleta_integridade",
        *diagnostico.CONSULTAS_INTEGRIDADE,
    }
    assert all(item["estado"] in {"conforme", "nao_conforme", "inconclusivo"}
               for nome, item in resumo["verificacoes"].items()
               if nome not in diagnostico.CONSULTAS_INTEGRIDADE or nome == selecionada)
    assert all(resumo["verificacoes"][nome]["estado"] == "nao_executada"
               for nome in diagnostico.CONSULTAS_INTEGRIDADE if nome != selecionada)
    assert sum(sql in conexao.cursor_falso.executadas
               for nome, sql in diagnostico.SQL_FIXO.items()
               if nome in diagnostico.CONSULTAS_INTEGRIDADE) == 1
    assert conexao.rollback_chamado and conexao.close_chamado
    assert "SEGREDO-DO-SERVIDOR" not in json.dumps(resumo)


def test_resumo_padrao_omite_identidade_ddl_e_valores_arbitrarios(monkeypatch, capsys):
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["identidade"]][0]["hostname"] = "HOST-SENSIVEL"
    respostas[diagnostico.SQL_FIXO["ddl_execucoes"]][0]["Create Table"] += " DDL-SENSIVEL"
    codigo, resumo, _ = executar_cli_com_respostas(
        monkeypatch, capsys, respostas,
        "--verificacao", "duplicidades_scheduler", "--confirmar-agregacao"
    )
    serializado = json.dumps(resumo)
    assert codigo == 0
    assert resumo["escopo"] == "verificacao_operacional"
    assert resumo["limites"]["garantia_temporal"] == "sem_limite_total_rigido"
    for segredo in ("HOST-SENSIVEL", "DDL-SENSIVEL", UUID_ESPERADO,
                    "mysql-operacional.interno", "CREATE TABLE", "conta-operacional"):
        assert segredo not in serializado


def test_relatorio_integral_exige_opcao_explicita(monkeypatch, capsys):
    codigo, integral, _ = executar_cli_com_respostas(
        monkeypatch, capsys, respostas_validas(), "--apenas-schema", "--completo"
    )
    assert codigo == 0
    assert integral["identidade"]["server_uuid"] == UUID_ESPERADO
    assert "ddl" in integral["tabelas"]["oc73_execucoes"]


def test_cli_apenas_schema_conforme_nao_executa_agregados(monkeypatch, capsys):
    codigo, resumo, conexao = executar_cli_com_respostas(
        monkeypatch, capsys, respostas_validas(), "--apenas-schema"
    )
    assert codigo == 0
    assert resumo["escopo"] == "apenas_schema"
    assert list(resumo["verificacoes"]) == ["contrato_minimo_schema"]
    assert all(diagnostico.SQL_FIXO[nome] not in conexao.cursor_falso.executadas
               for nome in diagnostico.CONSULTAS_INTEGRIDADE)


def test_cli_schema_inconclusivo_retorna_2(monkeypatch, capsys):
    respostas = respostas_validas()
    obter_coluna(respostas, "oc73_tentativas", "referencia").pop("COLLATION_NAME")
    codigo, resumo, _ = executar_cli_com_respostas(
        monkeypatch, capsys, respostas, "--apenas-schema"
    )
    assert codigo == 2
    assert resumo["conformidade"] == "inconclusivo"
    assert "contrato_minimo_schema:inconclusivo" in resumo["motivos_bloqueio"]


def test_contagem_negativa_nao_aprova_integridade(monkeypatch, capsys):
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["tentativas_orfas"]] = [{"total_ate_101": -1}]
    codigo, resumo, _ = executar_cli_com_respostas(
        monkeypatch, capsys, respostas, "--verificacao", "tentativas_orfas",
        "--confirmar-agregacao"
    )
    assert codigo == 2
    assert resumo["verificacoes"]["tentativas_orfas"]["estado"] == "inconclusivo"
    assert resumo["verificacoes"]["integridade_estados"]["estado"] == "nao_executada"


def test_resumo_exige_estado_global_de_coleta_completo():
    resultado = {
        "escopo": "schema_e_integridade",
        "conformidade_schema": {"contrato_minimo": "conforme"},
        "estado_coleta_integridade": "inconclusivo",
        "integridade": {
            nome: (
                {"estado_coleta": "completo", "execucoes_status_invalido": 0,
                 "tentativas_status_invalido": 0}
                if nome == "integridade_estados" else
                {"estado_coleta": "completo", "quantidade_exata": 0}
            )
            for nome in diagnostico.CONSULTAS_INTEGRIDADE
        },
    }
    resumo = diagnostico.resumir_resultado(resultado)
    assert resumo["conformidade"] == "inconclusivo"
    assert resumo["verificacoes"]["coleta_integridade"]["estado"] == "inconclusivo"


@pytest.mark.parametrize("nome", diagnostico.CONSULTAS_INTEGRIDADE)
def test_cada_selecao_executa_somente_seu_agregado(monkeypatch, capsys, nome):
    codigo, resumo, conexao = executar_cli_com_respostas(
        monkeypatch, capsys, respostas_validas(),
        "--verificacao", nome, "--confirmar-agregacao",
    )
    assert codigo == 0
    assert resumo["verificacao_selecionada"] == nome
    assert resumo["verificacoes"][nome]["estado"] == "conforme"
    assert {n for n in diagnostico.CONSULTAS_INTEGRIDADE
            if diagnostico.SQL_FIXO[n] in conexao.cursor_falso.executadas} == {nome}
    assert conexao.rollback_chamado and conexao.close_chamado


@pytest.mark.parametrize("argumentos", [
    [],
    ["--verificacao", "duplicidades_ctrc"],
    ["--verificacao", "desconhecida", "--confirmar-agregacao"],
    ["--apenas-schema", "--verificacao", "duplicidades_ctrc"],
    ["--apenas-schema", "--confirmar-agregacao"],
    ["--verificacao", "duplicidades_ctrc", "--verificacao", "tentativas_orfas",
     "--confirmar-agregacao"],
    ["--verificacao", "duplicidades_ctrc", "--confirmar-agregacao", "--completo"],
])
def test_cli_recusa_modo_ambiguo_antes_de_conectar(monkeypatch, capsys, argumentos):
    chamadas = []
    monkeypatch.setattr(
        diagnostico.ConfiguracaoBanco, "carregar",
        lambda: chamadas.append("config") or config(),
    )
    codigo = diagnostico.main([
        "--somente-leitura", "--server-uuid-esperado", UUID_ESPERADO,
        *argumentos,
    ])
    assert codigo == diagnostico.SAIDA_ERRO
    assert chamadas == []
    assert capsys.readouterr().out == ""


def test_schema_nao_conforme_impede_agregado_e_nao_aprova(monkeypatch, capsys):
    respostas = respostas_validas()
    respostas[diagnostico.SQL_FIXO["indices"]] = [
        item for item in respostas[diagnostico.SQL_FIXO["indices"]]
        if item["INDEX_NAME"] != "uq_oc73_ctrc"
    ]
    codigo, resumo, conexao = executar_cli_com_respostas(
        monkeypatch, capsys, respostas, "--verificacao", "duplicidades_scheduler",
        "--confirmar-agregacao",
    )
    assert codigo == 2
    assert resumo["verificacoes"]["contrato_minimo_schema"]["estado"] == "nao_conforme"
    assert resumo["verificacoes"]["duplicidades_scheduler"]["estado"] == "nao_executada"
    assert all(diagnostico.SQL_FIXO[n] not in conexao.cursor_falso.executadas
               for n in diagnostico.CONSULTAS_INTEGRIDADE)


def test_api_recusa_modo_legado_sem_abrir_conexao():
    chamadas = []
    with pytest.raises(diagnostico.DiagnosticoSeguroErro, match="Modo completo indisponível"):
        diagnostico.diagnosticar(
            config(), server_uuid_esperado=UUID_ESPERADO,
            connection_factory=lambda _: chamadas.append("conexao"),
        )
    assert chamadas == []


@pytest.mark.parametrize("falha", ["rollback", "close"])
def test_falha_no_encerramento_nao_aprova_diagnostico(falha):
    conexao = ConexaoFalsa(respostas_validas())
    original = getattr(conexao, falha)
    def falhar():
        original()
        raise RuntimeError("SEGREDO-DE-CONEXAO")
    setattr(conexao, falha, falhar)
    with pytest.raises(diagnostico.DiagnosticoSeguroErro, match="Encerramento da sessão") as erro:
        diagnostico.diagnosticar(
            config(), server_uuid_esperado=UUID_ESPERADO,
            connection_factory=lambda _: conexao,
            verificacao="duplicidades_scheduler", confirmar_agregacao=True,
        )
    assert "SEGREDO-DE-CONEXAO" not in str(erro.value)
    assert conexao.rollback_chamado and conexao.close_chamado


def test_selecionada_nao_executada_nunca_e_aprovada():
    resultado = {
        "escopo": "verificacao_operacional",
        "verificacao_selecionada": "duplicidades_scheduler",
        "conformidade_schema": {"contrato_minimo": "conforme"},
        "estado_coleta_integridade": "completo",
        "integridade": {
            "duplicidades_scheduler": {"estado_coleta": "nao_executada"},
        },
    }
    resumo = diagnostico.resumir_resultado(resultado)
    assert resumo["verificacoes"]["duplicidades_scheduler"]["estado"] == "nao_executada"
    assert resumo["conformidade"] == "inconclusivo"
    assert "duplicidades_scheduler:nao_executada" in resumo["motivos_bloqueio"]
