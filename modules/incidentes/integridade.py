"""Validações de cardinalidade para a auditoria de incidentes."""

from collections import Counter
import hashlib
import json

import pandas as pd


COLUNAS_ORIGEM_OP930 = (
    "CTRC",
    "EMISSAO_CTRC",
    "SERIE_CTE",
    "NRO_CTE",
    "SERIE_NOTA_FISCAL",
    "NRO_NOTA_FISCAL",
    "PEDIDO",
    "EMPR_OCOR",
    "UNID_OCOR",
    "DIA_INCLUSAO_OCOR",
    "HORA_INCLUSAO_OCOR",
    "DATA_OCOR",
    "HORA_OCOR",
    "USUARIO_OCOR",
    "COD_OCOR",
    "DESCRICAO_OCOR",
    "COMPLEMENTO_OCOR",
    "DATA_ENTREGA",
    "DESCR_ITEM",
    "PRIMEIRO_MANIFESTO",
    "CANCELADO",
    "CNPJ_PAGADOR",
    "NOME_PAGADOR",
    "_OC_NORMALIZADA",
    "GRUPO_CLIENTE",
    "CNPJ_GRUPO",
)


def assinatura_registro(linha):
    valores = [
        None if pd.isna(valor) else str(valor)
        for valor in linha
    ]

    conteudo = json.dumps(
        valores,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        conteudo.encode("utf-8")
    ).hexdigest()


def validar_identidade_registros(
    *,
    original: pd.DataFrame,
    atual: pd.DataFrame,
    etapa: str,
    log,
) -> None:
    for nome, base in (
        ("original", original),
        ("atual", atual),
    ):
        ausentes = [
            coluna
            for coluna in COLUNAS_ORIGEM_OP930
            if coluna not in base.columns
        ]

        if ausentes:
            raise ValueError(
                f"Integridade [{etapa}]: "
                f"colunas ausentes na base {nome}: "
                f"{ausentes}"
            )

    def gerar_contador(base):
        registros = base.loc[
            :,
            list(COLUNAS_ORIGEM_OP930),
        ]

        return Counter(
            assinatura_registro(linha)
            for linha in registros.itertuples(
                index=False,
                name=None,
            )
        )

    esperado = gerar_contador(original)
    obtido = gerar_contador(atual)

    if esperado != obtido:
        removidos = sum(
            (esperado - obtido).values()
        )
        adicionados = sum(
            (obtido - esperado).values()
        )

        raise ValueError(
            f"Integridade comprometida [{etapa}]: "
            f"{removidos} registros ausentes ou alterados; "
            f"{adicionados} registros novos ou alterados."
        )

    log(
        f"Integridade de identidade OK [{etapa}]: "
        f"{len(atual)} registros preservados."
    )

def validar_quantidade_linhas(*, etapa: str, quantidade_esperada: int, quantidade_obtida: int, log) -> None:
    if quantidade_obtida != quantidade_esperada:
        raise ValueError(
            f"Integridade comprometida na etapa {etapa}: "
            f"esperado={quantidade_esperada}, obtido={quantidade_obtida}"
        )
    log(f"Integridade OK [{etapa}]: {quantidade_obtida} registros preservados.")
