from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List

import numpy as np
import pandas as pd


def serializar_valor(
    valor: Any,
) -> Any:
    """
    Converte valores do Pandas, NumPy e Python
    para tipos compatíveis com JSON.
    """

    if valor is None:
        return None

    if isinstance(valor, pd.Timestamp):
        if pd.isna(valor):
            return None

        return valor.isoformat()

    if isinstance(valor, datetime):
        return valor.isoformat()

    if isinstance(valor, date):
        return valor.isoformat()

    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, np.integer):
        return int(valor)

    if isinstance(valor, np.floating):
        if np.isnan(valor):
            return None

        if np.isinf(valor):
            return None

        return float(valor)

    if isinstance(valor, np.bool_):
        return bool(valor)

    if isinstance(valor, np.ndarray):
        return [
            serializar_valor(item)
            for item in valor.tolist()
        ]

    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass

    return valor


def serializar_dict(
    dados: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        str(chave): serializar_objeto(valor)
        for chave, valor in dados.items()
    }


def serializar_lista(
    dados: List[Any],
) -> List[Any]:
    return [
        serializar_objeto(valor)
        for valor in dados
    ]


def serializar_objeto(
    valor: Any,
) -> Any:
    if isinstance(valor, dict):
        return serializar_dict(valor)

    if isinstance(valor, list):
        return serializar_lista(valor)

    if isinstance(valor, tuple):
        return serializar_lista(list(valor))

    if isinstance(valor, set):
        return serializar_lista(list(valor))

    if isinstance(valor, pd.DataFrame):
        return dataframe_para_registros(valor)

    if isinstance(valor, pd.Series):
        return serie_para_lista(valor)

    return serializar_valor(valor)


def dataframe_para_registros(
    df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []

    registros = df.to_dict(
        orient="records"
    )

    return [
        serializar_dict(registro)
        for registro in registros
    ]


def serie_para_lista(
    serie: pd.Series,
) -> List[Any]:
    if serie is None or serie.empty:
        return []

    return [
        serializar_valor(valor)
        for valor in serie.tolist()
    ]


def limpar_registros_vazios(
    registros: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Remove registros totalmente vazios.
    """

    resultado = []

    for registro in registros:
        possui_valor = any(
            valor not in (
                None,
                "",
                [],
                {},
            )
            for valor in registro.values()
        )

        if possui_valor:
            resultado.append(registro)

    return resultado