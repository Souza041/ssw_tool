"""Contrato de métricas OP455 com o Nexus Health Center."""

from modules.health_center.models import Bot, Outcome, Status


BOT = Bot(
    identifier="metricas_op455",
    name="Métricas · SSW OP455",
    description="Atualização dos indicadores operacionais a partir do SSW.",
    category="ssw",
    schedule_source="agendamento Nexus / execução manual",
)


def classify_result(result: dict) -> Outcome:
    """Classifica o resultado da atualização sem expor dados operacionais."""

    success = result.get("success") is True

    try:
        total = max(0, int(result.get("total") or 0))
    except (TypeError, ValueError, OverflowError):
        total = 0

    return Outcome(
        status=Status.SUCCESS if success else Status.FAILED,
        processed=total,
        succeeded=total if success else 0,
        errors=0 if success else 1,
        summary="",
        metadata={},
    )
