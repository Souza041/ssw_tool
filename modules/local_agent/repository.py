from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Iterator
import uuid

from modules.local_agent.contracts import (
    checked_json_object,
    checked_log_message,
    checked_task,
    decoded_object,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class BootstrapReplayError(RuntimeError):
    pass


class BootstrapRateLimitError(RuntimeError):
    pass


class LocalAgentRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        max_payload_bytes: int = 65536,
        max_result_bytes: int = 65536,
        max_log_bytes: int = 4096,
    ) -> None:
        self.database_path = Path(database_path)
        self.max_payload_bytes = max_payload_bytes
        self.max_result_bytes = max_result_bytes
        self.max_log_bytes = max_log_bytes
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        if self.database_path.is_symlink():
            raise RuntimeError("O banco do Local Agent não pode ser um link simbólico.")
        if not self.database_path.exists():
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(self.database_path, flags, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        if os.name != "nt":
            os.chmod(self.database_path, 0o600)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(self.database_path) + suffix)
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
        return connection

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS local_agent_jobs (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'QUEUED','LEASED','SUCCEEDED','FAILED','CANCELLED'
                    )),
                    priority INTEGER NOT NULL DEFAULT 100,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    agent_id TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_local_agent_jobs_claim
                    ON local_agent_jobs(status, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_local_agent_jobs_lease
                    ON local_agent_jobs(status, lease_expires_at);
                CREATE TABLE IF NOT EXISTS local_agent_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES local_agent_jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_local_agent_logs_job
                    ON local_agent_logs(job_id, id);
                CREATE TABLE IF NOT EXISTS local_agent_agents (
                    agent_id TEXT PRIMARY KEY,
                    capabilities_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    current_job_id TEXT
                );
                CREATE TABLE IF NOT EXISTS local_agent_bootstrap_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    nonce_hash TEXT NOT NULL,
                    remote_address TEXT NOT NULL,
                    authenticated INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    job_id TEXT,
                    UNIQUE(key_id, nonce_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_local_agent_bootstrap_rate
                    ON local_agent_bootstrap_audit(occurred_at, key_id, remote_address);
            """)

    @staticmethod
    def _nonce_hash(key_id: str, nonce: str) -> str:
        return hashlib.sha256(f"{key_id}\0{nonce}".encode("utf-8")).hexdigest()

    @staticmethod
    def _bootstrap_rate_exceeded(
        db: sqlite3.Connection,
        *,
        key_id: str,
        remote_address: str,
        now: datetime,
        per_minute: int,
        per_hour: int,
    ) -> bool:
        minute = utc_text(now - timedelta(minutes=1))
        hour = utc_text(now - timedelta(hours=1))
        query = """
            SELECT COUNT(*) FROM local_agent_bootstrap_audit
            WHERE occurred_at >= ? AND (key_id=? OR remote_address=?)
        """
        minute_count = int(db.execute(
            query, (minute, key_id, remote_address)
        ).fetchone()[0])
        hour_count = int(db.execute(
            query, (hour, key_id, remote_address)
        ).fetchone()[0])
        return minute_count >= per_minute or hour_count >= per_hour

    def audit_bootstrap_rejection(
        self,
        *,
        key_id: str,
        nonce: str,
        remote_address: str,
        outcome: str,
        per_minute: int,
        per_hour: int,
        now: datetime | None = None,
    ) -> str:
        current = now or utc_now()
        timestamp = utc_text(current)
        nonce_hash = self._nonce_hash(key_id, nonce)
        with self._transaction(immediate=True) as db:
            limited = self._bootstrap_rate_exceeded(
                db,
                key_id=key_id,
                remote_address=remote_address,
                now=current,
                per_minute=per_minute,
                per_hour=per_hour,
            )
            recorded_outcome = "RATE_LIMITED" if limited else outcome
            inserted = db.execute("""
                INSERT OR IGNORE INTO local_agent_bootstrap_audit (
                    occurred_at, key_id, nonce_hash, remote_address,
                    authenticated, action, outcome
                ) VALUES (?, ?, ?, ?, 0, 'AUTH', ?)
            """, (
                timestamp, key_id, nonce_hash, remote_address, recorded_outcome,
            ))
            if inserted.rowcount != 1:
                return "REPLAY"
            return recorded_outcome

    def bootstrap_enqueue_ping(
        self,
        *,
        key_id: str,
        nonce: str,
        remote_address: str,
        message: str,
        per_minute: int,
        per_hour: int,
        now: datetime | None = None,
    ) -> str:
        current = now or utc_now()
        timestamp = utc_text(current)
        nonce_hash = self._nonce_hash(key_id, nonce)
        payload_json = checked_json_object(
            {"message": message},
            maximum_bytes=self.max_payload_bytes,
            label="Payload",
        )
        job_id = str(uuid.uuid4())
        rate_limited = False
        with self._transaction(immediate=True) as db:
            if self._bootstrap_rate_exceeded(
                db,
                key_id=key_id,
                remote_address=remote_address,
                now=current,
                per_minute=per_minute,
                per_hour=per_hour,
            ):
                db.execute("""
                    INSERT OR IGNORE INTO local_agent_bootstrap_audit (
                        occurred_at, key_id, nonce_hash, remote_address,
                        authenticated, action, outcome
                    ) VALUES (?, ?, ?, ?, 1, 'ENQUEUE_PING', 'RATE_LIMITED')
                """, (timestamp, key_id, nonce_hash, remote_address))
                rate_limited = True
            if not rate_limited:
                try:
                    db.execute("""
                        INSERT INTO local_agent_bootstrap_audit (
                            occurred_at, key_id, nonce_hash, remote_address,
                            authenticated, action, outcome, job_id
                        ) VALUES (?, ?, ?, ?, 1, 'ENQUEUE_PING', 'ENQUEUED', ?)
                    """, (timestamp, key_id, nonce_hash, remote_address, job_id))
                except sqlite3.IntegrityError as exc:
                    raise BootstrapReplayError("Requisição de bootstrap repetida.") from exc
                db.execute("""
                    INSERT INTO local_agent_jobs (
                        id, task, payload_json, status, priority, max_attempts,
                        created_at, updated_at
                    ) VALUES (?, 'system.ping', ?, 'QUEUED', 100, 2, ?, ?)
                """, (job_id, payload_json, timestamp, timestamp))
        if rate_limited:
            raise BootstrapRateLimitError("Limite do bootstrap excedido.")
        return job_id

    def bootstrap_authorize_status(
        self,
        *,
        key_id: str,
        nonce: str,
        remote_address: str,
        job_id: str,
        per_minute: int,
        per_hour: int,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        current = now or utc_now()
        timestamp = utc_text(current)
        nonce_hash = self._nonce_hash(key_id, nonce)
        rate_limited = False
        with self._transaction(immediate=True) as db:
            if self._bootstrap_rate_exceeded(
                db,
                key_id=key_id,
                remote_address=remote_address,
                now=current,
                per_minute=per_minute,
                per_hour=per_hour,
            ):
                db.execute("""
                    INSERT OR IGNORE INTO local_agent_bootstrap_audit (
                        occurred_at, key_id, nonce_hash, remote_address,
                        authenticated, action, outcome, job_id
                    ) VALUES (?, ?, ?, ?, 1, 'READ_PING', 'RATE_LIMITED', ?)
                """, (timestamp, key_id, nonce_hash, remote_address, job_id))
                rate_limited = True
            if not rate_limited:
                try:
                    db.execute("""
                        INSERT INTO local_agent_bootstrap_audit (
                            occurred_at, key_id, nonce_hash, remote_address,
                            authenticated, action, outcome, job_id
                        ) VALUES (?, ?, ?, ?, 1, 'READ_PING', 'AUTHORIZED', ?)
                    """, (timestamp, key_id, nonce_hash, remote_address, job_id))
                except sqlite3.IntegrityError as exc:
                    raise BootstrapReplayError("Requisição de bootstrap repetida.") from exc
                allowed = db.execute("""
                    SELECT 1 FROM local_agent_bootstrap_audit
                    WHERE action='ENQUEUE_PING' AND outcome='ENQUEUED' AND job_id=?
                """, (job_id,)).fetchone()
                if allowed is None:
                    db.execute("""
                        UPDATE local_agent_bootstrap_audit SET outcome='NOT_FOUND'
                        WHERE key_id=? AND nonce_hash=?
                    """, (key_id, nonce_hash))
                    return None
        if rate_limited:
            raise BootstrapRateLimitError("Limite do bootstrap excedido.")
        job = self.get_job(job_id, log_limit=100)
        if job is None or job.get("task") != "system.ping":
            return None
        return {
            "id": job["id"],
            "task": job["task"],
            "status": job["status"],
            "progress_current": job["progress_current"],
            "progress_total": job["progress_total"],
            "result": job["result"],
            "error_message": job["error_message"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "logs": job["logs"],
        }

    def list_bootstrap_audit(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT occurred_at, key_id, remote_address, authenticated,
                       action, outcome, job_id
                FROM local_agent_bootstrap_audit
                ORDER BY id DESC LIMIT ?
            """, (max(1, min(limit, 500)),)).fetchall()
        return [dict(row) for row in rows]

    def enqueue(
        self,
        *,
        task: str,
        payload: dict[str, Any],
        priority: int = 100,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> str:
        task = checked_task(task)
        payload_json = checked_json_object(
            payload, maximum_bytes=self.max_payload_bytes, label="Payload"
        )
        if not 0 <= priority <= 1000:
            raise ValueError("Prioridade fora do intervalo permitido.")
        if not 1 <= max_attempts <= 10:
            raise ValueError("Número máximo de tentativas inválido.")
        job_id = str(uuid.uuid4())
        timestamp = utc_text(now or utc_now())
        with self._transaction() as db:
            db.execute("""
                INSERT INTO local_agent_jobs (
                    id, task, payload_json, status, priority, max_attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?)
            """, (job_id, task, payload_json, priority, max_attempts, timestamp, timestamp))
        return job_id

    def _recover_expired(self, db: sqlite3.Connection, now_text: str) -> None:
        db.execute("""
            UPDATE local_agent_agents SET current_job_id=NULL
            WHERE current_job_id IN (
                SELECT id FROM local_agent_jobs
                WHERE status='LEASED' AND lease_expires_at < ?
            )
        """, (now_text,))
        db.execute("""
            UPDATE local_agent_jobs
            SET status = CASE
                    WHEN cancellation_requested = 1 THEN 'CANCELLED'
                    WHEN attempts >= max_attempts THEN 'FAILED'
                    ELSE 'QUEUED'
                END,
                error_message = CASE
                    WHEN cancellation_requested = 1 THEN NULL
                    WHEN attempts >= max_attempts THEN 'Lease expirado; tentativas esgotadas.'
                    ELSE 'Lease anterior expirou; tarefa recolocada na fila.'
                END,
                finished_at = CASE
                    WHEN cancellation_requested = 1 OR attempts >= max_attempts THEN ?
                    ELSE NULL
                END,
                agent_id = NULL, lease_token = NULL, lease_expires_at = NULL,
                updated_at = ?
            WHERE status = 'LEASED' AND lease_expires_at < ?
        """, (now_text, now_text, now_text))

    def claim(
        self,
        *,
        agent_id: str,
        capabilities: set[str] | frozenset[str],
        lease_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        checked_capabilities = sorted({checked_task(task) for task in capabilities})
        if not checked_capabilities:
            raise ValueError("Executor sem capacidades registradas.")
        current = now or utc_now()
        now_text = utc_text(current)
        expires_text = utc_text(current + timedelta(seconds=lease_seconds))
        lease_token = secrets.token_urlsafe(32)
        placeholders = ",".join("?" for _ in checked_capabilities)
        with self._transaction(immediate=True) as db:
            self._recover_expired(db, now_text)
            capabilities_json = json.dumps(checked_capabilities, separators=(",", ":"))
            db.execute("""
                INSERT INTO local_agent_agents (
                    agent_id, capabilities_json, first_seen_at, last_seen_at, current_job_id
                ) VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(agent_id) DO UPDATE SET
                    capabilities_json=excluded.capabilities_json,
                    last_seen_at=excluded.last_seen_at
            """, (agent_id, capabilities_json, now_text, now_text))
            row = db.execute(f"""
                SELECT id, task, payload_json, attempts, max_attempts
                FROM local_agent_jobs
                WHERE status = 'QUEUED'
                  AND cancellation_requested = 0
                  AND attempts < max_attempts
                  AND task IN ({placeholders})
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
            """, checked_capabilities).fetchone()
            if row is None:
                return None
            updated = db.execute("""
                UPDATE local_agent_jobs
                SET status='LEASED', agent_id=?, lease_token=?, lease_expires_at=?,
                    attempts=attempts+1, started_at=COALESCE(started_at, ?), updated_at=?
                WHERE id=? AND status='QUEUED'
            """, (agent_id, lease_token, expires_text, now_text, now_text, row["id"]))
            if updated.rowcount != 1:
                return None
            db.execute(
                "UPDATE local_agent_agents SET current_job_id=? WHERE agent_id=?",
                (row["id"], agent_id),
            )
            return {
                "id": row["id"],
                "task": row["task"],
                "payload": decoded_object(row["payload_json"]),
                "lease_token": lease_token,
                "lease_expires_at": expires_text,
                "attempt": int(row["attempts"]) + 1,
                "max_attempts": int(row["max_attempts"]),
            }

    def heartbeat(
        self,
        *,
        job_id: str,
        agent_id: str,
        lease_token: str,
        lease_seconds: int,
        progress_current: int = 0,
        progress_total: int = 0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if progress_current < 0 or progress_total < 0 or (
            progress_total and progress_current > progress_total
        ):
            raise ValueError("Progresso inválido.")
        current = now or utc_now()
        now_text = utc_text(current)
        expires_text = utc_text(current + timedelta(seconds=lease_seconds))
        with self._transaction(immediate=True) as db:
            updated = db.execute("""
                UPDATE local_agent_jobs
                SET lease_expires_at=?, progress_current=?, progress_total=?, updated_at=?
                WHERE id=? AND status='LEASED' AND agent_id=? AND lease_token=?
                  AND lease_expires_at >= ?
            """, (
                expires_text, progress_current, progress_total, now_text,
                job_id, agent_id, lease_token, now_text,
            ))
            if updated.rowcount != 1:
                raise LookupError("Lease inexistente ou expirado.")
            row = db.execute(
                "SELECT cancellation_requested FROM local_agent_jobs WHERE id=?", (job_id,)
            ).fetchone()
            db.execute(
                "UPDATE local_agent_agents SET last_seen_at=? WHERE agent_id=?",
                (now_text, agent_id),
            )
            return {
                "lease_expires_at": expires_text,
                "cancellation_requested": bool(row["cancellation_requested"]),
            }

    def append_logs(
        self,
        *,
        job_id: str,
        agent_id: str,
        lease_token: str,
        entries: list[dict[str, str]],
        now: datetime | None = None,
    ) -> int:
        if not 1 <= len(entries) <= 100:
            raise ValueError("Lote de logs inválido.")
        normalized: list[tuple[str, str]] = []
        for entry in entries:
            level = str(entry.get("level") or "INFO").upper()
            if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
                raise ValueError("Nível de log inválido.")
            normalized.append((
                level,
                checked_log_message(entry.get("message"), maximum_bytes=self.max_log_bytes),
            ))
        timestamp = utc_text(now or utc_now())
        with self._transaction(immediate=True) as db:
            lease = db.execute("""
                SELECT 1 FROM local_agent_jobs
                WHERE id=? AND status='LEASED' AND agent_id=? AND lease_token=?
                  AND lease_expires_at >= ?
            """, (job_id, agent_id, lease_token, timestamp)).fetchone()
            if lease is None:
                raise LookupError("Lease inválido para logs.")
            db.executemany("""
                INSERT INTO local_agent_logs(job_id, created_at, level, message)
                VALUES (?, ?, ?, ?)
            """, [(job_id, timestamp, level, message) for level, message in normalized])
        return len(normalized)

    def complete(
        self,
        *,
        job_id: str,
        agent_id: str,
        lease_token: str,
        succeeded: bool,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> str:
        result_json = checked_json_object(
            result or {}, maximum_bytes=self.max_result_bytes, label="Resultado"
        )
        safe_error = None
        if error_message:
            safe_error = checked_log_message(
                error_message, maximum_bytes=min(self.max_log_bytes, 2048)
            )
        timestamp = utc_text(now or utc_now())
        with self._transaction(immediate=True) as db:
            row = db.execute("""
                SELECT cancellation_requested FROM local_agent_jobs
                WHERE id=? AND status='LEASED' AND agent_id=? AND lease_token=?
                  AND lease_expires_at >= ?
            """, (job_id, agent_id, lease_token, timestamp)).fetchone()
            if row is None:
                raise LookupError("Lease inválido para conclusão.")
            status = (
                "CANCELLED" if row["cancellation_requested"]
                else "SUCCEEDED" if succeeded else "FAILED"
            )
            db.execute("""
                UPDATE local_agent_jobs
                SET status=?, result_json=?, error_message=?, finished_at=?, updated_at=?,
                    lease_token=NULL, lease_expires_at=NULL
                WHERE id=?
            """, (status, result_json, safe_error, timestamp, timestamp, job_id))
            db.execute(
                "UPDATE local_agent_agents SET current_job_id=NULL, last_seen_at=? WHERE agent_id=?",
                (timestamp, agent_id),
            )
        return status

    def request_cancel(self, job_id: str, *, now: datetime | None = None) -> str:
        timestamp = utc_text(now or utc_now())
        with self._transaction(immediate=True) as db:
            row = db.execute(
                "SELECT status FROM local_agent_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise LookupError("Tarefa não encontrada.")
            if row["status"] == "QUEUED":
                db.execute("""
                    UPDATE local_agent_jobs
                    SET status='CANCELLED', cancellation_requested=1,
                        finished_at=?, updated_at=? WHERE id=?
                """, (timestamp, timestamp, job_id))
                return "CANCELLED"
            if row["status"] == "LEASED":
                db.execute("""
                    UPDATE local_agent_jobs
                    SET cancellation_requested=1, updated_at=? WHERE id=?
                """, (timestamp, job_id))
                return "CANCELLATION_REQUESTED"
            return str(row["status"])

    def get_job(self, job_id: str, *, log_limit: int = 200) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM local_agent_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            logs = db.execute("""
                SELECT created_at, level, message FROM local_agent_logs
                WHERE job_id=? ORDER BY id DESC LIMIT ?
            """, (job_id, max(1, min(log_limit, 1000)))).fetchall()
        result = dict(row)
        result["payload"] = decoded_object(result.pop("payload_json"))
        result["result"] = decoded_object(result.pop("result_json")) if result["result_json"] else None
        result["logs"] = [dict(item) for item in reversed(logs)]
        result.pop("lease_token", None)
        return result

    def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT id, task, status, priority, attempts, max_attempts, agent_id,
                       cancellation_requested, progress_current, progress_total,
                       created_at, started_at, finished_at, updated_at, error_message
                FROM local_agent_jobs ORDER BY created_at DESC LIMIT ?
            """, (max(1, min(limit, 200)),)).fetchall()
        return [dict(row) for row in rows]

    def list_agents(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT agent_id, capabilities_json, first_seen_at, last_seen_at, current_job_id
                FROM local_agent_agents ORDER BY agent_id
            """).fetchall()
        return [{
            **dict(row),
            "capabilities": json.loads(row["capabilities_json"]),
        } for row in rows]
