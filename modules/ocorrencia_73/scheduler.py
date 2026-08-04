import logging
import os

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from modules.ocorrencia_73.service import Ocorrencia73Service


logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("America/Sao_Paulo")

_scheduler = BackgroundScheduler(
    timezone=TIMEZONE,
)


def env_bool(nome: str, padrao: bool = False) -> bool:
    valor = os.getenv(
        nome,
        str(padrao),
    )

    return valor.strip().lower() in {
        "1",
        "true",
        "yes",
        "sim",
        "on",
    }


def executar_ocorrencia_73_agendada() -> None:
    logger.info(
        "[OCORRENCIA 73] Iniciando execução agendada."
    )

    try:
        service = Ocorrencia73Service()

        resultado = service.executar(
            triggered_by="scheduler",
        )

        logger.info(
            "[OCORRENCIA 73] Execução finalizada: "
            "relatório=%s, filtrados=%s, consultados=%s, "
            "encontrados=%s, erros=%s, dry_run=%s",
            resultado.get("total_relatorio", 0),
            resultado.get("total_filtrado", 0),
            resultado.get("total_consultado", 0),
            resultado.get("total_encontrado_op101", 0),
            resultado.get("total_erro_op101", 0),
            resultado.get("dry_run"),
        )

    except Exception:
        logger.exception(
            "[OCORRENCIA 73] Falha na execução agendada."
        )


def iniciar_scheduler_ocorrencia_73() -> None:
    run_enabled = env_bool(
        "RUN",
        False,
    )

    if not run_enabled:
        logger.warning(
            "[OCORRENCIA 73] Scheduler não iniciado: RUN != true."
        )
        return

    if _scheduler.running:
        logger.info(
            "[OCORRENCIA 73] Scheduler já está ativo."
        )
        return

    horario_primeiro_teste = datetime(
        year=2026,
        month=8,
        day=4,
        hour=20,
        minute=0,
        second=0,
        tzinfo=TIMEZONE,
    )

    agora = datetime.now(TIMEZONE)

    if horario_primeiro_teste <= agora:
        logger.warning(
            "[OCORRENCIA 73] Horário do primeiro teste já passou: %s.",
            horario_primeiro_teste.isoformat(),
        )
        return

    _scheduler.add_job(
        executar_ocorrencia_73_agendada,
        trigger=DateTrigger(
            run_date=horario_primeiro_teste,
            timezone=TIMEZONE,
        ),
        id="ocorrencia_73_primeiro_teste",
        name="Primeiro teste ocorrência 73",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    _scheduler.start()

    logger.info(
        "[OCORRENCIA 73] Primeiro teste agendado para %s.",
        horario_primeiro_teste.strftime(
            "%d/%m/%Y às %H:%M:%S"
        ),
    )