# Códigos preservados no histórico, mas excluídos da seleção.
OCORRENCIAS_IGNORADAS = frozenset({
    "93",  # SSWSCAN WHP
})


def ocorrencia_ignorada(codigo):
    valor = str(codigo or "").strip()

    return (
        valor.isdigit()
        and str(int(valor)).zfill(2) in OCORRENCIAS_IGNORADAS
    )
