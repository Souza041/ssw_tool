"""Fábrica experimental e inerte de conexão ao laboratório OC73.

O módulo não importa driver, ambiente ou runtime. Construir a fábrica não abre
conexão. A chamada futura da fábrica permanece condicionada a autorização.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from prototypes.oc73_laboratorio_verificador import (
    ConfiguracaoLaboratorio,
    validar_configuracao,
)


class ConexaoLaboratorioErro(RuntimeError):
    pass


@dataclass(frozen=True)
class CredencialLaboratorio:
    senha: str = field(repr=False)

    def validar(self) -> None:
        if not isinstance(self.senha, str) or not self.senha:
            raise ConexaoLaboratorioErro("Credencial de laboratório inválida.")


@dataclass(frozen=True)
class ContratoDriver:
    pacote: str
    versao_esperada: str | None
    origem: str
    integridade_sha256: str | None
    capabilities: frozenset[str]
    inventario_confirmado: bool

    def validar(self) -> None:
        esperadas = frozenset({"dict_cursor", "timeouts", "client_flag_zero"})
        if (
            self.pacote != "PyMySQL"
            or self.inventario_confirmado is not True
            or not isinstance(self.versao_esperada, str)
            or not self.versao_esperada.strip()
            or not isinstance(self.origem, str)
            or not self.origem.strip()
            or not isinstance(self.integridade_sha256, str)
            or len(self.integridade_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.integridade_sha256)
            or self.capabilities != esperadas
        ):
            raise ConexaoLaboratorioErro("Contrato do driver não comprovado.")


@dataclass(frozen=True)
class ParametrosConexaoAuditados:
    connect_timeout: int = 5
    read_timeout: int = 5
    write_timeout: int = 5
    charset: str = "utf8mb4"
    autocommit: bool = False
    client_flag: int = 0

    def validar(self) -> None:
        if (
            type(self.connect_timeout) is not int
            or type(self.read_timeout) is not int
            or type(self.write_timeout) is not int
            or not 1 <= self.connect_timeout <= 10
            or not 1 <= self.read_timeout <= 30
            or not 1 <= self.write_timeout <= 30
            or self.charset != "utf8mb4"
            or self.autocommit is not False
            or type(self.client_flag) is not int
            or self.client_flag != 0
        ):
            raise ConexaoLaboratorioErro("Parâmetros de conexão inseguros.")


def construir_fabrica_conexao(
    *,
    config: ConfiguracaoLaboratorio,
    credencial: CredencialLaboratorio,
    contrato_driver: ContratoDriver,
    driver: Any,
    parametros: ParametrosConexaoAuditados | None = None,
) -> Callable[[], Any]:
    """Retorna closure inerte; não chama ``driver.connect`` na construção."""
    validar_configuracao(config)
    credencial.validar()
    contrato_driver.validar()
    parametros = parametros or ParametrosConexaoAuditados()
    parametros.validar()
    try:
        connect = driver.connect
        cursorclass = driver.cursors.DictCursor
    except AttributeError as exc:
        raise ConexaoLaboratorioErro("Driver de laboratório incompatível.") from exc
    if not callable(connect):
        raise ConexaoLaboratorioErro("Driver de laboratório incompatível.")

    opcoes = {
        "host": config.host,
        "port": config.porta,
        "user": config.usuario,
        "password": credencial.senha,
        "database": config.schema,
        "charset": parametros.charset,
        "cursorclass": cursorclass,
        "autocommit": parametros.autocommit,
        "connect_timeout": parametros.connect_timeout,
        "read_timeout": parametros.read_timeout,
        "write_timeout": parametros.write_timeout,
        # Zero não habilita CLIENT.MULTI_STATEMENTS. O driver real ainda deve
        # ser verificado em laboratório para comprovar os flags efetivos.
        "client_flag": parametros.client_flag,
    }

    def conectar():
        try:
            return connect(**opcoes)
        except Exception:
            raise ConexaoLaboratorioErro(
                "Não foi possível abrir conexão com o laboratório."
            ) from None

    return conectar
