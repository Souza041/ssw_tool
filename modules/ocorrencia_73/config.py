import os


CLIENTES_PERMITIDOS = {
    "BUD COM. DE ELETRODOM. LTDA",
    "WHIRLPOOL SA",
    "MLOG ARMAZEM GERAL LTDA",
    "WHIRLPOOL S/A (C2)",
    "WHIRLPOOL S/A",
}

ROTAS_PERMITIDAS = {
    "JOI": {
        "FLORIANOPOLIS",
    },
    "CWB": {
        "CURITIBA",
    },
}

# Para as rotas de SC emitidas por JOI,
# somente entram CTRCs cuja última ocorrência seja 64.
UNIDADES_QUE_EXIGEM_OC64 = {
    "JOI",
}

CODIGO_OCORRENCIA_FILTRO_JOI = "64"

CODIGO_OCORRENCIA = "73"

DESCRICAO_OCORRENCIA = (
    "ENTREGA SERA REALIZADA AMANHA"
)

DRY_RUN = (
    os.getenv("OCORRENCIA_73_DRY_RUN", "true")
    .strip()
    .lower()
    in {"1", "true", "sim", "yes"}
)
