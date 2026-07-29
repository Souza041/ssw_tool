import re
import unicodedata

from typing import Any


# Preencheremos conforme validarmos as regras reais da operação.
CODIGOS_DEBITO_CONFIRMADO = {
    "11",
    "54",
}

CODIGOS_NAO_DEBITO = set()

# Termos que indicam que a pendência foi resolvida,
# aceita ou encerrada.
TERMOS_ANULAM_DEBITO = {
    "ACEITO PELO CLIENTE",
    "ACEITA PELO CLIENTE",
    "CLIENTE ACEITOU",
    "REGULARIZADO",
    "REGULARIZADA",
    "RESOLVIDO",
    "RESOLVIDA",
    "SEM DEBITO",
    "SEM DÉBITO",
    "CORTESIA AUTORIZADA",
    "CORTESIA APROVADA",
}

# Termos que ajudam a confirmar uma pendência ativa.
# Não são suficientes sozinhos; devem ser usados junto
# de um código parametrizado.
TERMOS_INDICAM_DEBITO = {
    "AVARIA",
    "FALTA",
    "EXTRAVIO",
    "PERDA",
    "DANO",
    "RECUSA",
    "ENTREGA PARCIAL",
}


def normalizar_texto(valor: object) -> str:
    texto = str(valor or "").strip().upper()

    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ASCII", "ignore").decode("ASCII")

    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()


def validar_debito(
    ultima_ocorrencia: dict | None,
    ocorrencia_relatorio_confere: str,
) -> dict:
    if not ultima_ocorrencia:
        return {
            "status": "PENDENTE",
            "debito": False,
            "motivo": "Histórico OP101 sem ocorrências.",
            "regra": "SEM_HISTORICO",
        }

    codigo = str(
        ultima_ocorrencia.get("codigo", "")
    ).strip().lstrip("0")

    codigo = codigo or "0"

    descricao = normalizar_texto(
        ultima_ocorrencia.get("descricao", "")
    )

    complemento = normalizar_texto(
        ultima_ocorrencia.get("complemento", "")
    )

    confere_relatorio = (
        str(ocorrencia_relatorio_confere)
        .strip()
        .upper()
        == "SIM"
    )

    texto_completo = f"{descricao} {complemento}".strip()

    # Uma ocorrência posterior pode anular o possível débito.
    for termo in TERMOS_ANULAM_DEBITO:
        termo_normalizado = normalizar_texto(termo)

        if termo_normalizado in texto_completo:
            return {
                "status": "NAO_DEBITO",
                "debito": False,
                "motivo": (
                    f"Última ocorrência indica resolução/aceite: "
                    f"{termo}"
                ),
                "regra": "TERMO_ANULA_DEBITO",
            }

    if codigo in CODIGOS_NAO_DEBITO:
        return {
            "status": "NAO_DEBITO",
            "debito": False,
            "motivo": (
                f"Última ocorrência {codigo} parametrizada "
                f"como não débito."
            ),
            "regra": "CODIGO_NAO_DEBITO",
        }

    if codigo in CODIGOS_DEBITO_CONFIRMADO:
        if not confere_relatorio:
            return {
                "status": "PENDENTE",
                "debito": False,
                "motivo": (
                    "A última ocorrência da OP101 não corresponde "
                    "à ocorrência do relatório OP930."
                ),
                "regra": "OCORRENCIA_DIVERGENTE",
            }

        termos_encontrados = [
            termo
            for termo in TERMOS_INDICAM_DEBITO
            if normalizar_texto(termo) in texto_completo
        ]

        detalhe = ""

        if termos_encontrados:
            detalhe = (
                " Termos identificados: "
                + ", ".join(termos_encontrados)
                + "."
            )

        return {
            "status": "DEBITO",
            "debito": True,
            "motivo": (
                f"Última ocorrência {codigo} confirma "
                f"débito ativo.{detalhe}"
            ),
            "regra": "CODIGO_DEBITO_CONFIRMADO",
        }

    return {
        "status": "PENDENTE",
        "debito": False,
        "motivo": (
            f"Última ocorrência {codigo} ainda não possui "
            "regra de débito parametrizada."
        ),
        "regra": "CODIGO_NAO_PARAMETRIZADO",
    }