"""Contratos do monitor de dependências, sem vínculos com bots operacionais."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DependencyState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"
    MAINTENANCE = "MAINTENANCE"


class ProbeKind(str, Enum):
    COMPOSITE = "COMPOSITE"
    DNS = "DNS"
    TCP = "TCP"
    TLS = "TLS"
    HTTP = "HTTP"
    MYSQL = "MYSQL"


@dataclass(frozen=True)
class DependencyDefinition:
    identifier: str
    name: str
    host: str
    port: int
    probes: tuple[ProbeKind, ...]
    scheme: str = "https"
    path: str = "/"
    enabled: bool = False
    allow_private: bool = False
    mysql_env_prefix: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.identifier or len(self.identifier) > 100:
            raise ValueError("Identificador de dependência inválido.")
        if not self.name or len(self.name) > 160:
            raise ValueError("Nome de dependência inválido.")
        if not self.host or len(self.host) > 253:
            raise ValueError("Host de dependência inválido.")
        if not 1 <= self.port <= 65535:
            raise ValueError("Porta de dependência inválida.")
        if self.scheme not in {"http", "https", "mysql"}:
            raise ValueError("Esquema de dependência inválido.")
        if not self.path.startswith("/") or len(self.path) > 500:
            raise ValueError("Caminho de dependência inválido.")
        if not self.probes:
            raise ValueError("Dependência precisa de ao menos um probe.")


@dataclass(frozen=True)
class ProbeResult:
    dependency_id: str
    probe: ProbeKind
    state: DependencyState
    checked_at: datetime
    latency_ms: float | None
    reason_code: str
    detail: str
    http_status: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None:
            raise ValueError("Timestamp do probe precisa de fuso horário.")
        if self.latency_ms is not None and not 0 <= self.latency_ms <= 86_400_000:
            raise ValueError("Latência inválida.")
        if not self.reason_code or len(self.reason_code) > 80:
            raise ValueError("Código técnico inválido.")
        if len(self.detail) > 500:
            raise ValueError("Detalhe técnico excessivo.")

    @classmethod
    def unknown(cls, dependency_id: str, probe: ProbeKind, reason_code: str) -> "ProbeResult":
        return cls(
            dependency_id=dependency_id,
            probe=probe,
            state=DependencyState.UNKNOWN,
            checked_at=datetime.now(timezone.utc),
            latency_ms=None,
            reason_code=reason_code,
            detail="Verificação inconclusiva; detalhes internos foram omitidos.",
        )


@dataclass(frozen=True)
class HysteresisDecision:
    state: DependencyState
    consecutive_failures: int
    consecutive_successes: int
    open_incident: bool = False
    recover_incident: bool = False


def apply_hysteresis(
    *, current: DependencyState, observed: DependencyState,
    failures: int, successes: int, failure_threshold: int = 3,
    recovery_threshold: int = 2,
) -> HysteresisDecision:
    """Consolida estado sem transformar falha isolada em indisponibilidade."""
    if not 1 <= failure_threshold <= 20 or not 1 <= recovery_threshold <= 20:
        raise ValueError("Limiares de histerese inválidos.")
    if current is DependencyState.MAINTENANCE:
        return HysteresisDecision(current, failures, successes)
    if observed is DependencyState.UNKNOWN:
        return HysteresisDecision(DependencyState.UNKNOWN, failures, successes)
    if observed is DependencyState.HEALTHY:
        next_successes = successes + 1
        if current in {DependencyState.DOWN, DependencyState.DEGRADED, DependencyState.UNKNOWN}:
            recovered = next_successes >= recovery_threshold
            return HysteresisDecision(
                DependencyState.HEALTHY if recovered else current,
                0,
                next_successes,
                recover_incident=recovered and current is DependencyState.DOWN,
            )
        return HysteresisDecision(DependencyState.HEALTHY, 0, next_successes)

    next_failures = failures + 1
    if next_failures >= failure_threshold:
        return HysteresisDecision(
            DependencyState.DOWN,
            next_failures,
            0,
            open_incident=current is not DependencyState.DOWN,
        )
    return HysteresisDecision(DependencyState.DEGRADED, next_failures, 0)
