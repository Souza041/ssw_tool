"""Persistência transacional, compartilhada por cron e processos web."""

import json
import os
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Callable

from modules.health_center.models import Bot, Outcome, Status
from modules.health_center.privacy import (
    checked_catalog_text, checked_error, checked_execution_id,
    checked_identifier, checked_metadata, checked_parameters, checked_summary,
)


TERMINAL_STATUSES = frozenset({"SUCCESS", "PARTIAL", "FAILED", "CANCELLED", "SKIPPED"})


def _safe_text(value, checker) -> str | None:
    if value is None:
        return None
    try:
        return checker(value)
    except (TypeError, ValueError, AttributeError):
        return None


def public_execution(row: dict, *, detail: bool = False) -> dict:
    """Allowlist de leitura; registros legados fora do contrato são redigidos."""
    result = {
        key: row.get(key) for key in (
            "id", "bot_id", "origin", "status", "started_at_utc",
            "finished_at_utc", "duration_seconds", "processed_count",
            "success_count", "error_count",
        )
    }
    result["id"] = _safe_text(row.get("id"), checked_execution_id)
    result["bot_id"] = _safe_text(row.get("bot_id"), checked_identifier)
    if result["status"] not in {item.value for item in Status}:
        result["status"] = "UNVERIFIED"
    elif result["status"] in TERMINAL_STATUSES and row.get("finished_at_utc") is None:
        result["status"] = "UNVERIFIED"
    result["bot_name"] = _safe_text(
        row.get("bot_name"), lambda value: checked_catalog_text(value, maximum=160)
    )
    result["origin"] = _safe_text(
        row.get("origin"), lambda value: checked_catalog_text(value, maximum=40)
    )
    result["summary"] = _safe_text(row.get("summary"), checked_summary)
    result["error_message"] = _safe_text(row.get("error_message"), checked_error)
    if detail:
        try:
            raw = row.get("metadata_json") or "{}"
            metadata = json.loads(raw) if isinstance(raw, str) else raw
            checked_metadata(metadata)
            result["indicators"] = dict(metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            result["indicators"] = {}
    return result


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Timestamp precisa de fuso horário.")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def checked_log_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not value.startswith("logs/")
        or "\\" in value
        or ":" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
        or PurePosixPath(value).is_absolute()
        or len(value) > 500
    ):
        raise ValueError("Referência de log inválida.")
    return value


class HealthRepository:
    def __init__(
        self,
        transaction_factory: Callable[[], AbstractContextManager] | None = None,
        read_transaction_factory: Callable[[], AbstractContextManager] | None = None,
    ):
        if transaction_factory is None:
            from modules.health_center.database import read_transaction, transaction

            transaction_factory = transaction
            read_transaction_factory = read_transaction
        elif read_transaction_factory is None:
            read_transaction_factory = transaction_factory
        self._transaction = transaction_factory
        self._read_transaction = read_transaction_factory

    def start(
        self,
        *,
        execution_id: str,
        bot: Bot,
        origin: str,
        started_at: datetime,
        responsible_user: str | None = None,
        correlation_id: str | None = None,
        code_version: str | None = None,
    ) -> None:
        started_utc = utc_naive(started_at)
        parameters_json = checked_parameters(bot.parameters)
        checked_execution_id(execution_id)
        checked_identifier(bot.identifier)
        checked_catalog_text(bot.name, maximum=160)
        if bot.description:
            checked_catalog_text(bot.description, maximum=500)
        checked_catalog_text(bot.category, maximum=80)
        if bot.schedule_source:
            checked_catalog_text(bot.schedule_source, maximum=80)
        checked_catalog_text(origin, maximum=40)
        if correlation_id:
            checked_execution_id(correlation_id)
        if code_version:
            checked_catalog_text(code_version, maximum=100)
        if responsible_user is not None:
            raise ValueError("Identidade de usuário não permitida nesta etapa.")
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO health_bots
                       (identifier, name, description, category, enabled,
                        schedule_source, parameters_json, created_at_utc, updated_at_utc)
                       VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                         name = VALUES(name), description = VALUES(description),
                         category = VALUES(category),
                         schedule_source = VALUES(schedule_source),
                         parameters_json = VALUES(parameters_json),
                         updated_at_utc = VALUES(updated_at_utc)""",
                    (
                        bot.identifier, bot.name, bot.description, bot.category,
                        bot.schedule_source,
                        parameters_json,
                        started_utc, started_utc,
                    ),
                )
                cursor.execute(
                    """INSERT INTO health_executions
                       (id, bot_id, bot_name, origin, responsible_user, status,
                        started_at_utc, correlation_id, code_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE id = id""",
                    (
                        execution_id, bot.identifier, bot.name, origin, responsible_user,
                        Status.RUNNING.value, started_utc,
                        correlation_id or execution_id, code_version,
                    ),
                )

    def finish(
        self,
        *,
        execution_id: str,
        outcome: Outcome,
        finished_at: datetime,
        duration_seconds: float,
        error_message: str | None = None,
        log_ref: str | None = None,
    ) -> bool:
        summary = checked_summary(outcome.summary)
        error = checked_error(error_message)
        metadata_json = checked_metadata(outcome.metadata)
        if log_ref is not None:
            raise ValueError("Referência de log não permitida nesta etapa.")
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE health_executions SET
                         status = %s, finished_at_utc = %s,
                         duration_seconds = %s, processed_count = %s,
                         success_count = %s, error_count = %s,
                         summary = %s, error_message = %s, log_ref = %s,
                         metadata_json = %s
                       WHERE id = %s AND status = 'RUNNING'""",
                    (
                        outcome.status.value, utc_naive(finished_at),
                        max(0.0, duration_seconds), outcome.processed,
                        outcome.succeeded, outcome.errors, summary,
                        error,
                        checked_log_ref(log_ref),
                        metadata_json,
                        execution_id,
                    ),
                )
                return cursor.rowcount == 1

    def stale_running(self, *, before: datetime, limit: int = 100) -> list[dict]:
        """Consulta somente; não reclassifica uma operação ainda ativa."""
        if not 1 <= limit <= 1000:
            raise ValueError("Limite fora do intervalo permitido.")
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT id, bot_id, origin, started_at_utc
                       FROM health_executions
                       WHERE status = 'RUNNING' AND started_at_utc < %s
                       ORDER BY started_at_utc ASC LIMIT %s""",
                    (utc_naive(before), limit),
                )
                return list(cursor.fetchall())

    def suspicious_running(
        self, *, now: datetime, limit: int = 100, threshold_minutes: int | None = None,
    ) -> list[dict]:
        """Sinaliza duração suspeita sem modificar o status persistido."""
        if threshold_minutes is None:
            try:
                threshold_minutes = int(os.getenv("HEALTH_RUNNING_SUSPECT_MINUTES", "120"))
            except ValueError as exc:
                raise ValueError("Limiar de RUNNING inválido.") from exc
        if not 1 <= threshold_minutes <= 10080:
            raise ValueError("Limiar de RUNNING fora do intervalo.")
        before = now - timedelta(minutes=threshold_minutes)
        return [
            {**row, "observability_state": "SUSPECTED_INTERRUPTION"}
            for row in self.stale_running(before=before, limit=limit)
        ]

    def list_bots(self, *, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Limite inválido.")
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT identifier, category FROM health_bots ORDER BY identifier LIMIT %s",
                    (limit,),
                )
                return [
                    {"identifier": row["identifier"], "category": row["category"],
                     "operational_state": "unverified", "next_execution": None}
                    for row in cursor.fetchall()
                ]

    def list_executions(self, *, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Limite inválido.")
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT id, bot_id, origin, status, started_at_utc,
                              finished_at_utc, processed_count, success_count, error_count
                       FROM health_executions ORDER BY started_at_utc DESC LIMIT %s""",
                    (limit,),
                )
                return list(cursor.fetchall())

    def overview(self, *, now: datetime, threshold_minutes: int = 120) -> dict:
        """Agregados do Health Center; não incluem métricas SQLite dos jobs web."""
        if not 1 <= threshold_minutes <= 10080:
            raise ValueError("Limiar de RUNNING fora do intervalo.")
        suspect_before = utc_naive(now - timedelta(minutes=threshold_minutes))
        since = utc_naive(now - timedelta(hours=24))
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT COUNT(*) AS registered,
                              COALESCE(SUM(e.id IS NOT NULL), 0) AS observed,
                              COALESCE(SUM(e.status = 'SUCCESS' AND
                                           e.finished_at_utc IS NOT NULL), 0) AS healthy,
                              COALESCE(SUM(e.status IN ('FAILED', 'PARTIAL') AND
                                           e.finished_at_utc IS NOT NULL), 0) AS issues,
                              COALESCE(SUM(e.status = 'RUNNING' AND
                                           e.started_at_utc < %s), 0) AS suspicious,
                              COALESCE(SUM(e.status = 'RUNNING' AND
                                           e.started_at_utc >= %s), 0) AS running,
                              COALESCE(SUM(e.id IS NULL OR
                                  (e.status <> 'RUNNING' AND e.finished_at_utc IS NULL)
                                  OR e.status NOT IN
                                  ('SUCCESS','PARTIAL','FAILED','RUNNING')), 0)
                                  AS unverified
                       FROM health_bots b LEFT JOIN health_executions e ON e.id = (
                         SELECT x.id FROM health_executions x
                         WHERE x.bot_id = b.identifier
                         ORDER BY x.started_at_utc DESC, x.id DESC LIMIT 1)""",
                    (suspect_before, suspect_before),
                )
                catalog = cursor.fetchone() or {}
                cursor.execute(
                    """SELECT COUNT(*) AS runs_24h,
                              COALESCE(SUM(status = 'SUCCESS' AND
                                           finished_at_utc IS NOT NULL), 0) AS successes_24h,
                              COALESCE(SUM(finished_at_utc IS NOT NULL AND status IN
                                  ('SUCCESS','PARTIAL','FAILED','CANCELLED','SKIPPED')), 0)
                                  AS completed_24h,
                              AVG(CASE WHEN finished_at_utc IS NOT NULL AND status IN
                                  ('SUCCESS','PARTIAL','FAILED','CANCELLED','SKIPPED')
                                  THEN duration_seconds END) AS avg_duration_seconds
                       FROM health_executions WHERE started_at_utc >= %s""",
                    (since,),
                )
                activity = cursor.fetchone() or {}
                cursor.execute(
                    "SELECT MAX(finished_at_utc) AS last_confirmed_at_utc "
                    "FROM health_executions WHERE finished_at_utc IS NOT NULL",
                    (),
                )
                latest = cursor.fetchone() or {}
        completed = int(activity.get("completed_24h") or 0)
        successes = int(activity.get("successes_24h") or 0)
        return {
            **{key: int(catalog.get(key) or 0) for key in (
                "registered", "observed", "healthy", "issues", "suspicious",
                "running", "unverified",
            )},
            "runs_24h": int(activity.get("runs_24h") or 0),
            "success_rate_24h": round(100 * successes / completed, 1) if completed else None,
            "avg_duration_seconds": (
                float(activity["avg_duration_seconds"])
                if activity.get("avg_duration_seconds") is not None else None
            ),
            "last_confirmed_at_utc": latest.get("last_confirmed_at_utc"),
            "scope": "health_executions",
        }

    def catalog(self, *, limit: int = 50, now: datetime,
                threshold_minutes: int = 120,
                bot_id: str | None = None) -> list[dict]:
        if not 1 <= limit <= 100 or not 1 <= threshold_minutes <= 10080:
            raise ValueError("Limite de catálogo inválido.")
        if bot_id is not None:
            checked_identifier(bot_id)
        cutoff = utc_naive(now - timedelta(minutes=threshold_minutes))
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT b.identifier, b.name, b.category,
                              e.origin, e.status, e.started_at_utc, e.finished_at_utc,
                              e.duration_seconds, e.id AS last_execution_id
                       FROM health_bots b LEFT JOIN health_executions e ON e.id = (
                         SELECT x.id FROM health_executions x
                         WHERE x.bot_id = b.identifier
                         ORDER BY x.started_at_utc DESC, x.id DESC LIMIT 1)
                       """ + ("WHERE b.identifier = %s " if bot_id is not None else "") +
                    "ORDER BY b.identifier LIMIT %s",
                    (bot_id, limit) if bot_id is not None else (limit,),
                )
                rows = list(cursor.fetchall())
        result = []
        for row in rows:
            status = row.get("status")
            started = row.get("started_at_utc")
            state = (
                "suspected" if status == "RUNNING" and started and started < cutoff
                else "running" if status == "RUNNING"
                else "success" if status == "SUCCESS" and row.get("finished_at_utc")
                else "partial" if status == "PARTIAL" and row.get("finished_at_utc")
                else "failed" if status == "FAILED" and row.get("finished_at_utc")
                else "unverified"
            )
            result.append({
                "identifier": row["identifier"],
                "name": _safe_text(row.get("name"), lambda v: checked_catalog_text(v, maximum=160)),
                "category": _safe_text(row.get("category"), lambda v: checked_catalog_text(v, maximum=80)),
                "origin": _safe_text(row.get("origin"), lambda v: checked_catalog_text(v, maximum=40)),
                "registered": True,
                "instrumented": row["identifier"] == "ocorrencia_73",
                "observed": bool(row.get("last_execution_id")),
                "schedule_verified": False,
                "operational_state": state,
                "last_execution_at_utc": started,
                "last_duration_seconds": (
                    float(row["duration_seconds"])
                    if row.get("duration_seconds") is not None else None
                ),
                "next_execution": None,
            })
        return result

    def bot_activity(self, *, bot_id: str, since: datetime) -> dict:
        checked_identifier(bot_id)
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT COUNT(*) AS executions,
                              COALESCE(SUM(status = 'SUCCESS' AND
                                           finished_at_utc IS NOT NULL), 0) AS successes,
                              COALESCE(SUM(status = 'PARTIAL' AND
                                           finished_at_utc IS NOT NULL), 0) AS partials,
                              COALESCE(SUM(status = 'FAILED' AND
                                           finished_at_utc IS NOT NULL), 0) AS failures,
                              AVG(CASE WHEN finished_at_utc IS NOT NULL
                                  THEN duration_seconds END) AS avg_duration_seconds
                       FROM health_executions
                       WHERE bot_id = %s AND started_at_utc >= %s""",
                    (bot_id, utc_naive(since)),
                )
                row = cursor.fetchone() or {}
        return {
            key: int(row.get(key) or 0)
            for key in ("executions", "successes", "partials", "failures")
        } | {"avg_duration_seconds": (
            float(row["avg_duration_seconds"])
            if row.get("avg_duration_seconds") is not None else None
        )}

    def history(self, *, bot_id: str | None = None, status: str | None = None,
                since: datetime, page: int = 1, per_page: int = 25) -> dict:
        if bot_id is not None:
            checked_identifier(bot_id)
        if status is not None and status not in {item.value for item in Status}:
            raise ValueError("Status inválido.")
        if not 1 <= page <= 1000 or not 1 <= per_page <= 100:
            raise ValueError("Paginação inválida.")
        offset = (page - 1) * per_page
        if offset > 100_000:
            raise ValueError("Deslocamento excessivo.")
        clauses = ["started_at_utc >= %s"]
        parameters = [utc_naive(since)]
        if bot_id is not None:
            clauses.append("bot_id = %s")
            parameters.append(bot_id)
        if status is not None:
            clauses.append("status = %s")
            parameters.append(status)
        where = " AND ".join(clauses)
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS total FROM health_executions WHERE " + where,
                    tuple(parameters),
                )
                total = int((cursor.fetchone() or {}).get("total") or 0)
                cursor.execute(
                    """SELECT id, bot_id, bot_name, origin, status,
                              started_at_utc, finished_at_utc, duration_seconds,
                              processed_count, success_count, error_count,
                              summary, error_message
                       FROM health_executions WHERE """ + where +
                    " ORDER BY started_at_utc DESC, id DESC LIMIT %s OFFSET %s",
                    (*parameters, per_page, offset),
                )
                rows = list(cursor.fetchall())
        return {"items": [public_execution(row) for row in rows],
                "total": total, "page": page, "per_page": per_page}

    def execution_detail(self, execution_id: str) -> dict | None:
        checked_execution_id(execution_id)
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT id, bot_id, bot_name, origin, status,
                              started_at_utc, finished_at_utc, duration_seconds,
                              processed_count, success_count, error_count,
                              summary, error_message, metadata_json
                       FROM health_executions WHERE id = %s LIMIT 1""",
                    (execution_id,),
                )
                row = cursor.fetchone()
        return public_execution(row, detail=True) if row else None
