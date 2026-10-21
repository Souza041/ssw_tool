import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def conectar():
    obrigatorias = (
        "LG_DB_HOST",
        "LG_DB_NAME",
        "LG_DB_USER",
        "LG_DB_PASSWORD",
    )

    ausentes = [
        nome for nome in obrigatorias
        if not os.getenv(nome)
    ]

    if ausentes:
        raise RuntimeError(
            "Configurações ausentes: " + ", ".join(ausentes)
        )

    return pymysql.connect(
        host=os.environ["LG_DB_HOST"],
        port=int(os.getenv("LG_DB_PORT", "3306")),
        user=os.environ["LG_DB_USER"],
        password=os.environ["LG_DB_PASSWORD"],
        database=os.environ["LG_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=15,
        read_timeout=60,
        write_timeout=60,
        autocommit=False,
    )
