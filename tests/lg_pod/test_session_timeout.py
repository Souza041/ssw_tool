import os
import unittest
from unittest import mock
import pymysql
from modules.lg_pod import database
from modules.lg_pod.db_worker import safe_error_message


ENV = {'LG_POD_DB_HOST':'db.invalid','LG_POD_DB_USER':'test',
       'LG_POD_DB_NAME':'lg_pod','LG_POD_DB_PASSWORD':'private-test'}


class SessionTimeoutTests(unittest.TestCase):
    def invoke(self, conn, extra=None):
        with mock.patch.dict(os.environ, dict(ENV, **(extra or {})), clear=True),\
             mock.patch.object(database,'load_env'),\
             mock.patch('pymysql.connect',return_value=conn):
            return database.connect()

    def connection(self, effective=600):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {'seconds':effective}
        return conn,cur

    def test_default_sets_and_verifies_only_session(self):
        conn,cur = self.connection()
        self.assertIs(self.invoke(conn),conn)
        self.assertEqual(cur.execute.call_args_list, [
            mock.call('SET SESSION wait_timeout = %s',(600,)),
            mock.call('SELECT @@SESSION.wait_timeout AS seconds')])
        conn.close.assert_not_called()

    def test_custom_session_value(self):
        conn,cur = self.connection(900)
        self.invoke(conn,{'LG_POD_DB_SESSION_WAIT_TIMEOUT_SECONDS':'900'})
        self.assertEqual(cur.execute.call_args_list[0],mock.call('SET SESSION wait_timeout = %s',(900,)))

    def test_provider_clamp_fails_before_connection_is_returned(self):
        conn,cur = self.connection(15)
        with self.assertRaisesRegex(RuntimeError,'aplicou wait_timeout=15s'):
            self.invoke(conn)
        conn.close.assert_called_once()

    def test_permission_failure_preserves_original(self):
        conn,cur = self.connection()
        error = pymysql.err.OperationalError(1227,'Permission denied')
        cur.execute.side_effect = error
        with self.assertRaises(pymysql.err.OperationalError) as raised:
            self.invoke(conn)
        self.assertIs(raised.exception,error)
        conn.close.assert_called_once()

    def test_close_failure_does_not_mask_primary(self):
        conn,cur = self.connection()
        error = pymysql.err.OperationalError(2013,'Lost connection')
        cur.execute.side_effect = error
        conn.close.side_effect = RuntimeError('close failed')
        with self.assertRaises(pymysql.err.OperationalError) as raised:
            self.invoke(conn)
        self.assertIs(raised.exception,error)

    def test_invalid_values_fail_before_opening_socket(self):
        for value in ['abc','0','15','3601']:
            with self.subTest(value=value),\
                 mock.patch.dict(os.environ,dict(ENV,LG_POD_DB_SESSION_WAIT_TIMEOUT_SECONDS=value),clear=True),\
                 mock.patch.object(database,'load_env'),mock.patch('pymysql.connect') as connect:
                with self.assertRaises(ValueError): database.connect()
                connect.assert_not_called()

    def test_log_redacts_passwords_and_urls(self):
        with mock.patch.dict(os.environ,{'LG_POD_DB_PASSWORD':'very-secret'},clear=True):
            message = safe_error_message(Exception('very-secret https://example.invalid/?token=value\nnext'))
        self.assertNotIn('very-secret',message)
        self.assertNotIn('token=value',message)
        self.assertNotIn('\n',message)


if __name__ == '__main__': unittest.main()
