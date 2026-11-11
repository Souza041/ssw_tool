"""Mapeamento somente de leitura do resultado da OC73."""

from modules.health_center.models import Bot, Outcome, Status


BOT = Bot(
    identifier="ocorrencia_73",
    name="Ocorrência 73",
    description="Consulta OP455 e OP101 e acompanha a ocorrência 73.",
    category="ocorrencias",
    schedule_source="cron_ou_scheduler",
    parameters={},
)


def _count(data: dict, key: str) -> int:
    value = data.get(key, 0)
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ValueError(f"Contador inválido: {key}")
    return value


def classify_result(result: dict) -> Outcome:
    consulted = _count(result, "total_consultado")
    query_errors = _count(result, "total_erro_op101")
    launch_errors = _count(result, "total_erro_lancamento")
    technical_errors = _count(result, "total_erro_tecnico")
    uncertain = _count(result, "total_incerto")
    blocked = _count(result, "total_bloqueado")
    # Indicadores técnicos podem sobrepor os contadores legados por etapa.
    errors = max(query_errors + launch_errors, technical_errors) + uncertain

    if result.get("success") is not True:
        status = Status.FAILED
    elif errors or blocked:
        status = Status.PARTIAL
    else:
        status = Status.SUCCESS

    diagnostic = result.get("diagnostico") or {}
    metadata = {
        "dry_run": result.get("dry_run") is True,
        "total_op455": _count(result, "total_relatorio"),
        "total_after_filters": _count(result, "total_filtrado"),
        "total_found_op101": _count(result, "total_encontrado_op101"),
        "total_not_found_op101": _count(result, "total_nao_encontrado_op101"),
        "total_already_exists": _count(result, "total_ja_existia"),
        "total_pending_launch": _count(result, "total_pendente_lancamento"),
        "total_launched": _count(result, "total_lancado"),
        "total_launch_errors": launch_errors,
        "total_query_errors": query_errors,
        "total_ignored_test_scope": _count(result, "total_ignorado_fora_teste"),
        "total_ignored_limit": _count(result, "total_ignorado_limite"),
        "cwb_curitiba": _count(diagnostic, "cwb_curitiba"),
        "joi_florianopolis": _count(diagnostic, "joi_florianopolis"),
        "big_oc64_allowed_customer": _count(diagnostic, "joi_big_oc64_cliente"),
        "allowed_customer_count": _count(diagnostic, "cliente"),
        "rejected_customer_count": max(
            0, _count(result, "total_relatorio") - _count(diagnostic, "cliente")
        ),
    }
    return Outcome(
        status=status,
        processed=consulted,
        succeeded=max(0, consulted - errors),
        errors=errors,
        summary=(
            "OC73: OP455=%d; filtrados=%d; OP101=%d; erros=%d."
            % (
                metadata["total_op455"], metadata["total_after_filters"],
                consulted, errors,
            )
        ),
        metadata=metadata,
    )
