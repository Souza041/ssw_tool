import csv
import re
from pathlib import Path

from .chaves import (
    normalizar_cte,
    normalizar_cte_op455,
    normalizar_ctrc,
)


COLUNAS = {
    "OP435": {
        "CTRC",
        "NUMERO CT-e/NFPS",
        "PAGADOR",
        "NRO PACOTE",
    },
    "OP455": {
        "Serie/Numero CTRC",
        "Serie/Numero CT-e",
        "Numero do Pacote de Arquivamento",
    },
    "OP930": {
        "CTRC",
        "SERIE_CTE",
        "NRO_CTE",
        "COD_OCOR",
        "DATA_OCOR",
        "HORA_OCOR",
        "DIA_INCLUSAO_OCOR",
        "HORA_INCLUSAO_OCOR",
        "CANCELADO",
    },
}


def identificar(relatorio, dados):
    campo = (
        "Serie/Numero CTRC"
        if relatorio == "OP455"
        else "CTRC"
    )

    original = dados[campo]
    chave = normalizar_ctrc(original)

    if not chave or len(chave) > 30:
        raise ValueError("CTRC inválido ou sem sigla.")

    if relatorio == "OP435":
        documento = normalizar_cte(
            dados["NUMERO CT-e/NFPS"]
        )

    elif relatorio == "OP455":
        documento = normalizar_cte_op455(
            dados["Serie/Numero CT-e"]
        )

    else:
        serie = dados["SERIE_CTE"].strip()
        numero = dados["NRO_CTE"].strip()

        if serie and not numero:
            raise ValueError("CT-e com série, mas sem número.")

        texto = f"{serie} {numero}".strip()
        documento = normalizar_cte(texto) if texto else None

    if documento is None:
        raise ValueError("Número de CT-e ausente ou inválido.")

    serie, numero = documento

    if len(numero) > 9 or (
        serie is not None and len(serie) > 3
    ):
        raise ValueError("Série ou número de CT-e inválido.")

    return chave, original, serie, numero


def ler_registros(relatorio, arquivo):
    relatorio = relatorio.upper()

    if relatorio not in COLUNAS:
        raise ValueError("Relatório inválido.")

    encoding = (
        "utf-8-sig"
        if relatorio == "OP435"
        else "latin1"
    )

    cabecalho = None

    with Path(arquivo).open(
        encoding=encoding,
        newline="",
    ) as handle:
        reader = csv.reader(handle, delimiter=";")

        for linha, valores in enumerate(reader, start=1):
            if not valores or not any(
                valor.strip() for valor in valores
            ):
                continue

            if cabecalho is None:
                if (
                    relatorio == "OP455"
                    and valores[0].strip() != "1"
                ):
                    continue

                cabecalho = [
                    valor.strip() for valor in valores
                ]

                nomes = [
                    nome for nome in cabecalho if nome
                ]

                if len(nomes) != len(set(nomes)):
                    raise ValueError(
                        "Cabeçalho com nomes repetidos."
                    )

                faltantes = (
                    COLUNAS[relatorio] - set(cabecalho)
                )

                if faltantes:
                    raise ValueError(
                        "Colunas ausentes: "
                        + ", ".join(sorted(faltantes))
                    )

                continue

            if relatorio == "OP455":
                tipo = valores[0].strip()

                if tipo == "9":
                    continue

                if tipo != "2":
                    raise ValueError(
                        f"Linha {linha}: registro inesperado."
                    )

            # A OP455 recebida tem um cabeçalho vazio extra.
            if (
                len(valores) == len(cabecalho) - 1
                and cabecalho[-1] == ""
            ):
                valores = valores + [""]

            if len(valores) != len(cabecalho):
                raise ValueError(
                    f"Linha {linha}: quantidade de "
                    "colunas incompatível."
                )

            dados = {
                nome: valor
                for nome, valor in zip(cabecalho, valores)
                if nome
            }

            if relatorio == "OP435":
                pagador = re.sub(
                    r"\s+\((?:B2|B\.)\)\s*$",
                    "",
                    dados["PAGADOR"].strip(),
                    flags=re.I,
                )

                pagador = " ".join(
                    pagador.upper().split()
                )

                if pagador != "LG ELECTRONICS DO BRASIL":
                    raise ValueError(
                        f"Linha {linha}: utilize a OP435 "
                        "já filtrada para LG."
                    )

            try:
                chave, original, serie, numero = identificar(
                    relatorio,
                    dados,
                )
            except ValueError as exc:
                raise ValueError(
                    f"Linha {linha}: {exc}"
                ) from None

            yield {
                "numero_linha": linha,
                "ctrc_chave": chave,
                "ctrc_original": original,
                "cte_serie": serie,
                "cte_numero": numero,
                "dados": dados,
            }

    if cabecalho is None:
        raise ValueError("Cabeçalho não encontrado.")
