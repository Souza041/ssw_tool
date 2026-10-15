import logging
import os

from datetime import date, datetime
from threading import Lock
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from modules.ocorrencia_73.calendario import (
    AGENDAMENTOS_OC73,
    calcular_janelas_oc73,
)
from modules.ocorrencia_73.config import DRY_RUN
from modules.ocorrencia_73.repository import Ocorrencia73Repository
from modules.ocorrencia_73.service import Ocorrencia73Service


logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("America/Sao_Paulo")
MISFIRE_GRACE_TIME = 60

_scheduler = BackgroundScheduler(timezone=TIMEZONE)
_startup_lock = Lock()


def env_bool(
    nome: str,
    padrao: bool = False,
) -> bool:
    valor = os.getenv(nome)

    if valor is None:
        logger.warning(
            "[OCORRENCIA 73] Variável %s não encontrada.",
            nome,
        )
        return padrao

    valor_normalizado = str(valor).strip().lower()

    logger.info(
        "[OCORRENCIA 73] %s=%r",
        nome,
        valor_normalizado,
    )

    return valor_normalizado in {
        "1",
        "true",
        "yes",
        "sim",
        "on",
    }


def executar_ocorrencia_73_agendada(
    fluxo: str,
    data_referencia: date | None = None,
) -> dict | None:
    fluxo = str(fluxo or "").strip().upper()
    data_referencia = (
        data_referencia
        or datetime.now(TIMEZONE).date()
    )

    if not calcular_janelas_oc73(
        data_referencia,
        fluxo=fluxo,
    ):
        logger.warning(
            "[OCORRENCIA 73][%s] Disparo ignorado fora do calendário: %s.",
            fluxo,
            data_referencia.isoformat(),
        )
        return None

    logger.info(
        "[OCORRENCIA 73][%s] Iniciando execução agendada para %s.",
        fluxo,
        data_referencia.isoformat(),
    )

    repository = Ocorrencia73Repository()

    try:
        reserva = repository.reservar_execucao_agendada(
            data_referencia=data_referencia,
            fluxo=fluxo,
            dry_run=DRY_RUN,
        )

        if not reserva["reservada"]:
            logger.warning(
                "[OCORRENCIA 73][%s] Execução não iniciada: %s "
                "(execucao_id=%s).",
                fluxo,
                reserva["motivo"],
                reserva.get("execucao_id"),
            )
            return None

        service = Ocorrencia73Service(repository=repository)
        resultado = service.executar(
            data_referencia=data_referencia,
            triggered_by=reserva["triggered_by"],
            fluxo=fluxo,
            execucao_id_reservada=reserva["execucao_id"],
        )

        logger.info(
            "[OCORRENCIA 73][%s] Execução %s finalizada: "
            "relatório=%s, filtrados=%s, consultados=%s, dry_run=%s.",
            fluxo,
            reserva["execucao_id"],
            resultado.get("total_relatorio", 0),
            resultado.get("total_filtrado", 0),
            resultado.get("total_consultado", 0),
            resultado.get("dry_run"),
        )
        return resultado

    except Exception:
        logger.exception(
            "[OCORRENCIA 73][%s] Falha na execução agendada.",
            fluxo,
        )
        return None


def iniciar_scheduler_ocorrencia_73() -> bool:
    if not env_bool("OCORRENCIA_73_ENABLED", False):
        logger.warning(
            "[OCORRENCIA 73] Scheduler não iniciado: "
            "OCORRENCIA_73_ENABLED != true."
        )
        return False

    with _startup_lock:
        if _scheduler.running:
            logger.info(
                "[OCORRENCIA 73] Scheduler já está ativo neste processo."
            )
            return False

        for agendamento in AGENDAMENTOS_OC73:
            job_id = f"ocorrencia_73_{agendamento.fluxo.lower()}"
            trigger = CronTrigger(
                day_of_week=agendamento.dias_apscheduler,
                hour=agendamento.hora,
                minute=agendamento.minuto,
                timezone=TIMEZONE,
            )

            _scheduler.add_job(
                executar_ocorrencia_73_agendada,
                trigger=trigger,
                kwargs={"fluxo": agendamento.fluxo},
                id=job_id,
                name=f"OC73 {agendamento.fluxo} recorrente",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=MISFIRE_GRACE_TIME,
            )

            logger.info(
                "[OCORRENCIA 73][%s] Job %s registrado: "
                "%s às %02d:%02d, timezone=%s, misfire=%ss.",
                agendamento.fluxo,
                job_id,
                agendamento.dias_apscheduler,
                agendamento.hora,
                agendamento.minuto,
                TIMEZONE.key,
                MISFIRE_GRACE_TIME,
            )

        _scheduler.start()
        logger.info(
            "[OCORRENCIA 73] Scheduler recorrente iniciado com %s jobs.",
            len(AGENDAMENTOS_OC73),
        )
        return True
