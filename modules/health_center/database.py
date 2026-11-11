"""Conexão exclusiva do Health Center; não altera os demais clientes MySQL."""

import logging
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pymysql
from dotenv import load_dotenv
from pymysql.connections import Connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Variável obrigatória ausente: {name}")
    return value


def _timeout(name: str) -> float:
    value = float(os.getenv(name, "2"))
    if not math.isfinite(value) or not 0 < value <= 31536000:
        raise ValueError(f"Timeout inválido: {name}")
    return value


def _warning(stage: str, error: Exception) -> None:
    # Mensagens do driver podem incluir detalhes de conexão; só a classe é segura.
    try:
        logger.warning(
            "Health Center: falha em %s (%s).", stage, type(error).__name__
        )
    except Exception:
        pass


def get_connection() -> Connection:
    """Abre uma transação com limites de socket próprios do monitor."""
    load_dotenv(PROJECT_ROOT / ".env")
    return pymysql.connect(
        host=_required_env("AUTOMATION_DB_HOST"),
        port=int(os.getenv("AUTOMATION_DB_PORT", "3306")),
        user=_required_env("AUTOMATION_DB_USER"),
        password=_required_env("AUTOMATION_DB_PASSWORD"),
        database=_required_env("AUTOMATION_DB_NAME"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=_timeout("HEALTH_DB_CONNECT_TIMEOUT"),
        read_timeout=_timeout("HEALTH_DB_READ_TIMEOUT"),
        write_timeout=_timeout("HEALTH_DB_WRITE_TIMEOUT"),
    )


def _rollback(connection: Connection) -> None:
    try:
        connection.rollback()
    except Exception as error:
        _warning("rollback", error)


@contextmanager
def transaction() -> Iterator[Connection]:
    """Uma conexão por chamada; sem retry e com fechamento em qualquer saída."""
    connection = get_connection()
    try:
        try:
            yield connection
        except BaseException:
            _rollback(connection)
            raise
        else:
            try:
                connection.commit()
            except BaseException:
                _rollback(connection)
                raise
    finally:
        try:
            connection.close()
        except Exception as error:
            # Commit pode ter sido confirmado. Falha de close não deve transformar
            # um início confirmado em RUNNING órfão nem mascarar o erro do bot.
            _warning("fechamento", error)
            force_close = getattr(connection, "_force_close", None)
            if callable(force_close):
                try:
                    force_close()
                except Exception as force_error:
                    _warning("fechamento forçado", force_error)


@contextmanager
def read_transaction() -> Iterator[Connection]:
    """Sessão de leitura explícita, sem commit ou mutação de estado."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
        yield connection
    finally:
        _rollback(connection)
        try:
            connection.close()
        except Exception as error:
            _warning("fechamento de leitura", error)
            force_close = getattr(connection, "_force_close", None)
            if callable(force_close):
                try:
                    force_close()
                except Exception as force_error:
                    _warning("fechamento forçado de leitura", force_error)
