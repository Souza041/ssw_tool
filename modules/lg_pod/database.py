import logging
import os
from .config import load_env

logger = logging.getLogger(__name__)


def connect():
    load_env()
    import pymysql
    names = ['LG_POD_DB_HOST', 'LG_POD_DB_USER', 'LG_POD_DB_NAME']
    if any(not os.getenv(n, '').strip() for n in names):
        raise ValueError('Configure as variáveis LG_POD_DB_* no .env.')
    try:
        session_timeout = int(os.getenv('LG_POD_DB_SESSION_WAIT_TIMEOUT_SECONDS', '600'))
    except ValueError:
        raise ValueError('LG_POD_DB_SESSION_WAIT_TIMEOUT_SECONDS deve ser um número inteiro.') from None
    if not 60 <= session_timeout <= 3600:
        raise ValueError('LG_POD_DB_SESSION_WAIT_TIMEOUT_SECONDS deve estar entre 60 e 3600.')
    conn = pymysql.connect(
        host=os.environ['LG_POD_DB_HOST'], port=int(os.getenv('LG_POD_DB_PORT', '3306')),
        user=os.environ['LG_POD_DB_USER'], password=os.getenv('LG_POD_DB_PASSWORD', ''),
        database=os.environ['LG_POD_DB_NAME'], charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
        connect_timeout=15, read_timeout=60, write_timeout=60,
    )
    try:
        # Somente a conexão nova de LG POD. Não altera o GLOBAL do DBaaS.
        with conn.cursor() as cur:
            cur.execute('SET SESSION wait_timeout = %s', (session_timeout,))
            cur.execute('SELECT @@SESSION.wait_timeout AS seconds')
            effective = int(cur.fetchone()['seconds'])
        if effective != session_timeout:
            raise RuntimeError(
                f'O MySQL aplicou wait_timeout={effective}s; solicitado={session_timeout}s. '
                'Confirme com o provedor se o ajuste de sessão é permitido.')
        logger.info('LG POD MySQL: wait_timeout da sessão confirmado em %ss.', effective)
        return conn
    except Exception as exc:
        args = getattr(exc, 'args', ())
        code = args[0] if args and isinstance(args[0], int) else None
        logger.error('LG POD: falha ao configurar/verificar wait_timeout da sessão (%s, codigo=%s).',
                     type(exc).__name__, code)
        try:
            conn.close()
        except Exception:
            pass
        raise
