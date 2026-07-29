from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Iterable, List, Optional, Sequence

import pandas as pd

from modules.incidentes.schemas import DashboardFilters


ALIASES_COLUNAS = {
    "data": [
        "DATA_OCORRENCIA",
        "DATA_OCOR",
        "DATA",
        "DATA_EMISSAO_CTE",
        "DATA_EMISSAO",
    ],

    "grupo_cliente": [
        "GRUPO_CLIENTE",
        "NOME_GRUPO_CLIENTE",
        "GRUPO_ECONOMICO",
        "CLIENTE_GRUPO",
    ],

    "cliente": [
        "NOME_PAGADOR",
        "CLIENTE",
        "NOME_CLIENTE",
        "PAGADOR",
    ],

    "unidade": [
        "UNID_OCOR",
        "UNIDADE_OCOR",
        "UNIDADE_OCORRENCIA",
        "UNIDADE_CTRC",
        "UNIDADE",
        "FILIAL",
    ],

    "codigo_ocorrencia": [
        "COD_OCOR",
        "CODIGO_OCORRENCIA",
        "CODIGO_OCOR",
        "OCORRENCIA",
    ],

    "status_debito": [
        "STATUS_VALIDACAO_DEBITO",
        "STATUS_DEBITO",
        "VALIDACAO_DEBITO",
    ],

    "regra_debito": [
        "REGRA_VALIDACAO_DEBITO",
        "REGRA_DEBITO",
        "MOTIVO_VALIDACAO_DEBITO",
    ],

    "produto": [
        "DESCRICAO_PRODUTO_PREDOMINANTE",
        "PRODUTO_PREDOMINANTE",
        "PRODUTO",
    ],
}


def normalizar_nome_coluna(
    valor: object,
) -> str:
    texto = str(valor).strip().upper()

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(
            caractere
        )
    )

    texto = re.sub(
        r"[^A-Z0-9]+",
        "_",
        texto,
    )

    return texto.strip("_")


def normalizar_colunas(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = df.copy()

    base.columns = [
        normalizar_nome_coluna(coluna)
        for coluna in base.columns
    ]

    return base


def encontrar_coluna(
    df: pd.DataFrame,
    aliases: Sequence[str],
) -> Optional[str]:
    colunas = {
        normalizar_nome_coluna(coluna): coluna
        for coluna in df.columns
    }

    for alias in aliases:
        alias_normalizado = (
            normalizar_nome_coluna(alias)
        )

        if alias_normalizado in colunas:
            return colunas[alias_normalizado]

    return None


def encontrar_coluna_logica(
    df: pd.DataFrame,
    nome_logico: str,
) -> Optional[str]:
    aliases = ALIASES_COLUNAS.get(
        nome_logico,
        [],
    )

    return encontrar_coluna(
        df,
        aliases,
    )


def serie_texto(
    df: pd.DataFrame,
    coluna: Optional[str],
) -> pd.Series:
    if coluna is None or coluna not in df.columns:
        return pd.Series(
            "",
            index=df.index,
            dtype="object",
        )

    return (
        df[coluna]
        .fillna("")
        .astype(str)
        .str.strip()
    )


def serie_data(
    df: pd.DataFrame,
    coluna: Optional[str],
) -> pd.Series:
    if coluna is None or coluna not in df.columns:
        return pd.Series(
            pd.NaT,
            index=df.index,
            dtype="datetime64[ns]",
        )

    serie = df[coluna]

    if pd.api.types.is_datetime64_any_dtype(
        serie
    ):
        return pd.to_datetime(
            serie,
            errors="coerce",
        )

    try:
        return pd.to_datetime(
            serie,
            errors="coerce",
            dayfirst=True,
            format="mixed",
        )

    except (TypeError, ValueError):
        return pd.to_datetime(
            serie,
            errors="coerce",
            dayfirst=True,
        )


def normalizar_valor_filtro(
    valor: object,
) -> str:
    """
    Normaliza valores para comparações de filtros.

    Exemplos:
    DÉBITO       -> debito
    NÃO DÉBITO   -> nao_debito
    SEM-HISTÓRICO -> sem_historico
    """

    if valor is None:
        return ""

    texto = str(valor).strip()

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(
            caractere
        )
    )

    texto = texto.casefold()

    texto = re.sub(
        r"[^a-z0-9]+",
        "_",
        texto,
    )

    return texto.strip("_")


def aplicar_filtro_lista(
    df: pd.DataFrame,
    coluna: Optional[str],
    valores: Iterable[str],
) -> pd.DataFrame:
    valores_normalizados = {
        normalizar_valor_filtro(valor)
        for valor in valores
        if str(valor).strip()
    }

    if not valores_normalizados:
        return df

    if coluna is None or coluna not in df.columns:
        return df.iloc[0:0].copy()

    serie_normalizada = (
        serie_texto(df, coluna)
        .map(normalizar_valor_filtro)
    )

    return df[
        serie_normalizada.isin(
            valores_normalizados
        )
    ].copy()


def aplicar_filtro_periodo(
    df: pd.DataFrame,
    coluna: Optional[str],
    data_inicial: Optional[date],
    data_final: Optional[date],
) -> pd.DataFrame:
    if (
        data_inicial is None
        and data_final is None
    ):
        return df

    if coluna is None or coluna not in df.columns:
        return df.iloc[0:0].copy()

    datas = serie_data(df, coluna)

    mascara = datas.notna()

    if data_inicial is not None:
        inicio = pd.Timestamp(data_inicial)

        mascara &= datas.ge(inicio)

    if data_final is not None:
        fim = (
            pd.Timestamp(data_final)
            + pd.Timedelta(days=1)
            - pd.Timedelta(microseconds=1)
        )

        mascara &= datas.le(fim)

    return df[mascara].copy()


def aplicar_filtros_dashboard(
    df: pd.DataFrame,
    filtros: DashboardFilters,
) -> pd.DataFrame:
    """
    Aplica os filtros sobre a base de auditoria.

    Os filtros são combinados com AND.
    Dentro de cada lista, os valores funcionam com OR.
    """
    base = normalizar_colunas(df)

    coluna_data = encontrar_coluna_logica(
        base,
        "data",
    )

    base = aplicar_filtro_periodo(
        base,
        coluna=coluna_data,
        data_inicial=filtros.data_inicial,
        data_final=filtros.data_final,
    )

    configuracoes = [
        (
            "grupo_cliente",
            filtros.grupos_clientes,
        ),
        (
            "cliente",
            filtros.clientes,
        ),
        (
            "unidade",
            filtros.unidades,
        ),
        (
            "codigo_ocorrencia",
            filtros.codigos_ocorrencia,
        ),
        (
            "status_debito",
            filtros.status_debito,
        ),
        (
            "regra_debito",
            filtros.regras_debito,
        ),
        (
            "produto",
            filtros.produtos,
        ),
    ]

    for nome_logico, valores in configuracoes:
        coluna = encontrar_coluna_logica(
            base,
            nome_logico,
        )

        base = aplicar_filtro_lista(
            base,
            coluna=coluna,
            valores=valores,
        )

    return base.reset_index(drop=True)


def filtros_estao_ativos(
    filtros: DashboardFilters,
) -> bool:
    if filtros.data_inicial is not None:
        return True

    if filtros.data_final is not None:
        return True

    listas = [
        filtros.grupos_clientes,
        filtros.clientes,
        filtros.unidades,
        filtros.codigos_ocorrencia,
        filtros.status_debito,
        filtros.regras_debito,
        filtros.produtos,
    ]

    return any(bool(valores) for valores in listas)


def gerar_opcoes_filtro(
    df: pd.DataFrame,
    nome_logico: str,
) -> List[dict]:
    """
    Gera opções para os selects do dashboard.

    Exemplo:
    [
        {
            "value": "GRU",
            "label": "GRU",
            "count": 25
        }
    ]
    """
    base = normalizar_colunas(df)

    coluna = encontrar_coluna_logica(
        base,
        nome_logico,
    )

    if coluna is None:
        return []

    serie = serie_texto(
        base,
        coluna,
    )

    serie = serie[
        serie.ne("")
        & serie.str.lower().ne("nan")
        & serie.str.lower().ne("none")
    ]

    if serie.empty:
        return []

    contagem = (
        serie
        .value_counts(dropna=False)
        .rename_axis("value")
        .reset_index(name="count")
    )

    contagem["label"] = contagem["value"]

    contagem = contagem.sort_values(
        by=[
            "count",
            "label",
        ],
        ascending=[
            False,
            True,
        ],
    )

    return (
        contagem[
            [
                "value",
                "label",
                "count",
            ]
        ]
        .to_dict(orient="records")
    )


def gerar_todas_opcoes_filtro(
    df: pd.DataFrame,
) -> dict:
    return {
        "grupos_clientes": gerar_opcoes_filtro(
            df,
            "grupo_cliente",
        ),

        "clientes": gerar_opcoes_filtro(
            df,
            "cliente",
        ),

        "unidades": gerar_opcoes_filtro(
            df,
            "unidade",
        ),

        "codigos_ocorrencia": gerar_opcoes_filtro(
            df,
            "codigo_ocorrencia",
        ),

        "status_debito": gerar_opcoes_filtro(
            df,
            "status_debito",
        ),

        "regras_debito": gerar_opcoes_filtro(
            df,
            "regra_debito",
        ),

        "produtos": gerar_opcoes_filtro(
            df,
            "produto",
        ),
    }