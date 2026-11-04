import os
from .config import load_env


def connect():
    load_env()
    import pymysql
    names = ['LG_POD_DB_HOST', 'LG_POD_DB_USER', 'LG_POD_DB_NAME']
    if any(not os.getenv(n, '').strip() for n in names):
        raise ValueError('Configure as variáveis LG_POD_DB_* no .env.')
    return pymysql.connect(
        host=os.environ['LG_POD_DB_HOST'], port=int(os.getenv('LG_POD_DB_PORT', '3306')),
        user=os.environ['LG_POD_DB_USER'], password=os.getenv('LG_POD_DB_PASSWORD', ''),
        database=os.environ['LG_POD_DB_NAME'], charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
        connect_timeout=15, read_timeout=60, write_timeout=60,
    )
