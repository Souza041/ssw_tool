"""Uma única thread possui a conexão e executa consultas e keepalive em série.
Nunca reconecta nem repete SQL: perder a conexão também perde GET_LOCK.
"""
from concurrent.futures import Future, TimeoutError as FutureTimeout
import logging
import os
import re
import queue
import threading
import time

logger = logging.getLogger(__name__)


def error_code(exc):
    args = getattr(exc, 'args', ())
    return args[0] if args and isinstance(args[0], int) else None


def safe_error_message(exc):
    message = str(exc)
    for name in tuple(os.environ):
        if not (name.startswith('LG_POD_') and name.endswith('PASSWORD')):
            continue
        secret = os.getenv(name, '')
        if secret:
            message = message.replace(secret, '[redigido]')
    message = re.sub(r'https?://[^\s]+', '[URL]', message)
    return message.replace('\n', ' ')[:400]


class DatabaseWorker:
    def __init__(self, factory, lock_name, interval=5.0, timeout=150.0):
        if interval <= 0 or timeout <= 0:
            raise ValueError('Intervalos do banco devem ser positivos.')
        self.factory, self.lock_name = factory, lock_name
        self.interval, self.timeout = interval, timeout
        self.submission_lock = threading.Lock()
        self.jobs = queue.Queue()
        self.ready = Future()
        self.failure = None
        self.last_success = None
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name='lg-pod-mysql', daemon=True)
        self.thread.start()
        try:
            self.ready.result(timeout=self.timeout)
        except BaseException:
            self.close()
            raise

    def _sql(self, conn, sql, params=(), one=False):
        started = time.monotonic()
        idle = 0.0 if self.last_success is None else started-self.last_success
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description:
                    result = cur.fetchone() if one else cur.fetchall()
                else:
                    result = cur.lastrowid
            self.last_success = time.monotonic()
            return result
        except Exception as exc:
            logger.error('LG POD MySQL: %s; codigo=%s; consulta=%.1fs; desde_ultimo_sucesso=%.1fs; '
                         'operacao=%s; detalhe=%s', type(exc).__name__, error_code(exc),
                         time.monotonic()-started, idle, ' '.join(sql.split()[:5]),
                         safe_error_message(exc))
            raise

    def _verify(self, conn):
        row = self._sql(conn, 'SELECT IS_USED_LOCK(%s) = CONNECTION_ID() AS ok',
                        (self.lock_name,), one=True)
        if not row or row['ok'] != 1:
            raise RuntimeError('Bloqueio MySQL perdido; nenhuma nova ação será enviada ao portal.')

    def _run(self):
        conn, locked, pending = None, False, None
        try:
            conn = self.factory()  # Criada, usada e fechada nesta mesma thread.
            self.ready.set_result(True)
            while not self.stopping.is_set():
                try:
                    job = self.jobs.get(timeout=self.interval if locked else 0.5)
                except queue.Empty:
                    if locked:
                        self._verify(conn)  # Atividade mesmo enquanto o portal demora.
                    continue
                if job is None:
                    break
                kind, sql, params, one, pending = job
                if kind == 'lock':
                    row = self._sql(conn, 'SELECT GET_LOCK(%s,0) AS ok', (self.lock_name,), True)
                    locked = bool(row and row['ok'] == 1)
                    if locked:
                        wait = self._sql(conn, 'SELECT @@SESSION.wait_timeout AS seconds', one=True)
                        seconds = float(wait['seconds'])
                        self.interval = min(self.interval, max(0.05, seconds / 3))
                        logger.info('LG POD: bloqueio adquirido; wait_timeout efetivo=%.0fs; keepalive a cada %.2fs.',
                                    seconds, self.interval)
                    result = locked
                elif kind == 'assert':
                    if not locked:
                        raise RuntimeError('Bloqueio de execução não adquirido.')
                    self._verify(conn)
                    result = None
                else:
                    result = self._sql(conn, sql, params, one)
                pending.set_result(result)
                pending = None
        except BaseException as exc:
            self.failure = exc
            if not self.ready.done():
                self.ready.set_exception(exc)
            if pending is not None and not pending.done():
                pending.set_exception(exc)
        finally:
            with self.submission_lock:
                self.stopping.set()
            if conn is not None:
                if locked and self.failure is None:
                    try:
                        self._sql(conn, 'SELECT RELEASE_LOCK(%s)', (self.lock_name,))
                    except Exception as exc:
                        # A limpeza nunca substitui a falha da execução.
                        logger.warning('LG POD: falha ao liberar bloqueio (%s, codigo=%s).',
                                       type(exc).__name__, error_code(exc))
                try:
                    conn.close()
                except Exception:
                    logger.warning('LG POD: conexão não pôde ser fechada normalmente.')
            while True:
                try:
                    job = self.jobs.get_nowait()
                except queue.Empty:
                    break
                if job is not None and not job[-1].done():
                    job[-1].set_exception(self.failure or RuntimeError('Conexão LG POD encerrada.'))

    def request(self, kind, sql='', params=(), one=False):
        future = Future()
        with self.submission_lock:
            if self.failure is not None:
                raise self.failure
            if self.stopping.is_set():
                raise RuntimeError('Conexão LG POD encerrada.')
            self.jobs.put((kind, sql, params, one, future))
        try:
            return future.result(timeout=self.timeout)
        except FutureTimeout:
            self.stopping.set()
            raise RuntimeError('Tempo limite de comunicação com a thread MySQL atingido; execução interrompida.') from None

    def close(self):
        self.stopping.set()
        self.jobs.put(None)
        self.thread.join(timeout=1.0)
