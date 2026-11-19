"""Contrato de Métricas OP455 com o Nexus Health Center."""

from modules.health_center.models import Bot, Outcome, Status


BOT = Bot(
    identifier="metricas_op455",
    name="Métricas OP455",
    description="Coleta OP455 e atualiza o painel de métricas.",
    category="metricas",
    schedule_source="VPS -> Nexus Local Agent Windows",
)


def classify_result(result: dict) -> Outcome:
    success = result.get("success") is True
    total = result.get("total", 0)
    total = total if type(total) is int and total >= 0 else 0
    return Outcome(
        status=Status.SUCCESS if success else Status.FAILED,
        processed=total,
        succeeded=total if success else 0,
        errors=0 if success else 1,
        summary="Métricas OP455 atualizadas." if success else "Falha nas métricas OP455.",
        metadata={"remote_executor": "windows"},
    )
