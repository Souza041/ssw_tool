"""Executar na raiz: python -m scripts.instalar_lg_pod"""
from pathlib import Path
from modules.lg_pod.database import connect


def main():
    conn = connect()
    try:
        schema = Path(__file__).resolve().parents[1] / 'modules/lg_pod/schema.sql'
        with conn.cursor() as cur:
            for statement in schema.read_text(encoding='utf-8').split(';'):
                if statement.strip():
                    cur.execute(statement)
        print('LG POD: tabelas instaladas no banco configurado em LG_POD_DB_NAME.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
