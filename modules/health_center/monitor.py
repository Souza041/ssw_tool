"""Observador opt-in: nunca executa a operação mais de uma vez."""

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, TypeVar

from modules.health_center.models import Bot, Outcome, Status


T = TypeVar("T")
logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv("HEALTH_CENTER_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "sim", "on",
    }


def _monitor_warning(stage: str, error: BaseException) -> None:
    # Nunca enviar str(error), traceback, SQL ou argumentos para o log.
    try:
        logger.warning(
            "Health Center indisponível em %s (%s).",
            stage,
            type(error).__name__,
        )
    except Exception:
        pass


def run_monitored(
    *,
    bot: Bot,
    origin: str,
    operation: Callable[[], T],
    classify: Callable[[T], Outcome],
    responsible_user: str | None = None,
    repository=None,
) -> T:
    """Retorna ou relança exatamente o resultado da operação observada.

    A operação é chamada uma única vez, inclusive se o banco falhar.
    Com o flag desligado, o repositório nem é construído/importado.
    """
    if not enabled():
        return operation()

    execution_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    start_tick = time.monotonic()
    recorded = False

    try:
        if repository is None:
            from modules.health_center.repository import HealthRepository

            repository = HealthRepository()
        repository.start(
            execution_id=execution_id,
            bot=bot,
            origin=origin,
            started_at=started_at,
            responsible_user=responsible_user,
            correlation_id=execution_id,
            code_version=os.getenv("HEALTH_CENTER_CODE_VERSION") or None,
        )
        recorded = True
    except Exception as error:
        _monitor_warning("registro de início", error)

    try:
        result = operation()
    except BaseException as error:
        if recorded:
            try:
                finished = repository.finish(
                    execution_id=execution_id,
                    outcome=Outcome(
                        status=Status.FAILED,
                        errors=1,
                        summary="Execução interrompida por exceção.",
                    ),
                    finished_at=datetime.now(timezone.utc),
                    duration_seconds=time.monotonic() - start_tick,
                    error_message=f"{type(error).__name__}: falha na execução",
                )
                if finished is False:
                    _monitor_warning("registro de falha", LookupError())
            except Exception as monitor_error:
                _monitor_warning("registro de falha", monitor_error)
        raise

    if recorded:
        try:
            outcome = classify(result)
            finished = repository.finish(
                execution_id=execution_id,
                outcome=outcome,
                finished_at=datetime.now(timezone.utc),
                duration_seconds=time.monotonic() - start_tick,
            )
            if finished is False:
                _monitor_warning("registro de conclusão", LookupError())
        except Exception as error:
            _monitor_warning("registro de conclusão", error)

    return result
