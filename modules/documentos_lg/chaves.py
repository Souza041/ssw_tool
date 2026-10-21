import re


def normalizar_cte(valor):
    """Retorna (serie, numero). Serie pode ser None."""
    if valor is None or isinstance(valor, bool):
        return None

    texto = str(valor).replace("\xa0", " ").strip()
    texto = re.sub(r"^(\d+)\.0+$", r"\1", texto)

    partes = re.fullmatch(r"(?:(\d+)\s+)?(\d+)", texto)
    if not partes:
        return None

    serie, numero = partes.groups()

    return (
        str(int(serie)) if serie is not None else None,
        str(int(numero)),
    )

def normalizar_ctrc(valor):
    """Preserva a sigla da unidade e retira o DV da chave."""
    texto = str(valor or "").replace("\xa0", " ").strip().upper()

    partes = re.fullmatch(
        r"([A-Z0-9]{3})\s*(\d+)(?:-\d)?",
        texto,
    )
    if not partes:
        return None

    sigla, numero = partes.groups()
    return sigla + str(int(numero))

def normalizar_cte_op455(valor):
    """Separa o formato SSSNNNNNNNNN exportado pela OP455."""
    texto = str(valor or "").replace("\xa0", " ").strip()

    if re.fullmatch(r"\d{12}", texto):
        return (
            str(int(texto[:3])),
            str(int(texto[3:])),
        )

    return normalizar_cte(texto)
