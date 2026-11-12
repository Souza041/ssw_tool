"""Persistência transacional do monitor de dependências externas."""

from __future__ import annotations

import json
import re
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Callable

from modules.health_center.dependency_models import (
    DependencyDefinition, DependencyState, HysteresisDecision, ProbeResult,
    apply_hysteresis,
)
from modules.health_center.repository import utc_naive


_REASON = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_METADATA_KEYS = frozenset({
    "address_count", "certificate_days_remaining", "functional_availability_proven",
})


def _metadata_json(value: dict) -> str:
    if set(value) - _METADATA_KEYS:
        raise ValueError("Metadado de probe não permitido.")
    for item in value.values():
        if item is not None and type(item) not in {bool, int, float}:
            raise ValueError("Tipo de metadado de probe inválido.")
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if len(encoded) > 1024:
        raise ValueError("Metadado de probe excessivo.")
    return encoded


def _validate_result(result: ProbeResult) -> None:
    if not _REASON.fullmatch(result.reason_code):
        raise ValueError("Código técnico inválido.")
    lowered = result.detail.casefold()
    if any(marker in lowered for marker in (
        "password=", "senha=", "token=", "cookie=", "authorization:", "mysql://",
    )):
        raise ValueError("Detalhe técnico contém marcador sensível.")


class DependencyRepository:
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

    def register(self, definition: DependencyDefinition, *, now: datetime) -> None:
        probes = json.dumps([item.value for item in definition.probes], separators=(",", ":"))
        config = json.dumps({
            "allow_private": definition.allow_private,
            "mysql_env_prefix": definition.mysql_env_prefix,
        }, separators=(",", ":"))
        timestamp = utc_naive(now)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO health_dependencies
                       (identifier, name, dependency_type, target_host, target_port,
                        target_scheme, target_path, enabled, maintenance, probes_json,
                        config_json, created_at_utc, updated_at_utc)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE name=VALUES(name),
                         dependency_type=VALUES(dependency_type),
                         target_host=VALUES(target_host), target_port=VALUES(target_port),
                         target_scheme=VALUES(target_scheme), target_path=VALUES(target_path),
                         probes_json=VALUES(probes_json), config_json=VALUES(config_json),
                         updated_at_utc=VALUES(updated_at_utc)""",
                    (
                        definition.identifier, definition.name, definition.scheme,
                        definition.host, definition.port, definition.scheme,
                        definition.path, int(definition.enabled), probes, config,
                        timestamp, timestamp,
                    ),
                )

    def record_result(
        self, result: ProbeResult, *, failure_threshold: int = 3,
        recovery_threshold: int = 2, stale_after_seconds: int = 300,
        alert_cooldown_seconds: int = 1800,
    ) -> HysteresisDecision:
        _validate_result(result)
        metadata_json = _metadata_json(result.metadata)
        check_id = str(uuid.uuid4())
        now = utc_naive(result.checked_at)
        stale_after = now + timedelta(seconds=stale_after_seconds)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO health_dependency_state
                       (dependency_id, current_state, updated_at_utc)
                       VALUES (%s,'UNKNOWN',%s)
                       ON DUPLICATE KEY UPDATE dependency_id=VALUES(dependency_id)""",
                    (result.dependency_id, now),
                )
                cursor.execute(
                    """SELECT current_state, consecutive_failures,
                              consecutive_successes, current_incident_id
                       FROM health_dependency_state
                       WHERE dependency_id=%s FOR UPDATE""",
                    (result.dependency_id,),
                )
                current = cursor.fetchone() or {}
                decision = apply_hysteresis(
                    current=DependencyState(current.get("current_state", "UNKNOWN")),
                    observed=result.state,
                    failures=int(current.get("consecutive_failures") or 0),
                    successes=int(current.get("consecutive_successes") or 0),
                    failure_threshold=failure_threshold,
                    recovery_threshold=recovery_threshold,
                )
                cursor.execute(
                    """INSERT INTO health_dependency_checks
                       (id, dependency_id, probe_type, observed_state, checked_at_utc,
                        latency_ms, reason_code, technical_detail, http_status, metadata_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        check_id, result.dependency_id, result.probe.value,
                        result.state.value, now, result.latency_ms, result.reason_code,
                        result.detail, result.http_status, metadata_json,
                    ),
                )
                incident_id = current.get("current_incident_id")
                if decision.open_incident:
                    incident_id = str(uuid.uuid4())
                    cursor.execute(
                        """INSERT INTO health_dependency_incidents
                           (id, dependency_id, state, opened_at_utc, opening_check_id,
                            reason_code, technical_detail, active_key,
                            last_internal_alert_at_utc, internal_alert_count,
                            created_at_utc, updated_at_utc)
                           VALUES (%s,%s,'OPEN',%s,%s,%s,%s,%s,%s,1,%s,%s)
                           ON DUPLICATE KEY UPDATE id=id""",
                        (
                            incident_id, result.dependency_id, now, check_id,
                            result.reason_code, result.detail, result.dependency_id,
                            now, now, now,
                        ),
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            "SELECT id FROM health_dependency_incidents "
                            "WHERE active_key=%s LIMIT 1",
                            (result.dependency_id,),
                        )
                        row = cursor.fetchone() or {}
                        incident_id = row.get("id")
                elif decision.recover_incident and incident_id:
                    cursor.execute(
                        """UPDATE health_dependency_incidents SET
                             state='RECOVERED', recovered_at_utc=%s,
                             recovery_check_id=%s, active_key=NULL,
                             updated_at_utc=%s
                           WHERE id=%s AND state='OPEN'""",
                        (now, check_id, now, incident_id),
                    )
                    incident_id = None
                elif decision.state is DependencyState.DOWN and incident_id:
                    cursor.execute(
                        """UPDATE health_dependency_incidents SET
                             last_internal_alert_at_utc=%s,
                             internal_alert_count=internal_alert_count+1,
                             updated_at_utc=%s
                           WHERE id=%s AND state='OPEN' AND
                             (last_internal_alert_at_utc IS NULL OR
                              last_internal_alert_at_utc < %s)""",
                        (
                            now, now, incident_id,
                            now - timedelta(seconds=alert_cooldown_seconds),
                        ),
                    )
                cursor.execute(
                    """UPDATE health_dependency_state SET
                         current_state=%s, consecutive_failures=%s,
                         consecutive_successes=%s, last_check_at_utc=%s,
                         last_success_at_utc=CASE WHEN %s='HEALTHY' THEN %s
                              ELSE last_success_at_utc END,
                         last_failure_at_utc=CASE WHEN %s IN ('DOWN','DEGRADED') THEN %s
                              ELSE last_failure_at_utc END,
                         latency_ms=%s, reason_code=%s, technical_detail=%s,
                         current_incident_id=%s, stale_after_utc=%s, updated_at_utc=%s
                       WHERE dependency_id=%s""",
                    (
                        decision.state.value, decision.consecutive_failures,
                        decision.consecutive_successes, now, result.state.value, now,
                        result.state.value, now, result.latency_ms, result.reason_code,
                        result.detail, incident_id, stale_after, now, result.dependency_id,
                    ),
                )
        return decision

    def record_observation(self, result: ProbeResult) -> None:
        """Persiste um probe individual sem aplicar histerese novamente."""
        _validate_result(result)
        metadata_json = _metadata_json(result.metadata)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO health_dependency_checks
                       (id, dependency_id, probe_type, observed_state, checked_at_utc,
                        latency_ms, reason_code, technical_detail, http_status, metadata_json)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        str(uuid.uuid4()), result.dependency_id, result.probe.value,
                        result.state.value, utc_naive(result.checked_at), result.latency_ms,
                        result.reason_code, result.detail, result.http_status,
                        metadata_json,
                    ),
                )

    def overview(self, *, now: datetime) -> dict:
        timestamp = utc_naive(now)
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT COUNT(*) AS total,
                              COALESCE(SUM(s.current_state='HEALTHY'),0) AS healthy,
                              COALESCE(SUM(s.current_state='DEGRADED'),0) AS degraded,
                              COALESCE(SUM(s.current_state='DOWN'),0) AS down_count,
                              COALESCE(SUM(s.current_state='UNKNOWN' OR s.current_state IS NULL),0) AS unknown_count,
                              COALESCE(SUM(d.maintenance=1),0) AS maintenance,
                              COALESCE(SUM(s.stale_after_utc < %s),0) AS stale
                       FROM health_dependencies d
                       LEFT JOIN health_dependency_state s ON s.dependency_id=d.identifier
                       WHERE d.enabled=1""",
                    (timestamp,),
                )
                row = cursor.fetchone() or {}
        return {key: int(row.get(key) or 0) for key in (
            "total", "healthy", "degraded", "down_count", "unknown_count",
            "maintenance", "stale",
        )}

    def catalog(self, *, now: datetime, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Limite inválido.")
        timestamp = utc_naive(now)
        day = timestamp - timedelta(hours=24)
        week = timestamp - timedelta(days=7)
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT d.identifier, d.name, d.dependency_type,
                              s.current_state, s.last_check_at_utc, s.latency_ms,
                              s.reason_code, s.technical_detail, s.stale_after_utc,
                              s.current_incident_id,
                              (SELECT ROUND(100*SUM(c.observed_state='HEALTHY')/COUNT(*),1)
                                 FROM health_dependency_checks c
                                WHERE c.dependency_id=d.identifier AND
                                      c.probe_type='COMPOSITE' AND c.checked_at_utc >= %s)
                                  AS availability_24h,
                              (SELECT ROUND(100*SUM(c.observed_state='HEALTHY')/COUNT(*),1)
                                 FROM health_dependency_checks c
                                WHERE c.dependency_id=d.identifier AND
                                      c.probe_type='COMPOSITE' AND c.checked_at_utc >= %s)
                                  AS availability_7d
                       FROM health_dependencies d
                       LEFT JOIN health_dependency_state s ON s.dependency_id=d.identifier
                       WHERE d.enabled=1 ORDER BY d.identifier LIMIT %s""",
                    (day, week, limit),
                )
                rows = list(cursor.fetchall())
        for row in rows:
            row["stale"] = bool(
                row.get("stale_after_utc") and row["stale_after_utc"] < timestamp
            )
            for key in ("availability_24h", "availability_7d", "latency_ms"):
                if row.get(key) is not None:
                    row[key] = float(row[key])
            row["current_state"] = row.get("current_state") or "UNKNOWN"
        return rows

    def detail(self, dependency_id: str, *, now: datetime) -> dict | None:
        catalog = [
            row for row in self.catalog(now=now, limit=100)
            if row["identifier"] == dependency_id
        ]
        if not catalog:
            return None
        since = utc_naive(now - timedelta(days=7))
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT probe_type, observed_state, checked_at_utc, latency_ms,
                              reason_code, technical_detail, http_status
                       FROM health_dependency_checks
                       WHERE dependency_id=%s AND checked_at_utc >= %s
                       ORDER BY checked_at_utc DESC LIMIT 200""",
                    (dependency_id, since),
                )
                checks = list(cursor.fetchall())
                cursor.execute(
                    """SELECT id, state, opened_at_utc, recovered_at_utc,
                              reason_code, technical_detail, internal_alert_count
                       FROM health_dependency_incidents
                       WHERE dependency_id=%s
                       ORDER BY opened_at_utc DESC LIMIT 100""",
                    (dependency_id,),
                )
                incidents = list(cursor.fetchall())
        return {"dependency": catalog[0], "checks": checks, "incidents": incidents}

    def incidents(self, *, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Limite inválido.")
        with self._read_transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT i.id, i.dependency_id, d.name, i.state,
                              i.opened_at_utc, i.recovered_at_utc,
                              i.reason_code, i.technical_detail,
                              i.internal_alert_count
                       FROM health_dependency_incidents i
                       JOIN health_dependencies d ON d.identifier=i.dependency_id
                       ORDER BY i.opened_at_utc DESC LIMIT %s""",
                    (limit,),
                )
                return list(cursor.fetchall())

    def link_execution(self, *, execution_id: str, incident_id: str, now: datetime) -> None:
        """Associação opt-in para execuções futuras; nunca faz backfill."""
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO health_execution_dependency_incidents
                       (execution_id, incident_id, linked_at_utc)
                       VALUES (%s,%s,%s)
                       ON DUPLICATE KEY UPDATE execution_id=execution_id""",
                    (execution_id, incident_id, utc_naive(now)),
                )
