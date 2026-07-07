from apscheduler.schedulers.background import BackgroundScheduler

from modules.metricas.service import MetricasService

scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")


def atualizar_metricas_automatico():
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