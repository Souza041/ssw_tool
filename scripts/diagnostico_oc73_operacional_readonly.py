"""Diagnóstico operacional estritamente consultivo das tabelas OC73.

Este módulo é independente do serviço, scheduler, gate e repositório da OC73.
Importá-lo não abre conexão. A execução exige confirmação explícita e o UUID
esperado do servidor MySQL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

import pymysql
from pymysql.cursors import DictCursor


SCHEMA_ESPERADO = "automationx"
VERSAO_ESPERADA = "5.7.32"
TABELAS_PERMITIDAS = ("oc73_execucoes", "oc73_tentativas")
LIMITE_GRUPOS = 101
MAX_EXECUTION_TIME_SOLICITADO_MS = 5_000
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 15
WRITE_TIMEOUT_S = 5
CONSULTAS_INTEGRIDADE = (
    "duplicidades_scheduler",
    "duplicidades_ctrc",
    "duplicidades_referencia",
    "tentativas_orfas",
    "integridade_estados",
)
SAIDA_CONFORME = 0
SAIDA_BLOQUEADA = 2
SAIDA_ERRO = 3

STATUS_EXECUCAO_PERMITIDOS = (
    "executando",
    "concluido",
    "concluido_com_pendencias",
    "erro",
)
STATUS_TENTATIVA_PERMITIDOS = (
    "reservado",
    "envio_iniciado",
    "incerto",
    "confirmado",
)

# Contrato derivado exclusivamente dos campos lidos/escritos pelo runtime.
# ``nullable``: False exige NOT NULL; True exige NULL; None aceita ambos.
CONTRATO_COLUNAS: Mapping[str, Mapping[str, Mapping[str, Any]]] = MappingProxyType({
    "oc73_execucoes": MappingProxyType({
        "id": {"tipos": ("bigint", "int"), "nullable": False},
        "data_referencia": {"tipos": ("date",), "nullable": False},
        "triggered_by": {"tipos": ("varchar", "char"), "nullable": False, "minimo": 18},
        "status": {"tipos": ("varchar", "char"), "nullable": False, "minimo": 23},
        "dry_run": {"tipos": ("tinyint", "bit", "boolean", "bool"), "nullable": False},
        "iniciado_em": {"tipos": ("datetime", "timestamp"), "nullable": False},
        "finalizado_em": {"tipos": ("datetime", "timestamp"), "nullable": True},
        "total_relatorio": {"tipos": ("int", "integer", "bigint"), "nullable": None, "omitida_no_insert": True},
        "total_filtrado": {"tipos": ("int", "integer", "bigint"), "nullable": None, "omitida_no_insert": True},
        "total_lancado": {"tipos": ("int", "integer", "bigint"), "nullable": None, "omitida_no_insert": True},
        "total_erros": {"tipos": ("int", "integer", "bigint"), "nullable": None, "omitida_no_insert": True},
        "mensagem_erro": {"tipos": ("varchar", "text", "mediumtext", "longtext"), "nullable": True, "minimo": 2000},
    }),
    "oc73_tentativas": MappingProxyType({
        "id": {"tipos": ("bigint", "int"), "nullable": False},
        "execucao_id": {"tipos": ("bigint", "int"), "nullable": False},
        "serie": {"tipos": ("varchar", "char"), "nullable": False, "minimo": 4},
        "numero": {"tipos": ("varchar", "char"), "nullable": False, "minimo": 1},
        "codigo_ocorrencia": {"tipos": ("int", "integer", "smallint", "tinyint"), "nullable": False},
        "fluxo": {"tipos": ("varchar", "char"), "nullable": False, "minimo": 3},
        "data_inicial_pesquisa": {"tipos": ("date",), "nullable": False},
        "data_final_pesquisa": {"tipos": ("date",), "nullable": False},
        "status": {"tipos": ("varchar", "char"), "nullable": False, "minimo": 14},
        "reservado_em": {"tipos": ("datetime", "timestamp"), "nullable": False},
        # NULL preserva compatibilidade com tentativas legadas sem REF.
        "referencia": {"tipos": ("varchar", "char"), "nullable": True, "minimo": 25, "collation_binaria": True},
        "envio_iniciado_em": {"tipos": ("datetime", "timestamp"), "nullable": True},
        "confirmado_em": {"tipos": ("datetime", "timestamp"), "nullable": True},
        "resposta_ssw": {"tipos": ("varchar", "text", "mediumtext", "longtext"), "nullable": True, "minimo": 2000},
        "mensagem_erro": {"tipos": ("varchar", "text", "mediumtext", "longtext"), "nullable": True, "minimo": 2000},
    }),
})

# Inventário fechado. Nenhum SQL é recebido por argumento ou variável externa.
# O limite 101 permite informar "mais de 100" sem retornar chaves operacionais.
SQL_FIXO: Mapping[str, str] = MappingProxyType({
    "iniciar_transacao_read_only": "START TRANSACTION READ ONLY",
    "identidade": (
        "SELECT @@version AS versao, @@version_comment AS distribuicao, "
        "@@server_uuid AS server_uuid, @@hostname AS hostname, "
        "@@port AS porta, DATABASE() AS banco"
    ),
    "tabelas": (
        "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, TABLE_ROWS "
        "FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = 'automationx' "
        "AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas') "
        "ORDER BY TABLE_NAME"
    ),
    "colunas": (
        "SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, "
        "IS_NULLABLE, COLUMN_DEFAULT, EXTRA, CHARACTER_SET_NAME, "
        "COLLATION_NAME, GENERATION_EXPRESSION "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = 'automationx' "
        "AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas') "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"
    ),
    "indices": (
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, "
        "COLUMN_NAME, SUB_PART, COLLATION, CARDINALITY, INDEX_TYPE "
        "FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = 'automationx' "
        "AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas') "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
    ),
    "constraints": (
        "SELECT tc.TABLE_NAME, tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE, "
        "kcu.ORDINAL_POSITION, kcu.COLUMN_NAME, "
        "kcu.REFERENCED_TABLE_NAME, kcu.REFERENCED_COLUMN_NAME "
        "FROM information_schema.TABLE_CONSTRAINTS AS tc "
        "LEFT JOIN information_schema.KEY_COLUMN_USAGE AS kcu "
        "ON kcu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA "
        "AND kcu.TABLE_NAME = tc.TABLE_NAME "
        "AND kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME "
        "WHERE tc.CONSTRAINT_SCHEMA = 'automationx' "
        "AND tc.TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas') "
        "ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION"
    ),
    "foreign_keys": (
        "SELECT CONSTRAINT_NAME, TABLE_NAME, REFERENCED_TABLE_NAME, "
        "UPDATE_RULE, DELETE_RULE "
        "FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = 'automationx' "
        "AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas') "
        "ORDER BY TABLE_NAME, CONSTRAINT_NAME"
    ),
    "ddl_execucoes": "SHOW CREATE TABLE automationx.oc73_execucoes",
    "ddl_tentativas": "SHOW CREATE TABLE automationx.oc73_tentativas",
    "duplicidades_scheduler": (
        "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
        "COUNT(*) AS grupos_ate_101, "
        "COALESCE(SUM(d.total - 1), 0) AS linhas_excedentes_ate_101 "
        "FROM ("
        "SELECT COUNT(*) AS total "
        "FROM automationx.oc73_execucoes "
        "WHERE triggered_by IN ('scheduler:CWB', 'scheduler:BIG') "
        "GROUP BY data_referencia, triggered_by "
        "HAVING COUNT(*) > 1 LIMIT 101"
        ") AS d"
    ),
    "duplicidades_ctrc": (
        "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
        "COUNT(*) AS grupos_ate_101, "
        "COALESCE(SUM(d.total - 1), 0) AS linhas_excedentes_ate_101 "
        "FROM ("
        "SELECT COUNT(*) AS total "
        "FROM automationx.oc73_tentativas "
        "GROUP BY serie, numero, codigo_ocorrencia "
        "HAVING COUNT(*) > 1 LIMIT 101"
        ") AS d"
    ),
    "duplicidades_referencia": (
        "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
        "COUNT(*) AS grupos_ate_101, "
        "COALESCE(SUM(d.total - 1), 0) AS linhas_excedentes_ate_101 "
        "FROM ("
        "SELECT COUNT(*) AS total "
        "FROM automationx.oc73_tentativas "
        "WHERE referencia IS NOT NULL AND referencia <> '' "
        "GROUP BY referencia HAVING COUNT(*) > 1 LIMIT 101"
        ") AS d"
    ),
    "tentativas_orfas": (
        "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
        "COUNT(*) AS total_ate_101 FROM ("
        "SELECT t.id "
        "FROM automationx.oc73_tentativas AS t "
        "LEFT JOIN automationx.oc73_execucoes AS e ON e.id = t.execucao_id "
        "WHERE e.id IS NULL LIMIT 101"
        ") AS o"
    ),
    "integridade_estados": (
        "SELECT /*+ MAX_EXECUTION_TIME(5000) */ "
        "(SELECT COUNT(*) FROM automationx.oc73_execucoes "
        "WHERE status IS NULL OR BINARY status NOT IN "
        "('executando', 'concluido', 'concluido_com_pendencias', 'erro')) "
        "AS execucoes_status_invalido, "
        "(SELECT COUNT(*) FROM automationx.oc73_tentativas "
        "WHERE status IS NULL OR BINARY status NOT IN "
        "('reservado', 'envio_iniciado', 'incerto', 'confirmado')) "
        "AS tentativas_status_invalido"
    ),
})


class DiagnosticoSeguroErro(RuntimeError):
    """Erro controlado que pode ser mostrado sem detalhes da conexão."""


class VerificacaoUnica(argparse.Action):
    """Recusa seleção repetida em vez de aceitar silenciosamente a última."""

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest) is not None:
            raise argparse.ArgumentError(self, "selecione apenas uma verificação")
        setattr(namespace, self.dest, values)


@dataclass(frozen=True)
class ConfiguracaoBanco:
    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def carregar(cls) -> "ConfiguracaoBanco":
        valores = {
            "host": os.getenv("AUTOMATION_DB_HOST", "").strip(),
            "database": os.getenv("AUTOMATION_DB_NAME", "").strip(),
            "user": os.getenv("AUTOMATION_DB_USER", "").strip(),
            "password": os.getenv("AUTOMATION_DB_PASSWORD", ""),
        }
        faltantes = [nome for nome, valor in valores.items() if not valor]
        if faltantes:
            raise DiagnosticoSeguroErro(
                "Configuração AUTOMATION_DB_* incompleta; conexão recusada."
            )

        porta_texto = os.getenv("AUTOMATION_DB_PORT", "3306").strip()
        try:
            porta = int(porta_texto)
        except ValueError as exc:
            raise DiagnosticoSeguroErro(
                "AUTOMATION_DB_PORT inválida; conexão recusada."
            ) from exc
        if not 1 <= porta <= 65535:
            raise DiagnosticoSeguroErro(
                "AUTOMATION_DB_PORT fora do intervalo; conexão recusada."
            )
        if valores["database"] != SCHEMA_ESPERADO:
            raise DiagnosticoSeguroErro(
                "Schema configurado não é o schema operacional esperado."
            )

        return cls(port=porta, **valores)


def validar_uuid_esperado(valor: str) -> str:
    uuid = str(valor or "").strip().lower()
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        uuid,
    ):
        raise DiagnosticoSeguroErro(
            "UUID esperado do servidor ausente ou inválido."
        )
    return uuid


def obter_sql(nome: str) -> str:
    """Retorna somente uma instrução presente no inventário auditável."""
    try:
        return SQL_FIXO[nome]
    except KeyError as exc:
        raise DiagnosticoSeguroErro(
            "Instrução fora do inventário fixo foi recusada."
        ) from exc


def executar_fixo(
    cursor,
    nome: str,
    *,
    possui_resultado: bool = True,
) -> list[dict[str, Any]]:
    sql = obter_sql(nome)
    cursor.execute(sql)
    if not possui_resultado:
        return []
    return list(cursor.fetchall())


def abrir_conexao(config: ConfiguracaoBanco):
    """Abre uma sessão dedicada sem MULTI_STATEMENTS e sem autocommit."""
    try:
        return pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=CONNECT_TIMEOUT_S,
            read_timeout=READ_TIMEOUT_S,
            write_timeout=WRITE_TIMEOUT_S,
        )
    except Exception as exc:
        raise DiagnosticoSeguroErro(
            "Não foi possível abrir a conexão de diagnóstico."
        ) from exc


def validar_identidade(
    identidade: dict[str, Any],
    config: ConfiguracaoBanco,
    server_uuid_esperado: str,
) -> None:
    versao = str(identidade.get("versao") or "")
    banco = str(identidade.get("banco") or "")
    uuid = str(identidade.get("server_uuid") or "").strip().lower()
    try:
        porta = int(identidade.get("porta"))
    except (TypeError, ValueError) as exc:
        raise DiagnosticoSeguroErro(
            "Identidade do servidor incompleta; auditoria recusada."
        ) from exc

    if not re.fullmatch(r"5\.7\.32(?:[-+].*)?", versao):
        raise DiagnosticoSeguroErro(
            "Versão MySQL diferente de 5.7.32; auditoria recusada."
        )
    if banco != SCHEMA_ESPERADO or banco != config.database:
        raise DiagnosticoSeguroErro(
            "Schema efetivo inesperado; auditoria recusada."
        )
    if porta != config.port:
        raise DiagnosticoSeguroErro(
            "Porta efetiva difere da configuração; auditoria recusada."
        )
    if uuid != server_uuid_esperado:
        raise DiagnosticoSeguroErro(
            "UUID do servidor não corresponde ao fingerprint aprovado."
        )


def _ddl(linhas: list[dict[str, Any]]) -> str:
    if len(linhas) != 1:
        raise DiagnosticoSeguroErro("DDL esperado não foi retornado.")
    for chave, valor in linhas[0].items():
        if str(chave).lower() == "create table":
            return str(valor)
    raise DiagnosticoSeguroErro("Resposta de DDL em formato inesperado.")


def _indices_por_nome(
    indices: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    agrupados: dict[tuple[str, str], dict[str, Any]] = {}
    for item in indices:
        tabela = str(item.get("TABLE_NAME") or "")
        nome = str(item.get("INDEX_NAME") or "")
        try:
            posicao = int(item.get("SEQ_IN_INDEX"))
            unico = int(item.get("NON_UNIQUE")) == 0
        except (TypeError, ValueError) as exc:
            raise DiagnosticoSeguroErro(
                "Metadados de índice incompletos; conformidade inconclusiva."
            ) from exc
        coluna = str(item.get("COLUMN_NAME") or "")
        if "SUB_PART" not in item:
            raise DiagnosticoSeguroErro(
                "Metadados de prefixo de índice ausentes; conformidade inconclusiva."
            )
        if not tabela or not nome or not coluna or posicao <= 0:
            raise DiagnosticoSeguroErro(
                "Metadados de índice incompletos; conformidade inconclusiva."
            )
        indice = agrupados.setdefault(
            (tabela, nome),
            {"unico": unico, "colunas_posicionadas": [], "prefixo": False},
        )
        if indice["unico"] != unico:
            raise DiagnosticoSeguroErro(
                "Metadados de índice inconsistentes; conformidade inconclusiva."
            )
        indice["colunas_posicionadas"].append((posicao, coluna))
        if item["SUB_PART"] is not None:
            indice["prefixo"] = True

    for indice in agrupados.values():
        posicionadas = sorted(indice.pop("colunas_posicionadas"))
        posicoes = [posicao for posicao, _ in posicionadas]
        if posicoes != list(range(1, len(posicoes) + 1)):
            raise DiagnosticoSeguroErro(
                "Sequência de índice incompleta; conformidade inconclusiva."
            )
        indice["colunas"] = [coluna for _, coluna in posicionadas]
    return agrupados


def _avaliar_unique(
    indices_agrupados: dict[tuple[str, str], dict[str, Any]],
    *,
    tabela: str,
    colunas_esperadas: tuple[str, ...],
    nome_esperado: str,
) -> dict[str, Any]:
    encontrados = [
        nome
        for (tabela_indice, nome), dados in indices_agrupados.items()
        if tabela_indice == tabela
        and dados["unico"]
        and not dados["prefixo"]
        and tuple(dados["colunas"]) == colunas_esperadas
    ]
    # O repository identifica o conflito pelo nome do índice retornado
    # pelo MySQL. Um índice equivalente com outro nome não cumpre esse
    # contrato, embora seja listado para facilitar o diagnóstico.
    return {
        "estado": "conforme" if nome_esperado in encontrados else "nao_conforme",
        "colunas_esperadas": list(colunas_esperadas),
        "indices_compativeis": encontrados,
        "nome_esperado": nome_esperado,
        "nome_esperado_presente": nome_esperado in encontrados,
    }


def _decompor_tipo(tipo_coluna: Any) -> tuple[str, int | None, bool] | None:
    texto = str(tipo_coluna or "").strip().lower()
    correspondencia = re.fullmatch(
        r"([a-z]+)(?:\((\d+)(?:,\d+)?\))?(?:\s+(unsigned))?",
        texto,
    )
    if not correspondencia:
        return None
    base, tamanho, unsigned = correspondencia.groups()
    return base, int(tamanho) if tamanho is not None else None, bool(unsigned)


def _tipo_suporta_tamanho(base: str, tamanho: int | None, minimo: int) -> bool:
    if base in {"text", "mediumtext", "longtext"}:
        capacidades = {"text": 65_535, "mediumtext": 16_777_215, "longtext": 4_294_967_295}
        return capacidades[base] >= minimo
    return tamanho is not None and tamanho >= minimo


def _avaliar_estrutura_colunas(
    colunas: list[dict[str, Any]],
    indices_agrupados: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    por_chave: dict[tuple[str, str], dict[str, Any]] = {}
    metadados_duplicados: list[str] = []
    for item in colunas:
        chave = (
            str(item.get("TABLE_NAME") or ""),
            str(item.get("COLUMN_NAME") or ""),
        )
        if chave in por_chave:
            metadados_duplicados.append(".".join(chave))
        por_chave[chave] = item

    ausentes: list[str] = []
    incompativeis: list[dict[str, Any]] = []
    inconclusivos: list[dict[str, Any]] = []
    for tabela, contrato_tabela in CONTRATO_COLUNAS.items():
        for coluna, regra in contrato_tabela.items():
            chave = (tabela, coluna)
            item = por_chave.get(chave)
            identificador = f"{tabela}.{coluna}"
            if item is None:
                ausentes.append(identificador)
                continue

            campos_necessarios = {"COLUMN_TYPE", "IS_NULLABLE", "EXTRA"}
            if regra.get("omitida_no_insert"):
                campos_necessarios.add("COLUMN_DEFAULT")
            if regra.get("collation_binaria"):
                campos_necessarios.add("COLLATION_NAME")
            faltantes = sorted(
                campo for campo in campos_necessarios if campo not in item
            )
            if faltantes:
                inconclusivos.append({
                    "coluna": identificador,
                    "metadados_ausentes": faltantes,
                })
                continue

            tipo = _decompor_tipo(item.get("COLUMN_TYPE"))
            if tipo is None:
                inconclusivos.append({
                    "coluna": identificador,
                    "motivo": "column_type_nao_interpretavel",
                })
                continue
            base, tamanho, _ = tipo
            problemas: list[str] = []
            if base not in regra["tipos"]:
                problemas.append("tipo_incompativel")
            minimo = regra.get("minimo")
            if minimo is not None and base in regra["tipos"]:
                if not _tipo_suporta_tamanho(base, tamanho, int(minimo)):
                    problemas.append("capacidade_insuficiente")

            nullable = str(item.get("IS_NULLABLE") or "").upper()
            if nullable not in {"YES", "NO"}:
                inconclusivos.append({
                    "coluna": identificador,
                    "motivo": "is_nullable_invalido",
                })
                continue
            esperado_nullable = regra.get("nullable")
            if esperado_nullable is False and nullable != "NO":
                problemas.append("campo_obrigatorio_permite_null")
            elif esperado_nullable is True and nullable != "YES":
                problemas.append("campo_opcional_exige_valor")
            if (
                regra.get("omitida_no_insert")
                and nullable == "NO"
                and item.get("COLUMN_DEFAULT") is None
            ):
                problemas.append("insert_omite_campo_sem_default")

            if regra.get("collation_binaria"):
                collation = str(item.get("COLLATION_NAME") or "").lower()
                if not collation.endswith("_bin"):
                    problemas.append("collation_nao_case_sensitive")
            if problemas:
                incompativeis.append({
                    "coluna": identificador,
                    "problemas": problemas,
                })

    pks: dict[str, Any] = {}
    for tabela in TABELAS_PERMITIDAS:
        primary = indices_agrupados.get((tabela, "PRIMARY"))
        id_meta = por_chave.get((tabela, "id"))
        if primary is None or not primary["unico"] or primary["colunas"] != ["id"]:
            pks[tabela] = {"estado": "nao_conforme", "motivo": "pk_id_ausente_ou_invalida"}
        elif id_meta is None:
            pks[tabela] = {"estado": "nao_conforme", "motivo": "coluna_id_ausente"}
        elif "EXTRA" not in id_meta:
            pks[tabela] = {"estado": "inconclusivo", "motivo": "extra_ausente"}
        elif "auto_increment" not in str(id_meta.get("EXTRA") or "").lower().split():
            pks[tabela] = {"estado": "nao_conforme", "motivo": "auto_increment_ausente"}
        else:
            pks[tabela] = {"estado": "conforme"}

    if ausentes or incompativeis or any(
        item["estado"] == "nao_conforme" for item in pks.values()
    ):
        estado = "nao_conforme"
    elif inconclusivos or metadados_duplicados or any(
        item["estado"] == "inconclusivo" for item in pks.values()
    ):
        estado = "inconclusivo"
    else:
        estado = "conforme"
    return {
        "estado": estado,
        "colunas_obrigatorias_ausentes": ausentes,
        "colunas_incompativeis": incompativeis,
        "metadados_inconclusivos": inconclusivos,
        "metadados_duplicados": metadados_duplicados,
        "chaves_primarias": pks,
    }


def _tipos_fk_compativeis(
    colunas: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    por_chave = {
        (str(item.get("TABLE_NAME") or ""), str(item.get("COLUMN_NAME") or "")): item
        for item in colunas
    }
    origem = por_chave.get(("oc73_tentativas", "execucao_id"))
    destino = por_chave.get(("oc73_execucoes", "id"))
    if origem is None or destino is None:
        return "nao_conforme", {"motivo": "coluna_fk_ou_pk_ausente"}
    if "COLUMN_TYPE" not in origem or "COLUMN_TYPE" not in destino:
        return "inconclusivo", {"motivo": "column_type_ausente"}
    tipo_origem = _decompor_tipo(origem.get("COLUMN_TYPE"))
    tipo_destino = _decompor_tipo(destino.get("COLUMN_TYPE"))
    if tipo_origem is None or tipo_destino is None:
        return "inconclusivo", {"motivo": "column_type_nao_interpretavel"}
    # Largura de exibição de inteiros em 5.7 não integra a compatibilidade.
    compativel = (
        tipo_origem[0] == tipo_destino[0]
        and tipo_origem[2] == tipo_destino[2]
    )
    return (
        "conforme" if compativel else "nao_conforme",
        {
            "tipo_origem": str(origem.get("COLUMN_TYPE")),
            "tipo_destino": str(destino.get("COLUMN_TYPE")),
        },
    )


def avaliar_conformidade_schema(
    *,
    tabelas: list[dict[str, Any]],
    colunas: list[dict[str, Any]],
    indices: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    foreign_keys: list[dict[str, Any]],
) -> dict[str, Any]:
    """Avalia o contrato exigido pelo runtime com os metadados coletados."""
    indices_agrupados = _indices_por_nome(indices)
    estrutura_colunas = _avaliar_estrutura_colunas(colunas, indices_agrupados)
    tabelas_por_nome = {
        str(item.get("TABLE_NAME") or ""): item for item in tabelas
    }
    engines = {
        tabela: str(tabelas_por_nome.get(tabela, {}).get("ENGINE") or "")
        for tabela in TABELAS_PERMITIDAS
    }
    engine_inconclusivo = any(
        tabela not in tabelas_por_nome
        or "ENGINE" not in tabelas_por_nome[tabela]
        or not engines[tabela]
        for tabela in TABELAS_PERMITIDAS
    )
    engine_conforme = not engine_inconclusivo and all(
        engines[tabela].upper() == "INNODB"
        for tabela in TABELAS_PERMITIDAS
    )
    engine = {
        "estado": (
            "inconclusivo"
            if engine_inconclusivo
            else "conforme" if engine_conforme else "nao_conforme"
        ),
        "esperado": "InnoDB",
        "encontrado": engines,
    }

    unique_ctrc = _avaliar_unique(
        indices_agrupados,
        tabela="oc73_tentativas",
        colunas_esperadas=("serie", "numero", "codigo_ocorrencia"),
        nome_esperado="uq_oc73_ctrc",
    )
    unique_referencia = _avaliar_unique(
        indices_agrupados,
        tabela="oc73_tentativas",
        colunas_esperadas=("referencia",),
        nome_esperado="uq_oc73_referencia",
    )

    fks_execucao = []
    for item in constraints:
        if (
            str(item.get("CONSTRAINT_TYPE") or "").upper()
            == "FOREIGN KEY"
            and item.get("TABLE_NAME") == "oc73_tentativas"
            and item.get("COLUMN_NAME") == "execucao_id"
            and item.get("REFERENCED_TABLE_NAME") == "oc73_execucoes"
            and item.get("REFERENCED_COLUMN_NAME") == "id"
        ):
            fks_execucao.append(str(item.get("CONSTRAINT_NAME") or ""))
    regras_fk = [
        {
            "constraint": item.get("CONSTRAINT_NAME"),
            "update_rule": item.get("UPDATE_RULE"),
            "delete_rule": item.get("DELETE_RULE"),
        }
        for item in foreign_keys
        if item.get("TABLE_NAME") == "oc73_tentativas"
        and item.get("REFERENCED_TABLE_NAME") == "oc73_execucoes"
        and item.get("CONSTRAINT_NAME") in fks_execucao
    ]
    estado_tipo_fk, detalhe_tipo_fk = _tipos_fk_compativeis(colunas)
    fk_execucao = {
        "estado_relacao_minima": (
            "conforme" if fks_execucao else "nao_conforme"
        ),
        "estado_compatibilidade_tipos": estado_tipo_fk,
        "detalhe_compatibilidade_tipos": detalhe_tipo_fk,
        "relacao_esperada": (
            "oc73_tentativas.execucao_id -> oc73_execucoes.id"
        ),
        "constraints_compativeis": fks_execucao,
        "regras_encontradas": regras_fk,
        "conformidade_regras_ddl_oficial": "inconclusiva",
        "motivo_inconclusivo": (
            "DDL oficial completo não está versionado no repositório; "
            "não é seguro presumir regras ON UPDATE/ON DELETE."
        ),
    }

    expressoes_geradas = {
        (str(item.get("TABLE_NAME")), str(item.get("COLUMN_NAME"))):
        str(item.get("GENERATION_EXPRESSION") or "").lower()
        for item in colunas
    }
    indices_fluxo_data = []
    for (tabela, nome), dados in indices_agrupados.items():
        if tabela != "oc73_execucoes" or not dados["unico"]:
            continue
        nomes_colunas = set(dados["colunas"])
        composto = nomes_colunas in (
            {"data_referencia", "fluxo"},
            {"data_referencia", "triggered_by"},
        )
        gerado = any(
            "data_referencia" in expressoes_geradas.get((tabela, coluna), "")
            and (
                "fluxo" in expressoes_geradas.get((tabela, coluna), "")
                or "triggered_by"
                in expressoes_geradas.get((tabela, coluna), "")
            )
            for coluna in dados["colunas"]
        )
        if composto or gerado:
            indices_fluxo_data.append({
                "indice": nome,
                "colunas": dados["colunas"],
                "via_coluna_gerada": gerado,
            })
    unique_fluxo_data = {
        "estado": "presente" if indices_fluxo_data else "ausente",
        "indices_compativeis": indices_fluxo_data,
        "observacao": (
            "A ausência não invalida o protocolo GET_LOCK, mas confirma que "
            "não há backstop UNIQUE detectável para fluxo/data."
        ),
    }

    verificacoes_minimas = [
        engine["estado"],
        estrutura_colunas["estado"],
        unique_ctrc["estado"],
        unique_referencia["estado"],
        fk_execucao["estado_relacao_minima"],
        fk_execucao["estado_compatibilidade_tipos"],
    ]
    if "nao_conforme" in verificacoes_minimas:
        contrato_minimo = "nao_conforme"
    elif "inconclusivo" in verificacoes_minimas:
        contrato_minimo = "inconclusivo"
    else:
        contrato_minimo = "conforme"
    return {
        "estado_coleta": (
            "inconclusivo"
            if contrato_minimo == "inconclusivo"
            else "completo"
        ),
        "contrato_minimo": contrato_minimo,
        "ddl_oficial_completo": {
            "estado": "inconclusivo",
            "motivo": (
                "Não foi localizado DDL oficial completo versionado para "
                "comparar nomes e regras referenciais."
            ),
        },
        "engine": engine,
        "estrutura_colunas": estrutura_colunas,
        "unique_ctrc": unique_ctrc,
        "unique_referencia": unique_referencia,
        "fk_execucao": fk_execucao,
        "unique_fluxo_data": unique_fluxo_data,
    }


def _resultado_integridade(
    linha: dict[str, Any],
    *,
    campo_quantidade: str,
    campo_excedentes: str | None = None,
) -> dict[str, Any]:
    quantidade_observada = int(linha[campo_quantidade])
    if quantidade_observada < 0:
        raise ValueError("contagem agregada invalida")
    limitado = quantidade_observada >= LIMITE_GRUPOS
    resultado: dict[str, Any] = {
        "estado_coleta": "limitado" if limitado else "completo",
        "quantidade_exata": None if limitado else quantidade_observada,
        "quantidade_reportada": min(quantidade_observada, 100),
        "pelo_menos": LIMITE_GRUPOS if limitado else quantidade_observada,
        "limite_atingido": limitado,
    }
    if campo_excedentes is not None:
        excedentes = int(linha[campo_excedentes])
        if excedentes < 0:
            raise ValueError("contagem agregada invalida")
        resultado["linhas_excedentes_observadas"] = excedentes
        resultado["excedentes_sao_parciais"] = limitado
    return resultado


def _consultar_integridade(cursor, verificacao: str) -> dict[str, Any]:
    """Executa exatamente uma instrução agregada da allowlist."""
    if verificacao not in CONSULTAS_INTEGRIDADE:
        raise DiagnosticoSeguroErro("Verificação operacional inválida.")
    try:
        linhas = executar_fixo(cursor, verificacao)
        if len(linhas) != 1:
            raise ValueError("resultado agregado inesperado")
        if verificacao == "integridade_estados":
            execucoes = int(linhas[0]["execucoes_status_invalido"])
            tentativas = int(linhas[0]["tentativas_status_invalido"])
            if execucoes < 0 or tentativas < 0:
                raise ValueError("contagem agregada invalida")
            return {
                "estado_coleta": "completo",
                "estado_conformidade": (
                    "conforme" if execucoes == tentativas == 0 else "nao_conforme"
                ),
                "execucoes_status_invalido": execucoes,
                "tentativas_status_invalido": tentativas,
            }
        campos = {
            "duplicidades_scheduler": ("grupos_ate_101", "linhas_excedentes_ate_101"),
            "duplicidades_ctrc": ("grupos_ate_101", "linhas_excedentes_ate_101"),
            "duplicidades_referencia": ("grupos_ate_101", "linhas_excedentes_ate_101"),
            "tentativas_orfas": ("total_ate_101", None),
        }
        quantidade, excedentes = campos[verificacao]
        return _resultado_integridade(
            linhas[0], campo_quantidade=quantidade, campo_excedentes=excedentes,
        )
    except Exception:
        falha = {
            "estado_coleta": "inconclusivo",
            "motivo": "erro_consulta",
        }
        if verificacao == "integridade_estados":
            falha["execucoes_status_invalido"] = None
            falha["tentativas_status_invalido"] = None
        else:
            falha["quantidade_exata"] = None
        return falha


def resumir_resultado(resultado: Mapping[str, Any]) -> dict[str, Any]:
    """Classifica somente estados controlados, sem copiar identidade, DDL ou linhas."""
    escopo = resultado.get("escopo")
    if escopo not in ("apenas_schema", "schema_e_integridade", "verificacao_operacional"):
        raise DiagnosticoSeguroErro("Escopo do diagnóstico inválido.")
    selecionada = resultado.get("verificacao_selecionada")
    if escopo == "verificacao_operacional" and selecionada not in CONSULTAS_INTEGRIDADE:
        raise DiagnosticoSeguroErro("Verificação operacional inválida.")

    schema = resultado.get("conformidade_schema")
    estado_schema = schema.get("contrato_minimo") if isinstance(schema, Mapping) else None
    if estado_schema not in ("conforme", "nao_conforme", "inconclusivo"):
        estado_schema = "inconclusivo"
    verificacoes: dict[str, dict[str, str]] = {
        "contrato_minimo_schema": {"estado": estado_schema}
    }
    if escopo != "apenas_schema":
        coleta_geral = resultado.get("estado_coleta_integridade")
        verificacoes["coleta_integridade"] = {
            "estado": (
                "conforme" if coleta_geral == "completo" else
                "nao_conforme" if coleta_geral == "limitado" else "inconclusivo"
            ),
            "estado_coleta": coleta_geral if coleta_geral in (
                "completo", "limitado", "inconclusivo"
            ) else "inconclusivo",
        }
        integridade = resultado.get("integridade")
        for nome in CONSULTAS_INTEGRIDADE:
            if escopo == "verificacao_operacional" and nome != selecionada:
                verificacoes[nome] = {
                    "estado": "nao_executada", "estado_coleta": "nao_executada"
                }
                continue
            item = integridade.get(nome) if isinstance(integridade, Mapping) else None
            estado = "inconclusivo"
            coleta = "inconclusivo"
            if isinstance(item, Mapping):
                coleta_original = item.get("estado_coleta")
                if coleta_original == "nao_executada" and escopo == "verificacao_operacional":
                    verificacoes[nome] = {
                        "estado": "nao_executada", "estado_coleta": "nao_executada"
                    }
                    continue
                if coleta_original in ("completo", "limitado", "inconclusivo"):
                    coleta = coleta_original
                if nome == "integridade_estados":
                    execucoes = item.get("execucoes_status_invalido")
                    tentativas = item.get("tentativas_status_invalido")
                    if (coleta == "completo" and type(execucoes) is int
                            and type(tentativas) is int and execucoes >= 0
                            and tentativas >= 0):
                        estado = "conforme" if execucoes == tentativas == 0 else "nao_conforme"
                else:
                    quantidade = item.get("quantidade_exata")
                    if coleta == "completo" and type(quantidade) is int and quantidade >= 0:
                        estado = "conforme" if quantidade == 0 else "nao_conforme"
                    elif coleta == "limitado" and item.get("pelo_menos") == LIMITE_GRUPOS:
                        estado = "nao_conforme"
            verificacoes[nome] = {"estado": estado, "estado_coleta": coleta}

    estados = {
        item["estado"] for item in verificacoes.values()
        if item["estado"] != "nao_executada"
    }
    if (escopo == "verificacao_operacional"
            and verificacoes[selecionada]["estado"] == "nao_executada"):
        estados.add("inconclusivo")
    conformidade = (
        "inconclusivo" if "inconclusivo" in estados else
        "nao_conforme" if "nao_conforme" in estados else "conforme"
    )
    motivos = [
        f"{nome}:{item['estado']}"
        for nome, item in verificacoes.items()
        if item["estado"] not in ("conforme", "nao_executada")
    ]
    if (escopo == "verificacao_operacional"
            and verificacoes[selecionada]["estado"] == "nao_executada"):
        motivos.append(f"{selecionada}:nao_executada")
    resumo = {
        "escopo": escopo,
        "conformidade": conformidade,
        "verificacoes": verificacoes,
        "limites": {
            "max_execution_time_solicitado_ms": MAX_EXECUTION_TIME_SOLICITADO_MS,
            "read_timeout_cliente_s": READ_TIMEOUT_S,
            "connect_timeout_cliente_s": CONNECT_TIMEOUT_S,
            "write_timeout_cliente_s": WRITE_TIMEOUT_S,
            "max_grupos_reportados": 100,
            "garantia_temporal": "sem_limite_total_rigido",
            "registros_operacionais_exportados": 0,
        },
        "motivos_bloqueio": motivos,
    }
    if escopo == "verificacao_operacional":
        resumo["verificacao_selecionada"] = selecionada
    return resumo


def diagnosticar(
    config: ConfiguracaoBanco,
    *,
    server_uuid_esperado: str,
    connection_factory=abrir_conexao,
    apenas_schema: bool = False,
    verificacao: str | None = None,
    confirmar_agregacao: bool = False,
) -> dict[str, Any]:
    """Executa o inventário fixo e sempre encerra com rollback e close."""
    if apenas_schema:
        if verificacao is not None or confirmar_agregacao:
            raise DiagnosticoSeguroErro("Modos de diagnóstico incompatíveis.")
    elif verificacao not in CONSULTAS_INTEGRIDADE or not confirmar_agregacao:
        raise DiagnosticoSeguroErro(
            "Modo completo indisponível; selecione uma verificação e confirme a agregação."
        )
    conexao = None
    try:
        conexao = connection_factory(config)
        with conexao.cursor() as cursor:
            executar_fixo(
                cursor,
                "iniciar_transacao_read_only",
                possui_resultado=False,
            )
            identidades = executar_fixo(cursor, "identidade")
            if len(identidades) != 1:
                raise DiagnosticoSeguroErro(
                    "Identidade do servidor não pôde ser comprovada."
                )
            identidade = identidades[0]
            validar_identidade(identidade, config, server_uuid_esperado)

            tabelas = executar_fixo(cursor, "tabelas")
            nomes = {str(item.get("TABLE_NAME")) for item in tabelas}
            if nomes != set(TABELAS_PERMITIDAS):
                raise DiagnosticoSeguroErro(
                    "Conjunto de tabelas OC73 inesperado; auditoria recusada."
                )

            colunas = executar_fixo(cursor, "colunas")
            indices = executar_fixo(cursor, "indices")
            constraints = executar_fixo(cursor, "constraints")
            foreign_keys = executar_fixo(cursor, "foreign_keys")
            ddl_execucoes = _ddl(executar_fixo(cursor, "ddl_execucoes"))
            ddl_tentativas = _ddl(executar_fixo(cursor, "ddl_tentativas"))

            conformidade_schema = avaliar_conformidade_schema(
                tabelas=tabelas,
                colunas=colunas,
                indices=indices,
                constraints=constraints,
                foreign_keys=foreign_keys,
            )
            if apenas_schema:
                integridade, estado_integridade = None, "nao_consultada"
            else:
                integridade = {
                    nome: {"estado_coleta": "nao_executada", "motivo": "nao_selecionada"}
                    for nome in CONSULTAS_INTEGRIDADE
                }
                if conformidade_schema["contrato_minimo"] == "conforme":
                    integridade[verificacao] = _consultar_integridade(cursor, verificacao)
                    estado_integridade = integridade[verificacao]["estado_coleta"]
                else:
                    integridade[verificacao] = {
                        "estado_coleta": "nao_executada",
                        "motivo": "schema_nao_conforme",
                    }
                    estado_integridade = "nao_executada"

            por_tabela = {}
            for tabela in TABELAS_PERMITIDAS:
                por_tabela[tabela] = {
                    "metadados": [
                        {
                            **item,
                            "TABLE_ROWS_EXATO": (
                                False
                                if str(item.get("ENGINE", "")).upper()
                                == "INNODB"
                                else None
                            ),
                            "TABLE_ROWS_NATUREZA": (
                                "estimativa_aproximada_innodb"
                                if str(item.get("ENGINE", "")).upper()
                                == "INNODB"
                                else "dependente_da_engine"
                            ),
                        }
                        for item in tabelas
                        if item.get("TABLE_NAME") == tabela
                    ],
                    "colunas": [
                        item for item in colunas
                        if item.get("TABLE_NAME") == tabela
                    ],
                    "indices": [
                        item for item in indices
                        if item.get("TABLE_NAME") == tabela
                    ],
                    "constraints": [
                        item for item in constraints
                        if item.get("TABLE_NAME") == tabela
                    ],
                    "foreign_keys": [
                        item for item in foreign_keys
                        if item.get("TABLE_NAME") == tabela
                    ],
                    "ddl": (
                        ddl_execucoes
                        if tabela == "oc73_execucoes"
                        else ddl_tentativas
                    ),
                }

            return {
                "modo": "somente_leitura",
                "escopo": "apenas_schema" if apenas_schema else "verificacao_operacional",
                **({"verificacao_selecionada": verificacao} if not apenas_schema else {}),
                "isolamento": (
                    "transacao_read_only_com_allowlist_sql; a conta ainda pode "
                    "possuir privilégios de escrita fora deste processo"
                ),
                "identidade": {
                    "host_configurado": config.host,
                    "porta": int(identidade["porta"]),
                    "banco": identidade["banco"],
                    "versao": identidade["versao"],
                    "distribuicao": identidade["distribuicao"],
                    "server_uuid": identidade["server_uuid"],
                    "hostname": identidade["hostname"],
                },
                "tabelas": por_tabela,
                "conformidade_schema": conformidade_schema,
                "estado_coleta_integridade": estado_integridade,
                "integridade": integridade,
                "limites": {
                    "max_execution_time_solicitado_ms": (
                        MAX_EXECUTION_TIME_SOLICITADO_MS
                    ),
                    "alcance_max_execution_time": (
                        "hint global na instrucao SELECT inteira, incluindo "
                        "a subquery derivada"
                    ),
                    "garantia_temporal": (
                        "timeout solicitado ao servidor; nao constitui limite "
                        "rigido de tempo de parede e nao cobre conexao, rede "
                        "nem as demais consultas"
                    ),
                    "read_timeout_cliente_s": READ_TIMEOUT_S,
                    "connect_timeout_cliente_s": CONNECT_TIMEOUT_S,
                    "write_timeout_cliente_s": WRITE_TIMEOUT_S,
                    "max_grupos_reportados": 100,
                    "registros_operacionais_exportados": 0,
                },
            }
    except DiagnosticoSeguroErro:
        raise
    except Exception as exc:
        raise DiagnosticoSeguroErro(
            "Falha interna durante o diagnóstico; operação abortada."
        ) from exc
    finally:
        if conexao is not None:
            falha_encerramento = False
            try:
                conexao.rollback()
            except Exception:
                falha_encerramento = True
            try:
                conexao.close()
            except Exception:
                falha_encerramento = True
            if falha_encerramento and sys.exc_info()[0] is None:
                raise DiagnosticoSeguroErro(
                    "Encerramento da sessão não pôde ser comprovado."
                )


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Auditoria consultiva das tabelas OC73 no MySQL operacional."
        )
    )
    parser.add_argument(
        "--somente-leitura",
        action="store_true",
        help="Confirma que a execução é exclusivamente diagnóstica.",
    )
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument(
        "--apenas-schema",
        action="store_true",
        help="Consulta somente identidade e metadados estruturais; sem agregados de registros.",
    )
    modo.add_argument(
        "--verificacao", choices=CONSULTAS_INTEGRIDADE,
        action=VerificacaoUnica,
        help="Seleciona exatamente uma agregação operacional do inventário fixo.",
    )
    parser.add_argument(
        "--confirmar-agregacao", action="store_true",
        help="Confirma explicitamente a execução da agregação selecionada.",
    )
    formato = parser.add_mutually_exclusive_group()
    formato.add_argument(
        "--resumo", action="store_true",
        help="Exibe somente estados e limites, sem identidade do servidor ou DDL (padrão).",
    )
    formato.add_argument(
        "--completo", action="store_true",
        help="Exibe o relatório técnico integral, incluindo identidade e DDL.",
    )
    parser.add_argument(
        "--server-uuid-esperado",
        required=True,
        help="Fingerprint previamente aprovado do servidor MySQL.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = criar_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return SAIDA_CONFORME if exc.code == 0 else SAIDA_ERRO
    if not args.somente_leitura:
        print("ERRO SEGURO: --somente-leitura é obrigatório", file=sys.stderr)
        return SAIDA_ERRO
    if args.apenas_schema and args.confirmar_agregacao:
        print("ERRO SEGURO: confirmação incompatível com --apenas-schema.", file=sys.stderr)
        return SAIDA_ERRO
    if args.verificacao and args.completo:
        print("ERRO SEGURO: verificação operacional exige saída resumida.", file=sys.stderr)
        return SAIDA_ERRO
    if not args.apenas_schema and (not args.verificacao or not args.confirmar_agregacao):
        print(
            "ERRO SEGURO: modo completo indisponível; use --apenas-schema ou "
            "--verificacao com --confirmar-agregacao.", file=sys.stderr,
        )
        return SAIDA_ERRO

    try:
        uuid_esperado = validar_uuid_esperado(args.server_uuid_esperado)
        config = ConfiguracaoBanco.carregar()
        resultado = diagnosticar(
            config,
            server_uuid_esperado=uuid_esperado,
            apenas_schema=args.apenas_schema,
            verificacao=args.verificacao,
            confirmar_agregacao=args.confirmar_agregacao,
        )
        resumo = resumir_resultado(resultado)
    except DiagnosticoSeguroErro as exc:
        print(f"ERRO SEGURO: {exc}", file=sys.stderr)
        return SAIDA_ERRO

    print(json.dumps(resultado if args.completo else resumo,
                     ensure_ascii=False, indent=2, default=str))
    return SAIDA_CONFORME if resumo["conformidade"] == "conforme" else SAIDA_BLOQUEADA


if __name__ == "__main__":
    sys.exit(main())
