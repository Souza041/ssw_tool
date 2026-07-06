import os
import pymysql
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return pymysql.connect(
        host=os.getenv("METRICAS_DB_HOST", "localhost"),
        port=int(os.getenv("METRICAS_DB_PORT", "3306")),
        user=os.getenv("METRICAS_DB_USER", "root"),
        password=os.getenv("METRICAS_DB_PASSWORD", ""),
        database=os.getenv("METRICAS_DB_NAME", "metricas"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )