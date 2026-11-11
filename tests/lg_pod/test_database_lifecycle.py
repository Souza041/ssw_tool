import sqlite3
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from modules.lg_pod.repository import Repository
from modules.lg_pod.service import execute
from modules.lg_pod import service
from datetime import date


class OperationalError(Exception): pass
class InterfaceError(Exception): pass


class Server:
    def __init__(self, idle=1.0, clock=None):
        self.idle = idle
        self.clock = clock or time.monotonic
        self.advance_on_verify = None
        self.connections = []
        self.owner = None
        self.heartbeats = 0
        self.enough_heartbeats = threading.Event()
        self.verify_failed = threading.Event()
        self.release_error = False
        self.mutex = threading.Lock()

    def connect(self):
        conn = Connection(self, len(self.connections)+1)
        self.connections.append(conn)
        return conn


class Connection:
    def __init__(self, server, identifier):
        self.server, self.identifier = server, identifier
        self.thread = threading.get_ident()
        self.last_activity = server.clock()
        self.sql = []
        self.closed = False

    def cursor(self):
        assert threading.get_ident() == self.thread, 'Conexão compartilhada entre threads!'
        return Cursor(self)

    def close(self):
        assert threading.get_ident() == self.thread
        self.closed = True
        if self.server.owner == self.identifier:
            self.server.owner = None


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.description, self.result, self.lastrowid = None, None, 0

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def execute(self, sql, params):
        c, s = self.conn, self.conn.server
        c.sql.append(sql)
        if c.closed:
            raise InterfaceError(0, '')
        if 'IS_USED_LOCK' in sql and s.advance_on_verify is not None:
            s.advance_on_verify()
        current = s.clock()
        if current-c.last_activity > s.idle:
            c.closed = True
            raise OperationalError(2013, 'Lost connection to MySQL server during query')
        c.last_activity = current
        self.description = True
        if 'GET_LOCK(' in sql:
            ok = s.owner in (None, c.identifier)
            if ok: s.owner = c.identifier
            self.result = {'ok':int(ok)}
        elif '@@SESSION.wait_timeout' in sql:
            self.result = {'seconds':s.idle}
        elif 'IS_USED_LOCK' in sql:
            ok = s.owner == c.identifier
            self.result = {'ok':int(ok)}
            if not ok: s.verify_failed.set()
            s.heartbeats += 1
            if s.heartbeats >= 15: s.enough_heartbeats.set()
        elif 'RELEASE_LOCK' in sql:
            if s.release_error:
                raise OperationalError(2013, 'release failed')
            s.owner = None
            self.result = {'ok':1}
        elif sql == 'BROKEN':
            c.closed = True
            raise OperationalError(2013, 'Lost connection to MySQL server during query')
        else:
            self.result = {'value':42}

    def fetchone(self): return self.result
    def fetchall(self): return [self.result]


class DatabaseLifecycleTests(unittest.TestCase):
    def test_keepalive_survives_idle_period_without_sharing_connection(self):
        clock = [0.0]
        server = Server(idle=15.0, clock=lambda: clock[0])
        server.advance_on_verify = lambda: clock.__setitem__(0, clock[0]+2.0)
        with Repository(connection_factory=server.connect, heartbeat_interval=0.02) as repo:
            self.assertTrue(repo.lock())
            # Tempo de servidor simulado: 15 heartbeats x 2s = 30s, maior que o timeout de 15s.
            self.assertTrue(server.enough_heartbeats.wait(5))
            self.assertGreater(clock[0], server.idle)
            self.assertEqual(repo.query('SELECT value', one=True), {'value':42})
            repo.assert_lock()
        self.assertEqual(len(server.connections), 1)
        self.assertTrue(server.connections[0].closed)
        self.assertIsNone(server.owner)

    def test_second_execution_cannot_acquire_existing_lock(self):
        server = Server()
        with Repository(connection_factory=server.connect) as first:
            self.assertTrue(first.lock())
            with Repository(connection_factory=server.connect) as second:
                self.assertFalse(second.lock())
                with self.assertRaises(RuntimeError): second.assert_lock()
            first.assert_lock()

    def test_broken_query_is_never_replayed_or_reconnected(self):
        server = Server()
        with Repository(connection_factory=server.connect) as repo:
            self.assertTrue(repo.lock())
            with self.assertRaises(OperationalError) as original:
                repo.query('BROKEN')
            with self.assertRaises(OperationalError) as subsequent:
                repo.query('SELECT value')
            self.assertIs(original.exception, subsequent.exception)
        self.assertEqual(len(server.connections), 1)
        sql = server.connections[0].sql
        self.assertEqual(sql[-1], 'BROKEN')
        self.assertNotIn('SELECT RELEASE_LOCK(%s)', sql)

    def test_lost_lock_stops_worker_instead_of_reacquiring(self):
        server = Server()
        with Repository(connection_factory=server.connect, heartbeat_interval=0.02) as repo:
            repo.lock()
            server.owner = 999
            self.assertTrue(server.verify_failed.wait(2))
            with self.assertRaisesRegex(RuntimeError, 'Bloqueio MySQL perdido'):
                repo.assert_lock()
        self.assertEqual(sum('GET_LOCK(' in q for q in server.connections[0].sql), 1)

    def test_cleanup_error_never_masks_body_exception(self):
        server = Server()
        server.release_error = True
        with self.assertRaisesRegex(ValueError, 'erro original'):
            with Repository(connection_factory=server.connect) as repo:
                repo.lock()
                raise ValueError('erro original')
        self.assertTrue(server.connections[0].closed)

    def test_idle_timeout_without_activity_reproduces_2013(self):
        # Evidencia a diferença: cliente parado sem lock não ganha keepalive.
        server = Server(idle=0.01)
        with Repository(connection_factory=server.connect) as repo:
            server.connections[0].last_activity -= 1
            with self.assertRaises(OperationalError) as exc:
                repo.query('SELECT value')
        self.assertEqual(exc.exception.args[0], 2013)


class RecoveryRepo:
    def __init__(self, events, fail_status=False):
        self.events, self.fail_status = events, fail_status
        self.status = []
    def __enter__(self): self.events.append('enter'); return self
    def __exit__(self,*args): self.events.append('close')
    def lock(self): self.events.append('lock'); return True
    def interrupt_old(self,run): pass
    def run_status(self,*args):
        self.status.append(args)
        if self.fail_status: raise InterfaceError(0,'')


class RecoveryPortal:
    def __init__(self,*args): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def login(self): pass


class RecoveryTests(unittest.TestCase):
    def settings(self):
        return SimpleNamespace(user='test', carrier='ROBR_SC', operation='SC', password='dummy', validate=lambda **kwargs: None)

    def test_error_status_uses_fresh_connection_without_restarting_portal(self):
        events = []
        first, recovery = RecoveryRepo(events), RecoveryRepo(events)
        factory = mock.Mock(side_effect=[first, recovery])
        with mock.patch.object(service, 'process', side_effect=OperationalError(2013,'Lost connection')) as process:
            execute('run',self.settings(),date(2020,1,1),date(2020,1,2),True,
                    repo_factory=factory,portal_factory=RecoveryPortal)
        self.assertEqual(process.call_count,1)
        self.assertEqual(events, ['enter','lock','close','enter','close'])
        self.assertEqual(recovery.status[0][1],'ERRO')
        self.assertIn('2013', recovery.status[0][2])

    def test_error_reporting_failure_keeps_original_code_in_log(self):
        events = []
        first, recovery = RecoveryRepo(events), RecoveryRepo(events,fail_status=True)
        with mock.patch.object(service,'process',side_effect=OperationalError(2013,'Lost connection')), \
             mock.patch.object(service,'logger') as logger:
            execute('run',self.settings(),date(2020,1,1),date(2020,1,2),True,
                    repo_factory=mock.Mock(side_effect=[first,recovery]),portal_factory=RecoveryPortal)
        self.assertIn(2013,logger.error.call_args.args)
        self.assertIn(0,logger.error.call_args.args)


class HistoricalQueryTests(unittest.TestCase):
    def test_latest_significant_real_event_per_pod(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.executescript('''CREATE TABLE lg_pod_execucoes(id TEXT,simulacao INT);
            CREATE TABLE lg_pod_itens(id INT,pod_id TEXT,execucao_id TEXT,portal_user TEXT,
                transportador TEXT,etapa TEXT);
            INSERT INTO lg_pod_execucoes VALUES ('real',0),('simulation',1),('current',0);''')
        rows = [(1,'A','real','MAPEADO'),(2,'A','simulation','ENVIADO'),
                (3,'B','real','MAPEADO'),(4,'B','real','ENVIADO'),
                (5,'C','real','CONFERIR'),(6,'C','real','IGNORADO'),
                (7,'D','real','CONFERIR'),(8,'D','current','ENVIADO'),
                (9,'E','real','CONFERIR'),(10,'E','real','LIBERADO')]
        for identifier,pod,run,stage in rows:
            conn.execute('INSERT INTO lg_pod_itens VALUES (?,?,?,?,?,?)',
                         (identifier,pod,run,'user','ROBR_SC',stage))
        repo = Repository.__new__(Repository)
        repo.query = lambda sql,params: [dict(r) for r in conn.execute(sql.replace('%s','?'),params).fetchall()]
        result = repo.unresolved('user','ROBR_SC','current')
        self.assertEqual({row['id'] for row in result}, {1,5,7})
        conn.close()


if __name__ == '__main__': unittest.main()
