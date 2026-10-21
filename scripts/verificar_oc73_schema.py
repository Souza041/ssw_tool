import os
import pymysql
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

conn = pymysql.connect(
    host=os.environ["AUTOMATION_DB_HOST"],
    port=int(os.getenv("AUTOMATION_DB_PORT", "3306")),
    user=os.environ["AUTOMATION_DB_USER"],
    password=os.environ["AUTOMATION_DB_PASSWORD"],
    database=os.environ["AUTOMATION_DB_NAME"],
    connect_timeout=10,
    read_timeout=10,
    autocommit=False,
)

try:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DATABASE(), @@port, @@version"
        )
        print("Banco / porta / versão:", cursor.fetchone())

        cursor.execute("""
            SELECT TABLE_NAME, ENGINE
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN (
                  'oc73_tentativas',
                  'oc73_execucoes'
              )
            ORDER BY TABLE_NAME
        """)
        print("\nTabelas:")
        for linha in cursor.fetchall():
            print(linha)

        cursor.execute("""
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE,
                   IS_NULLABLE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN (
                  'oc73_tentativas',
                  'oc73_execucoes'
              )
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """)
        print("\nColunas:")
        for linha in cursor.fetchall():
            print(linha)

        cursor.execute("""
            SELECT TABLE_NAME, INDEX_NAME,
                   NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN (
                  'oc73_tentativas',
                  'oc73_execucoes'
              )
            ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
        """)
        print("\nÍndices:")
        for linha in cursor.fetchall():
            print(linha)

finally:
    conn.rollback()
    conn.close()