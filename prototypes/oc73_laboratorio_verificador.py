"""Verificador consultivo e independente do laboratório transacional OC73.

Não cria conexão sozinho, não lê `.env`, não executa DDL/DML e não importa o
runtime. Uma fábrica de conexão deve ser fornecida explicitamente por teste ou
por uma futura ferramenta autorizada.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping


HOST_PERMITIDO = "127.0.0.1"
PORTA_PERMITIDA = 3308
SCHEMA_PERMITIDO = "oc73_autorizacao_transacional_test"
USUARIO_PERMITIDO = "oc73_tester"
VERSOES_PERMITIDAS = frozenset({"5.7.32", "5.7.32-log"})
DISTRIBUICOES_PERMITIDAS = frozenset({"MySQL Community Server (GPL)"})
TABELAS_PERMITIDAS = frozenset({
    "oc73_lab_tentativas",
    "oc73_lab_autorizacoes",
    "oc73_lab_aprovacoes",
    "oc73_lab_consumos",
    "oc73_lab_auditoria",
})

PERFIS_PRIVILEGIOS = frozenset({"consultivo", "transacional"})
PRIVILEGIOS_GLOBAIS_INOFENSIVOS = frozenset({"USAGE"})
PRIVILEGIOS_PROIBIDOS = frozenset({
    "ALL", "ALL PRIVILEGES", "GRANT OPTION", "SUPER", "FILE", "PROCESS",
    "CREATE USER", "RELOAD", "SHUTDOWN", "CREATE", "ALTER", "DROP",
    "INDEX", "REFERENCES", "DELETE", "EXECUTE", "TRIGGER", "EVENT",
    "LOCK TABLES", "REPLICATION CLIENT", "REPLICATION SLAVE",
})
_GRANT_RE = re.compile(
    r"^GRANT (?P<privilegios>[A-Z]+(?: [A-Z]+)*(?:, [A-Z]+(?: [A-Z]+)*)*) ON "
    r"(?P<escopo>\*\.\*|`[A-Za-z0-9_]+`\.(?:\*|`[A-Za-z0-9_]+`)) "
    r"TO '(?P<usuario>[A-Za-z0-9_.-]+)'@'(?P<host>[A-Za-z0-9_.:-]+)'$"
)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

SQL_IDENTIDADE = (
    "SELECT DATABASE() AS schema_efetivo, @@hostname AS hostname, "
    "@@port AS porta, @@version AS versao, "
    "@@version_comment AS comentario_versao, "
    "@@server_uuid AS server_uuid, @@datadir AS datadir, "
    "CURRENT_USER() AS usuario_efetivo, USER() AS usuario_conexao"
)
SQL_TABELAS = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = %s ORDER BY table_name"
)
SQL_GRANTS = "SHOW GRANTS FOR CURRENT_USER"
SQL_PERMITIDO = frozenset({SQL_IDENTIDADE, SQL_TABELAS, SQL_GRANTS})


class LaboratorioRejeitado(RuntimeError):
    """Falha sanitizada, sem credencial, DSN ou detalhes da conexão."""


@dataclass(frozen=True)
class ConfiguracaoLaboratorio:
    host: str
    porta: int
    schema: str
    usuario: str
    conta_efetiva_esperada: str
    usuario_conexao_esperado: str
    server_uuid_esperado: str
    datadir_esperado: str
    versao_esperada: str
    distribuicao_esperada: str
    hostname_esperado: str | None
    perfil_privilegios: str
    autorizacao_explicita: bool
    unix_socket: str | None = None


@dataclass(frozen=True)
class RelatorioLaboratorio:
    identidade_validada: bool
    versao: str
    hostname: str
    tabelas_encontradas: tuple[str, ...]
    grants_validados: int


def construir_configuracao(valores: Mapping[str, Any]) -> ConfiguracaoLaboratorio:
    campos = {
        "host", "porta", "schema", "usuario", "conta_efetiva_esperada",
        "usuario_conexao_esperado", "server_uuid_esperado",
        "datadir_esperado", "versao_esperada", "distribuicao_esperada",
        "hostname_esperado", "perfil_privilegios", "autorizacao_explicita",
        "unix_socket",
    }
    desconhecidos = set(valores) - campos
    if desconhecidos:
        raise LaboratorioRejeitado("Parâmetros de conexão inesperados.")
    try:
        return ConfiguracaoLaboratorio(**dict(valores))
    except (TypeError, ValueError) as exc:
        raise LaboratorioRejeitado("Configuração de laboratório inválida.") from exc


def validar_configuracao(config: ConfiguracaoLaboratorio) -> None:
    if config.autorizacao_explicita is not True:
        raise LaboratorioRejeitado("Autorização explícita ausente.")
    if (
        not isinstance(config.host, str)
        or config.host != HOST_PERMITIDO
        or type(config.porta) is not int
        or config.porta != PORTA_PERMITIDA
        or not isinstance(config.schema, str)
        or config.schema != SCHEMA_PERMITIDO
        or not isinstance(config.usuario, str)
        or config.usuario != USUARIO_PERMITIDO
    ):
        raise LaboratorioRejeitado("Endpoint fora da allowlist do laboratório.")
    if config.porta == 3306 or config.schema.casefold() == "automationx":
        raise LaboratorioRejeitado("Ambiente operacional é proibido.")
    if config.unix_socket is not None:
        raise LaboratorioRejeitado("Conexão por socket é proibida.")
    if (
        not _conta_mysql_valida(config.conta_efetiva_esperada, config.usuario)
        or not _conta_mysql_valida(
            config.usuario_conexao_esperado, config.usuario
        )
    ):
        raise LaboratorioRejeitado("Identidade MySQL esperada é inválida.")
    if (
        not isinstance(config.server_uuid_esperado, str)
        or _UUID_RE.fullmatch(config.server_uuid_esperado) is None
    ):
        raise LaboratorioRejeitado("UUID esperado é obrigatório.")
    try:
        if str(uuid.UUID(config.server_uuid_esperado)) != config.server_uuid_esperado:
            raise ValueError
    except ValueError as exc:
        raise LaboratorioRejeitado("UUID esperado é inválido.") from exc
    _validar_datadir(config.datadir_esperado)
    if (
        type(config.versao_esperada) is not str
        or config.versao_esperada not in VERSOES_PERMITIDAS
        or type(config.distribuicao_esperada) is not str
        or config.distribuicao_esperada not in DISTRIBUICOES_PERMITIDAS
    ):
        raise LaboratorioRejeitado("Produto MySQL esperado é inválido.")
    if config.hostname_esperado is not None and (
        not isinstance(config.hostname_esperado, str)
        or not config.hostname_esperado.strip()
        or len(config.hostname_esperado) > 255
    ):
        raise LaboratorioRejeitado("Hostname esperado é inválido.")
    if config.perfil_privilegios not in PERFIS_PRIVILEGIOS:
        raise LaboratorioRejeitado("Perfil de privilégios inválido.")


def _conta_mysql_valida(valor: Any, usuario: str) -> bool:
    if not isinstance(valor, str):
        return False
    correspondencia = re.fullmatch(
        r"(?P<usuario>[A-Za-z0-9_.-]+)@(?P<host>[A-Za-z0-9_.:-]+)",
        valor,
    )
    return bool(
        correspondencia
        and correspondencia.group("usuario") == usuario
        and correspondencia.group("host") not in {"%", "*"}
    )


def _validar_datadir(valor: Any) -> str:
    if not isinstance(valor, str) or not valor or valor != valor.strip():
        raise LaboratorioRejeitado("Diretório de dados esperado é obrigatório.")
    canonico = valor.replace("\\", "/")
    absoluto_posix = canonico.startswith("/")
    absoluto_windows = bool(re.match(r"^[A-Za-z]:/", canonico))
    partes = canonico.rstrip("/").split("/")
    if (
        not (absoluto_posix or absoluto_windows)
        or not canonico.endswith("/")
        or "//" in canonico
        or any(parte in {"", ".", ".."} for parte in partes[1:])
        or "\x00" in canonico
    ):
        raise LaboratorioRejeitado("Diretório de dados esperado é ambíguo.")
    return canonico


def _valor_grant(linha: Any) -> str:
    if isinstance(linha, Mapping):
        return str(next(iter(linha.values()), ""))
    if isinstance(linha, (tuple, list)) and linha:
        return str(linha[0])
    return ""


def validar_grants(
    grants: tuple[str, ...], config: ConfiguracaoLaboratorio
) -> int:
    escopos_esperados = _escopos_privilegios(config)
    encontrados: dict[str, set[str]] = {}
    if not grants:
        raise LaboratorioRejeitado("Privilégios MySQL não puderam ser comprovados.")
    for grant in grants:
        correspondencia, privilegios = _interpretar_grant(grant)
        conta = (
            f"{correspondencia.group('usuario')}@"
            f"{correspondencia.group('host')}"
        )
        if conta != config.conta_efetiva_esperada:
            raise LaboratorioRejeitado("Grant pertence a conta inesperada.")
        escopo = correspondencia.group("escopo")
        if escopo == "*.*":
            if not privilegios.issubset(PRIVILEGIOS_GLOBAIS_INOFENSIVOS):
                raise LaboratorioRejeitado("Grant global incompatível.")
            continue
        if escopo not in escopos_esperados:
            raise LaboratorioRejeitado("Grant aponta para schema não autorizado.")
        if not privilegios or not privilegios.issubset(
            escopos_esperados[escopo]
        ):
            raise LaboratorioRejeitado("Privilégio MySQL incompatível.")
        if escopo in encontrados and privilegios.issubset(encontrados[escopo]):
            raise LaboratorioRejeitado("Grant MySQL duplicado.")
        encontrados.setdefault(escopo, set()).update(privilegios)
    if encontrados != {
        escopo: set(privilegios)
        for escopo, privilegios in escopos_esperados.items()
    }:
        raise LaboratorioRejeitado("Privilégios mínimos não foram comprovados.")
    return len(grants)


def _interpretar_grant(grant: Any):
    if not isinstance(grant, str) or grant != grant.strip():
        raise LaboratorioRejeitado("Grant MySQL não interpretável.")
    correspondencia = _GRANT_RE.fullmatch(grant)
    if correspondencia is None:
        raise LaboratorioRejeitado("Grant MySQL não interpretável.")
    tokens = [
        item.strip() for item in correspondencia.group("privilegios").split(",")
    ]
    if not tokens or any(not item for item in tokens) or len(tokens) != len(set(tokens)):
        raise LaboratorioRejeitado("Privilégio MySQL repetido ou inválido.")
    privilegios = set(tokens)
    conhecidos = (
        PRIVILEGIOS_GLOBAIS_INOFENSIVOS
        | PRIVILEGIOS_PROIBIDOS
        | frozenset({"SELECT", "INSERT", "UPDATE"})
    )
    if not privilegios.issubset(conhecidos):
        raise LaboratorioRejeitado("Privilégio MySQL desconhecido.")
    if privilegios & PRIVILEGIOS_PROIBIDOS:
        raise LaboratorioRejeitado("Privilégio MySQL incompatível.")
    return correspondencia, privilegios


def _escopos_privilegios(
    config: ConfiguracaoLaboratorio,
) -> dict[str, frozenset[str]]:
    schema = f"`{config.schema}`"
    if config.perfil_privilegios == "consultivo":
        return {f"{schema}.*": frozenset({"SELECT"})}
    return {
        f"{schema}.*": frozenset({"SELECT"}),
        f"{schema}.`oc73_lab_tentativas`": frozenset({"INSERT", "UPDATE"}),
        f"{schema}.`oc73_lab_autorizacoes`": frozenset({"INSERT", "UPDATE"}),
        f"{schema}.`oc73_lab_aprovacoes`": frozenset({"INSERT"}),
        f"{schema}.`oc73_lab_consumos`": frozenset({"INSERT"}),
        f"{schema}.`oc73_lab_auditoria`": frozenset({"INSERT"}),
    }


def verificar_laboratorio(
    config: ConfiguracaoLaboratorio,
    connection_factory: Callable[[], Any],
) -> RelatorioLaboratorio:
    """Executa somente o inventário fixo após a validação estática."""
    validar_configuracao(config)
    conexao = None
    try:
        conexao = connection_factory()
        with conexao.cursor() as cursor:
            cursor.execute(SQL_IDENTIDADE)
            identidade = cursor.fetchone() or {}
            if not isinstance(identidade, Mapping):
                raise LaboratorioRejeitado("Identidade do servidor inválida.")

            versao = str(identidade.get("versao") or "")
            distribuicao = str(identidade.get("comentario_versao") or "")
            datadir = str(identidade.get("datadir") or "").replace("\\", "/")
            if (
                identidade.get("schema_efetivo") != config.schema
                or type(identidade.get("porta")) is not int
                or identidade.get("porta") != config.porta
                or str(identidade.get("usuario_efetivo") or "")
                != config.conta_efetiva_esperada
                or str(identidade.get("usuario_conexao") or "")
                != config.usuario_conexao_esperado
                or str(identidade.get("server_uuid") or "")
                != config.server_uuid_esperado
                or datadir != _validar_datadir(config.datadir_esperado)
                or versao != config.versao_esperada
                or distribuicao != config.distribuicao_esperada
                or "mariadb" in versao.casefold()
                or "mariadb" in distribuicao.casefold()
                or (
                    config.hostname_esperado is not None
                    and str(identidade.get("hostname") or "")
                    != config.hostname_esperado
                )
            ):
                raise LaboratorioRejeitado("Identidade do servidor divergente.")

            cursor.execute(SQL_TABELAS, (config.schema,))
            linhas_tabelas = cursor.fetchall() or []
            tabelas = tuple(
                str(
                    linha.get("table_name")
                    if isinstance(linha, Mapping)
                    else linha[0]
                )
                for linha in linhas_tabelas
            )
            if not set(tabelas).issubset(TABELAS_PERMITIDAS):
                raise LaboratorioRejeitado("Tabela fora do escopo experimental.")

            cursor.execute(SQL_GRANTS)
            grants = tuple(
                item for item in (_valor_grant(linha) for linha in (
                    cursor.fetchall() or []
                )) if item
            )
            quantidade_grants = validar_grants(grants, config)

        return RelatorioLaboratorio(
            identidade_validada=True,
            versao=versao,
            hostname=str(identidade.get("hostname") or ""),
            tabelas_encontradas=tabelas,
            grants_validados=quantidade_grants,
        )
    except LaboratorioRejeitado:
        raise
    except Exception:
        raise LaboratorioRejeitado(
            "Não foi possível validar o laboratório."
        ) from None
    finally:
        if conexao is not None:
            erro_limpeza = False
            try:
                conexao.rollback()
            except Exception:
                erro_limpeza = True
            try:
                conexao.close()
            except Exception:
                erro_limpeza = True
            if erro_limpeza:
                raise LaboratorioRejeitado(
                    "Falha ao encerrar conexão de laboratório."
                ) from None
