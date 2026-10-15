"""Validação estrita das referências globais da OC73."""

import re

PADRAO_REFERENCIA = re.compile(r"OC73-[0-9A-F]{20}\Z")


def referencia_valida(referencia):
    return (
        isinstance(referencia, str)
        and PADRAO_REFERENCIA.fullmatch(referencia) is not None
    )


def ocorrencia_confirma_referencia(ocorrencia, referencia):
    if not referencia_valida(referencia):
        return False

    if ocorrencia is None:
        return False

    if isinstance(ocorrencia, dict):
        codigo = ocorrencia.get("codigo")
        complemento = ocorrencia.get("complemento")
    else:
        codigo = getattr(ocorrencia, "codigo", None)
        complemento = getattr(ocorrencia, "complemento", None)

    if str(codigo or "").strip() != "73":
        return False

    complemento = str(complemento or "")

    padrao = (
        rf"(?<![A-Z0-9-])REF:\s*"
        rf"{re.escape(referencia)}"
        rf"(?![A-Z0-9-])"
    )

    return re.search(padrao, complemento) is not None
