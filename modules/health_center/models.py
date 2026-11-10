"""Contratos pequenos e independentes de FastAPI e do SSW."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class Bot:
    identifier: str
    name: str
    description: str = ""
    category: str = "geral"
    schedule_source: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    status: Status
    processed: int = 0
    succeeded: int = 0
    errors: int = 0
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, Status):
            raise ValueError("Status inválido.")
        if self.status in (Status.PENDING, Status.RUNNING):
            raise ValueError("Resultado precisa de um status final.")
        for count in (self.processed, self.succeeded, self.errors):
            if type(count) is not int or not 0 <= count <= (2**64 - 1):
                raise ValueError("Contador fora do intervalo BIGINT UNSIGNED.")
