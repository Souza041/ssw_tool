"""Contrato do Documentos GCE com o Nexus Health Center."""

from modules.health_center.models import Bot, Outcome, Status


BOT = Bot(
    identifier="documentos_gce",
    name="GCE · Portal × SSW",
    description="Coleta, importação e conciliação Portal GCE × SSW.",
    category="gce",
    schedule_source="agendamento externo / execução manual Nexus",
)


def classify_result(result: dict) -> Outcome:
    matching = result.get("matching") or {}
    processed = int(matching.get("total") or 0)
    errors = int(matching.get("errors") or 0)
    return Outcome(
        status=Status.SUCCESS if result.get("success") else Status.FAILED,
        processed=processed,
        succeeded=int(matching.get("matched") or 0),
        errors=errors,
        # O Health Center aceita somente o contrato sanitizado do piloto.
        # O vínculo detalhado permanece no histórico document_pipeline_runs.
        summary="",
        metadata={},
    )
