from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator

import pymysql
from pymysql.connections import Connection
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Variável obrigatória ausente: {name}")
    return value


def get_connection(*, autocommit: bool = False) -> Connection:
    return pymysql.connect(
        host=_required_env("DOCUMENTOS_DB_HOST"),
        port=int(os.getenv("DOCUMENTOS_DB_PORT", "3306")),
        user=_required_env("DOCUMENTOS_DB_USER"),
        password=_required_env("DOCUMENTOS_DB_PASSWORD"),
        database=_required_env("DOCUMENTOS_DB_NAME"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=autocommit,
        connect_timeout=int(os.getenv("DOCUMENTOS_DB_CONNECT_TIMEOUT", "15")),
        read_timeout=int(os.getenv("DOCUMENTOS_DB_READ_TIMEOUT", "120")),
        write_timeout=int(os.getenv("DOCUMENTOS_DB_WRITE_TIMEOUT", "120")),
    )


@contextmanager
def transaction() -> Iterator[Connection]:
    connection = get_connection(autocommit=False)
    try:
        yield connection
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            # A conexão pode já ter sido derrubada pelo servidor.
            pass
        raise
    finally:
        try:
            connection.close()
        except Exception:
            pass


def test_connection() -> dict:
    connection = get_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT DATABASE() AS database_name, VERSION() AS database_version"
            )
            return cursor.fetchone()
    finally:
        connection.close()

