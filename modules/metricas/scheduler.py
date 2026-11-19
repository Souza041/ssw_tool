from apscheduler.schedulers.background import BackgroundScheduler

from modules.metricas.service import MetricasService

scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")


def atualizar_metricas_automatico():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from modules.local_agent.delegation import (
        delegation_enabled, enqueue_ssw_task, scheduled_key,
    )
    if delegation_enabled():
        hour = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%d%H")
        return enqueue_ssw_task(
            "ssw.metricas_op455",
            {"origin": "scheduler", "days": 30},
            idempotency_key=scheduled_key("ssw.metricas_op455", hour),
            concurrency_key="ssw.metricas_op455",
            priority=70,
            coalesce_active=True,
        )
    service = MetricasService()
    result = service.atualizar_op455(
        triggered_by="scheduler",
        triggered_user_id=None,
        dias=30,
    )

    print("[METRICAS][SCHEDULER]", result)


def iniciar_scheduler_metricas():
    if scheduler.running:
        return

    scheduler.add_job(
        atualizar_metricas_automatico,
        trigger="interval",
        hours=1,
        id="metricas_op455_hourly",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    print("[METRICAS][SCHEDULER] iniciado")
