from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Iterator

import pymysql
from pymysql.connections import Connection


def get_connection(*, autocommit: bool = False) -> Connection:
    return pymysql.connect(
        host=os.getenv("DOCUMENTOS_DB_HOST", "documentosgce.mysql.dbaas.com.br"),
        port=int(os.getenv("DOCUMENTOS_DB_PORT", "3306")),
        user=os.getenv("DOCUMENTOS_DB_USER", "documentosgce"),
        password=os.getenv("DOCUMENTOS_DB_PASSWORD", "Rodobras@2026"),
        database=os.getenv("DOCUMENTOS_DB_NAME", "documentosgce"),
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

