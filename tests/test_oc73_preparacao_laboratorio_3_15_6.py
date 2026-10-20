"""Validações estáticas do pacote 3.15.6; nenhum SQL é executado."""

from pathlib import Path
import re


RAIZ = Path(__file__).resolve().parents[1]
DDL = RAIZ / "prototypes/sql/oc73_autorizacao_lab_mysql57.sql"
DOCUMENTOS = {
    "inventario": RAIZ / "docs/oc73_inventario_laboratorio_3_15_6.md",
    "provisionamento": RAIZ / "docs/oc73_provisionamento_laboratorio_3_15_6.md",
    "testes": RAIZ / "docs/oc73_plano_testes_mysql57_3_15_6.md",
    "driver": RAIZ / "docs/oc73_contrato_driver_mysql_3_15_6.md",
    "autorizacao": RAIZ / "docs/oc73_solicitacao_autorizacao_laboratorio_3_15_6.md",
    "ddl": RAIZ / "docs/oc73_revisao_ddl_mysql57_3_15_6.md",
}

TABELAS = [
    "oc73_lab_tentativas",
    "oc73_lab_autorizacoes",
    "oc73_lab_aprovacoes",
    "oc73_lab_consumos",
    "oc73_lab_auditoria",
]


def _ddl_sem_comentarios() -> str:
    linhas = DDL.read_text(encoding="utf-8").splitlines()
    return "\n".join(linha for linha in linhas if not linha.lstrip().startswith("--"))


def test_ddl_tem_cinco_tabelas_na_ordem_das_dependencias():
    ddl = _ddl_sem_comentarios()
    encontradas = re.findall(r"CREATE TABLE\s+(oc73_lab_[a-z_]+)\s*\(", ddl)
    assert encontradas == TABELAS


def test_ddl_fixa_engine_charset_e_collation_compativeis_com_mysql57():
    ddl = _ddl_sem_comentarios()
    terminador = (
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
        "COLLATE=utf8mb4_unicode_ci;"
    )
    assert ddl.count(terminador) == len(TABELAS)
    assert "DATETIME(6)" in ddl
    assert "CHARACTER SET ascii COLLATE ascii_bin" in ddl
    assert not re.search(r"\b(CHECK|GENERATED|JSON)\b", ddl, re.IGNORECASE)


def test_ddl_declara_pks_uniques_e_indices_das_fks_compostas():
    ddl = _ddl_sem_comentarios()
    assert ddl.count("PRIMARY KEY (id)") == 5
    exigidos = {
        "UNIQUE KEY uq_oc73_lab_tentativa_contexto\n"
        "    (id, referencia, revisao_codigo)",
        "UNIQUE KEY uq_oc73_lab_ctrc (serie, numero)",
        "UNIQUE KEY uq_oc73_lab_referencia (referencia)",
        "UNIQUE KEY uq_oc73_lab_auth_tentativa (tentativa_id)",
        "UNIQUE KEY uq_oc73_lab_auth_contexto (id, tentativa_id)",
        "UNIQUE KEY uq_oc73_lab_aprovacao_papel (autorizacao_id, papel)",
        "UNIQUE KEY uq_oc73_lab_aprovacao_conta",
        "UNIQUE KEY uq_oc73_lab_aprovacao_pessoa",
        "UNIQUE KEY uq_oc73_lab_consumo_auth (autorizacao_id)",
        "UNIQUE KEY uq_oc73_lab_consumo_tentativa (tentativa_id)",
    }
    assert all(item in ddl for item in exigidos)


def test_ddl_impede_contextos_cruzados_por_fks_compostas():
    ddl = _ddl_sem_comentarios()
    assert (
        "FOREIGN KEY (tentativa_id, referencia, revisao_codigo)\n"
        "    REFERENCES oc73_lab_tentativas (id, referencia, revisao_codigo)"
    ) in ddl
    assert ddl.count(
        "FOREIGN KEY (autorizacao_id, tentativa_id)\n"
        "    REFERENCES oc73_lab_autorizacoes (id, tentativa_id)"
    ) == 2
    assert ddl.count(
        "FOREIGN KEY (tentativa_id) REFERENCES oc73_lab_tentativas (id)"
    ) == 2


def test_ddl_tem_estados_identidades_e_expiracao_esperados():
    ddl = _ddl_sem_comentarios()
    assert "ENUM('reservado','envio_iniciado','incerto','confirmado')" in ddl
    assert "ENUM('pendente','revogada','consumida')" in ddl
    assert "ENUM('tecnico','operacional')" in ddl
    for campo in (
        "expira_em DATETIME(6) NOT NULL",
        "person_binding_expires_at DATETIME(6) NOT NULL",
        "executor_person_binding_expires_at DATETIME(6) NOT NULL",
    ):
        assert campo in ddl
    for campo in (
        "subject_issuer",
        "subject_id",
        "person_issuer",
        "person_id",
        "executor_subject_issuer",
        "executor_subject_id",
        "executor_person_issuer",
        "executor_person_id",
    ):
        assert campo in ddl


def test_ddl_e_exclusivamente_experimental_e_sem_sql_operacional():
    ddl = _ddl_sem_comentarios()
    assert "automationx" not in ddl.lower()
    assert "op535" not in ddl.lower()
    assert "oc73_execucoes" not in ddl.lower()
    assert "oc73_tentativas" not in ddl.lower()
    assert not re.search(
        r"\b(DROP|ALTER|TRUNCATE|DELETE|INSERT|UPDATE|REPLACE|CALL)\b",
        ddl,
        re.IGNORECASE,
    )


def test_ddl_tipos_e_tamanhos_criticos_sao_coerentes():
    ddl = _ddl_sem_comentarios()
    assert ddl.count("id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT") == 5
    assert ddl.count("tentativa_id BIGINT UNSIGNED NOT NULL") == 3
    assert ddl.count("autorizacao_id BIGINT UNSIGNED NOT NULL") == 3
    assert ddl.count(
        "referencia VARCHAR(25) CHARACTER SET ascii COLLATE ascii_bin NOT NULL"
    ) == 2
    assert ddl.count(
        "revisao_codigo VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL"
    ) == 2
    assert ddl.count("session_id_hash CHAR(64)") == 2


def test_documentos_obrigatorios_existem_e_declaram_escopo_offline():
    for caminho in DOCUMENTOS.values():
        texto = caminho.read_text(encoding="utf-8")
        assert "offline" in texto.lower()
        assert "não execut" in texto.lower()
        assert "automationx" not in texto.lower()


def test_inventario_separa_origem_das_evidencias_e_nao_as_presume():
    texto = DOCUMENTOS["inventario"].read_text(encoding="utf-8")
    for categoria in (
        "Administrativa",
        "Declarada pelo servidor",
        "Obtida pelo driver",
        "Pendente",
    ):
        assert categoria in texto
    for literal in (
        "127.0.0.1",
        "3308",
        "oc73_autorizacao_transacional_test",
        "oc73_tester",
        "MySQL Community 5.7.32",
    ):
        assert literal in texto
    assert "A preencher" in texto


def test_provisionamento_separa_quatro_autorizacoes():
    texto = DOCUMENTOS["provisionamento"].read_text(encoding="utf-8")
    for escopo in ("Inventário", "Provisionamento/DDL", "Testes DML", "Limpeza"):
        assert escopo in texto
    assert "não autoriza" in texto.lower()
    for passo in range(1, 17):
        assert f"{passo}." in texto


def test_plano_transacional_documenta_todos_os_cenarios_e_controles():
    texto = DOCUMENTOS["testes"].read_text(encoding="utf-8")
    for numero in range(1, 15):
        assert f"T{numero:02d}" in texto
    for item in (
        "Pré-condições",
        "Dados sintéticos",
        "Conexões",
        "Ordem/locks e barreiras",
        "Transações",
        "Resultado esperado",
        "Evidência SQL",
        "Aprovação",
        "Interrupção",
        "Recuperação e limpeza",
    ):
        assert texto.count(item) >= 14
    assert re.search(r"CTRC\s+operacional", texto)
    assert "SSW real" in texto


def test_contrato_driver_cobre_integridade_flags_fechamento_e_segredos():
    texto = DOCUMENTOS["driver"].read_text(encoding="utf-8")
    for item in (
        "Versão",
        "Origem",
        "SHA-256",
        "autocommit=False",
        "DictCursor",
        "connect_timeout",
        "read_timeout",
        "write_timeout",
        "CLIENT.MULTI_STATEMENTS",
        "socket",
        "rollback",
        "close",
        "credenciais",
    ):
        assert item in texto


def test_solicitacao_nao_presume_aprovacao_e_exige_escopo_exato():
    texto = DOCUMENTOS["autorizacao"].read_text(encoding="utf-8")
    for campo in (
        "Responsável autorizador",
        "Responsável executor",
        "Escopo exato",
        "Ambiente permitido",
        "Instância",
        "Host",
        "Porta",
        "Schema",
        "Conta",
        "Operações autorizadas",
        "Operações proibidas",
        "Janela de execução",
        "Revisão de código",
        "Evidências obrigatórias",
        "Critérios de interrupção",
        "Recuperação",
        "Limpeza",
        "Validade",
    ):
        assert campo in texto
    assert "APROVAÇÃO NÃO CONCEDIDA" in texto
    assert "autorização genérica" in texto.lower()


def test_pacote_documental_nao_embute_credenciais_nem_execucao_automatica():
    combinado = "\n".join(
        caminho.read_text(encoding="utf-8") for caminho in DOCUMENTOS.values()
    )
    assert not re.search(r"(?i)(password|senha)\s*[:=]\s*\S+", combinado)
    assert "pymysql.connect(" not in combinado.lower()
    assert "subprocess." not in combinado
    assert "os.system(" not in combinado
