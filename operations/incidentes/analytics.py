from __future__ import annotations

from typing import Optional

import pandas as pd


# =========================================================
# Utilitários
# =========================================================

DESCRICOES_REGRAS_DEBITO = {
    "CODIGO_CONFIRMADO": {
        "descricao": (
            "Código de ocorrência validado conforme "
            "a parametrização de débito."
        ),
        "acao": (
            "Prosseguir com o tratamento do débito."
        ),
    },

    "CODIGO_NAO_PARAMETRIZADO": {
        "descricao": (
            "Código de ocorrência ainda não possui "
            "regra de débito cadastrada."
        ),
        "acao": (
            "Revisar o código e definir a parametrização."
        ),
    },

    "OCORRENCIA_DIVERGENTE": {
        "descricao": (
            "A ocorrência informada na OP930 diverge "
            "do histórico encontrado na OP101."
        ),
        "acao": (
            "Conferir o histórico do CTRC e validar "
            "manualmente a ocorrência correta."
        ),
    },

    "SEM_HISTORICO": {
        "descricao": (
            "Não foi encontrado histórico suficiente "
            "na OP101 para validar o débito."
        ),
        "acao": (
            "Realizar análise manual do CTRC."
        ),
    },

    "NAO_DEBITO": {
        "descricao": (
            "A ocorrência analisada não atende às "
            "regras definidas para débito."
        ),
        "acao": (
            "Não gerar débito e registrar a conclusão."
        ),
    },

    "DEBITO_CONFIRMADO": {
        "descricao": (
            "O débito foi confirmado pelas regras "
            "de validação automática."
        ),
        "acao": (
            "Encaminhar para o processo de cobrança."
        ),
    },

    "PENDENTE_VALIDACAO": {
        "descricao": (
            "O registro depende de validação adicional "
            "antes da decisão final."
        ),
        "acao": (
            "Revisar os dados e concluir a validação."
        ),
    },
}

def encontrar_coluna(
    df: pd.DataFrame,
    candidatos: list[str],
) -> Optional[str]:
    """
    Retorna o primeiro nome de coluna encontrado.
    A comparação também considera diferenças entre
    maiúsculas e minúsculas.
    """
    mapa_colunas = {
        str(coluna).strip().upper(): coluna
        for coluna in df.columns
    }

    for candidato in candidatos:
        chave = str(candidato).strip().upper()

        if chave in mapa_colunas:
            return mapa_colunas[chave]

    return None


def serie_texto(
    df: pd.DataFrame,
    coluna: Optional[str],
) -> pd.Series:
    """
    Retorna uma série textual segura.
    """
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


def serie_numerica(
    df: pd.DataFrame,
    coluna: Optional[str],
) -> pd.Series:
    """
    Converte valores numéricos de forma segura.

    Aceita:
    - valores numéricos nativos;
    - 3898.90;
    - 3898,90;
    - 3.898,90;
    - R$ 3.898,90.
    """
    if coluna is None or coluna not in df.columns:
        return pd.Series(
            0.0,
            index=df.index,
            dtype="float64",
        )

    serie = df[coluna]

    if pd.api.types.is_numeric_dtype(serie):
        return (
            pd.to_numeric(
                serie,
                errors="coerce",
            )
            .fillna(0.0)
            .astype(float)
        )

    def converter_valor(valor) -> float:
        if pd.isna(valor):
            return 0.0

        if isinstance(
            valor,
            (int, float),
        ):
            return float(valor)

        texto = (
            str(valor)
            .strip()
            .replace("R$", "")
            .replace(" ", "")
        )

        if texto == "":
            return 0.0

        possui_ponto = "." in texto
        possui_virgula = "," in texto

        try:
            # Formato brasileiro: 3.898,90
            if possui_ponto and possui_virgula:
                texto = (
                    texto
                    .replace(".", "")
                    .replace(",", ".")
                )

            # Formato brasileiro sem milhar: 3898,90
            elif possui_virgula:
                texto = texto.replace(",", ".")

            # Formato decimal internacional: 3898.90
            # Mantém o ponto como separador decimal.

            return float(texto)

        except (
            TypeError,
            ValueError,
        ):
            return 0.0

    return (
        serie
        .apply(converter_valor)
        .astype(float)
    )


def serie_data(
    df: pd.DataFrame,
    coluna: Optional[str],
) -> pd.Series:
    """
    Retorna uma série datetime segura.
    """
    if coluna is None or coluna not in df.columns:
        return pd.Series(
            pd.NaT,
            index=df.index,
            dtype="datetime64[ns]",
        )

    return pd.to_datetime(
        df[coluna],
        errors="coerce",
        dayfirst=True,
    )


def percentual(
    quantidade: pd.Series,
) -> pd.Series:
    """
    Calcula o percentual de cada item sobre o total.
    """
    total = quantidade.sum()

    if total == 0:
        return pd.Series(
            0.0,
            index=quantidade.index,
        )

    return (
        quantidade
        .div(total)
        .mul(100)
        .round(2)
    )


def percentual_acumulado(
    quantidade: pd.Series,
) -> pd.Series:
    """
    Calcula percentual acumulado para análises de Pareto.
    """
    total = quantidade.sum()

    if total == 0:
        return pd.Series(
            0.0,
            index=quantidade.index,
        )

    return (
        quantidade
        .cumsum()
        .div(total)
        .mul(100)
        .round(2)
    )


# =========================================================
# Preparação da base
# =========================================================

def preparar_base_analitica(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Padroniza os principais campos usados pelas análises.
    Não modifica o dataframe original.
    """
    base = df.copy()

    coluna_ctrc = encontrar_coluna(
        base,
        [
            "CTRC",
            "CTE",
            "NUMERO_CTRC",
            "NUMERO_CTE",
        ],
    )

    coluna_cliente = encontrar_coluna(
        base,
        [
            "NOME_PAGADOR",
            "CLIENTE",
            "NOME_CLIENTE",
            "CLIENTE_NOME",
        ],
    )

    coluna_grupo_cliente = encontrar_coluna(
        base,
        [
            "GRUPO_CLIENTE",
            "NOME_GRUPO_CLIENTE",
            "GRUPO_ECONOMICO",
            "CLIENTE_GRUPO",
        ],
    )

    coluna_cnpj = encontrar_coluna(
        base,
        [
            "CNPJ_PAGADOR",
            "CNPJ_CLIENTE",
            "CLIENTE_CNPJ",
            "CNPJ",
        ],
    )

    coluna_unidade = encontrar_coluna(
        base,
        [
            "UNID_OCOR",
            "UNIDADE_OCORRENCIA",
            "UNIDADE_CTRC",
            "UNIDADE",
            "FILIAL",
            "SIGLA_UNIDADE",
        ],
    )

    coluna_codigo_ocorrencia = encontrar_coluna(
        base,
        [
            "COD_OCOR",
            "CODIGO_OCORRENCIA_ANALITICO",
            "CODIGO_OCORRENCIA_RELATORIO",
            "COD_OCORRENCIA",
            "OCORRENCIA",
        ],
    )

    coluna_descricao_ocorrencia = encontrar_coluna(
        base,
        [
            "DESCRICAO_OCOR",
            "DESCRICAO_OCORRENCIA_ANALITICA",
            "DESCRICAO_OCORRENCIA_RELATORIO",
            "DESC_OCORRENCIA",
            "DESCRICAO_OCORRENCIA",
        ],
    )

    coluna_status_debito = encontrar_coluna(
        base,
        [
            "STATUS_VALIDACAO_DEBITO",
            "STATUS_DEBITO",
        ],
    )

    coluna_regra_debito = encontrar_coluna(
        base,
        [
            "REGRA_VALIDACAO_DEBITO",
            "REGRA_DEBITO",
        ],
    )

    coluna_produto = encontrar_coluna(
        base,
        [
            "PRODUTO_PREDOMINANTE",
            "DESCRICAO_PRODUTO_PREDOMINANTE",
            "PRODUTO",
        ],
    )

    coluna_sku = encontrar_coluna(
        base,
        [
            "SKU_PRODUTO_PREDOMINANTE",
            "SKU",
            "CODIGO_PRODUTO",
        ],
    )

    coluna_valor_carga = encontrar_coluna(
        base,
        [
            "VALOR_CARGA_CTE",
            "VALOR_CARGA",
            "VALOR_MERCADORIA",
            "VALOR_NOTA",
            "VALOR_NF",
        ],
    )

    coluna_data = encontrar_coluna(
        base,
        [
            "DATA_EMISSAO",
            "DATA_EMISSAO_CTE",
            "DATA_OCORRENCIA",
            "DATA",
        ],
    )

    coluna_status_parser = encontrar_coluna(
        base,
        [
            "STATUS_PARSER_XML",
        ],
    )

    coluna_tipo_operacao = encontrar_coluna(
        base,
        [
            "TIPO_OPERACAO",
            "OPERACAO",
            "TIPO_DE_OPERACAO",
        ],
    )

    aliases_texto = {
        "AN_CTRC": coluna_ctrc,
        "AN_CLIENTE": coluna_cliente,
        "AN_GRUPO_CLIENTE": coluna_grupo_cliente,
        "AN_CNPJ_CLIENTE": coluna_cnpj,
        "AN_UNIDADE": coluna_unidade,
        "AN_CODIGO_OCORRENCIA": coluna_codigo_ocorrencia,
        "AN_DESCRICAO_OCORRENCIA": coluna_descricao_ocorrencia,
        "AN_STATUS_DEBITO": coluna_status_debito,
        "AN_REGRA_DEBITO": coluna_regra_debito,
        "AN_PRODUTO": coluna_produto,
        "AN_SKU": coluna_sku,
        "AN_STATUS_PARSER": coluna_status_parser,
        "AN_TIPO_OPERACAO": coluna_tipo_operacao,
    }

    for destino, origem in aliases_texto.items():
        base[destino] = serie_texto(
            base,
            origem,
        )

    base["AN_STATUS_DEBITO"] = (
        base["AN_STATUS_DEBITO"]
        .str.upper()
    )

    base["AN_REGRA_DEBITO"] = (
        base["AN_REGRA_DEBITO"]
        .str.upper()
    )

    base["AN_STATUS_PARSER"] = (
        base["AN_STATUS_PARSER"]
        .str.upper()
    )

    base["AN_TIPO_OPERACAO"] = (
        base["AN_TIPO_OPERACAO"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace(
            {
                "": "NAO_IDENTIFICADO",
                "NAN": "NAO_IDENTIFICADO",
                "NONE": "NAO_IDENTIFICADO",
            }
        )
    )

    base["AN_VALOR_CARGA"] = serie_numerica(
        base,
        coluna_valor_carga,
    )

    base["AN_DATA"] = serie_data(
        base,
        coluna_data,
    )

    base["AN_ANO"] = (
        base["AN_DATA"]
        .dt.year
        .astype("Int64")
    )

    base["AN_MES_NUMERO"] = (
        base["AN_DATA"]
        .dt.month
        .astype("Int64")
    )

    base["AN_MES"] = (
        base["AN_DATA"]
        .dt.to_period("M")
        .astype(str)
    )

    base["AN_DIA"] = (
        base["AN_DATA"]
        .dt.strftime("%Y-%m-%d")
    )

    # Chave auxiliar para eliminar duplicidades analíticas.
    base["AN_CHAVE_UNICA"] = (
        base["AN_CTRC"]
        .str.upper()
        + "|"
        + base["AN_PRODUTO"]
        .str.upper()
    )

    return base


def remover_duplicidades_analiticas(
    base: pd.DataFrame,
) -> pd.DataFrame:
    """
    Remove repetição do mesmo CTRC/produto.
    A aba AUDITORIA continua preservando todas as linhas.
    """
    resultado = base.copy()

    possui_ctrc = (
        resultado["AN_CTRC"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    )

    com_ctrc = resultado[
        possui_ctrc
    ].drop_duplicates(
        subset=[
            "AN_CTRC",
            "AN_PRODUTO",
        ],
        keep="first",
    )

    sem_ctrc = resultado[
        ~possui_ctrc
    ]

    return pd.concat(
        [
            com_ctrc,
            sem_ctrc,
        ],
        ignore_index=True,
    )


# =========================================================
# Indicadores gerais
# =========================================================

def gerar_indicadores(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = preparar_base_analitica(df)
    base_unica = remover_duplicidades_analiticas(base)

    total_linhas = len(base)

    total_ctrcs = int(
        base_unica["AN_CTRC"]
        .replace("", pd.NA)
        .nunique()
    )

    total_clientes = int(
        base_unica["AN_CLIENTE"]
        .replace("", pd.NA)
        .nunique()
    )

    total_unidades = int(
        base_unica["AN_UNIDADE"]
        .replace("", pd.NA)
        .nunique()
    )

    total_produtos = int(
        base_unica["AN_PRODUTO"]
        .replace("", pd.NA)
        .nunique()
    )

    valor_total_carga = float(
        base_unica["AN_VALOR_CARGA"]
        .sum()
    )

    debitos = int(
        base_unica["AN_STATUS_DEBITO"]
        .eq("DEBITO")
        .sum()
    )

    pendentes = int(
        base_unica["AN_STATUS_DEBITO"]
        .eq("PENDENTE")
        .sum()
    )

    nao_debitos = int(
        base_unica["AN_STATUS_DEBITO"]
        .eq("NAO_DEBITO")
        .sum()
    )

    sem_historico = int(
        base_unica["AN_STATUS_DEBITO"]
        .eq("SEM_HISTORICO")
        .sum()
    )

    xml_ok = int(
        base["AN_STATUS_PARSER"]
        .isin(["OK", "CACHE"])
        .sum()
    )

    status_parser = (
        base["AN_STATUS_PARSER"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    xml_erro = int(
        (
            status_parser.ne("")
            & ~status_parser.isin(["OK", "CACHE"])
        )
        .sum()
    )

    dados = [
        {
            "INDICADOR": "Total de linhas da auditoria",
            "VALOR": total_linhas,
        },
        {
            "INDICADOR": "CTRCs únicos",
            "VALOR": total_ctrcs,
        },
        {
            "INDICADOR": "Clientes distintos",
            "VALOR": total_clientes,
        },
        {
            "INDICADOR": "Unidades distintas",
            "VALOR": total_unidades,
        },
        {
            "INDICADOR": "Produtos distintos",
            "VALOR": total_produtos,
        },
        {
            "INDICADOR": "Valor total da carga",
            "VALOR": valor_total_carga,
        },
        {
            "INDICADOR": "Débitos confirmados",
            "VALOR": debitos,
        },
        {
            "INDICADOR": "Débitos pendentes",
            "VALOR": pendentes,
        },
        {
            "INDICADOR": "Não débitos",
            "VALOR": nao_debitos,
        },
        {
            "INDICADOR": "Sem histórico",
            "VALOR": sem_historico,
        },
        {
            "INDICADOR": "XMLs processados",
            "VALOR": xml_ok,
        },
        {
            "INDICADOR": "Erros de XML",
            "VALOR": xml_erro,
        },
    ]

    return pd.DataFrame(dados)


# =========================================================
# Rankings
# =========================================================

def _ranking_generico(
    base: pd.DataFrame,
    coluna_grupo: str,
    nome_coluna_saida: str,
) -> pd.DataFrame:
    filtrada = base[
        base[coluna_grupo]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    ].copy()

    if filtrada.empty:
        return pd.DataFrame(
            columns=[
                nome_coluna_saida,
                "QUANTIDADE",
                "PERCENTUAL",
                "PERCENTUAL_ACUMULADO",
                "VALOR_CARGA",
            ]
        )

    ranking = (
        filtrada
        .groupby(
            coluna_grupo,
            dropna=False,
        )
        .agg(
            QUANTIDADE=(
                "AN_CTRC",
                "size",
            ),
            VALOR_CARGA=(
                "AN_VALOR_CARGA",
                "sum",
            ),
        )
        .reset_index()
        .rename(
            columns={
                coluna_grupo: nome_coluna_saida,
            }
        )
        .sort_values(
            by=[
                "QUANTIDADE",
                "VALOR_CARGA",
            ],
            ascending=[
                False,
                False,
            ],
        )
        .reset_index(drop=True)
    )

    ranking["PERCENTUAL"] = percentual(
        ranking["QUANTIDADE"]
    )

    ranking["PERCENTUAL_ACUMULADO"] = (
        percentual_acumulado(
            ranking["QUANTIDADE"]
        )
    )

    return ranking[
        [
            nome_coluna_saida,
            "QUANTIDADE",
            "PERCENTUAL",
            "PERCENTUAL_ACUMULADO",
            "VALOR_CARGA",
        ]
    ]


def gerar_ranking_clientes(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    return _ranking_generico(
        base,
        coluna_grupo="AN_CLIENTE",
        nome_coluna_saida="CLIENTE",
    )

def gerar_ranking_grupos_clientes(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    return _ranking_generico(
        base,
        coluna_grupo="AN_GRUPO_CLIENTE",
        nome_coluna_saida="GRUPO_CLIENTE",
    )


def gerar_ranking_unidades(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    return _ranking_generico(
        base,
        coluna_grupo="AN_UNIDADE",
        nome_coluna_saida="UNIDADE",
    )


def gerar_ranking_ocorrencias(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    base["AN_OCORRENCIA_COMPLETA"] = (
        base["AN_CODIGO_OCORRENCIA"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    descricao = (
        base["AN_DESCRICAO_OCORRENCIA"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    possui_codigo = (
        base["AN_OCORRENCIA_COMPLETA"]
        .ne("")
    )

    possui_descricao = descricao.ne("")

    base.loc[
        possui_codigo & possui_descricao,
        "AN_OCORRENCIA_COMPLETA",
    ] = (
        base.loc[
            possui_codigo & possui_descricao,
            "AN_OCORRENCIA_COMPLETA",
        ]
        + " - "
        + descricao[
            possui_codigo & possui_descricao
        ]
    )

    base.loc[
        ~possui_codigo & possui_descricao,
        "AN_OCORRENCIA_COMPLETA",
    ] = descricao[
        ~possui_codigo & possui_descricao
    ]

    return _ranking_generico(
        base,
        coluna_grupo="AN_OCORRENCIA_COMPLETA",
        nome_coluna_saida="OCORRENCIA",
    )


def gerar_ranking_produtos(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    filtrada = base[
        base["AN_PRODUTO"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    ].copy()

    if filtrada.empty:
        return pd.DataFrame(
            columns=[
                "SKU",
                "PRODUTO",
                "QUANTIDADE",
                "PERCENTUAL",
                "PERCENTUAL_ACUMULADO",
                "VALOR_CARGA",
                "PERCENTUAL_VALOR",
                "VALOR_MEDIO_CTRC",
            ]
        )

    ranking = (
        filtrada
        .groupby(
            [
                "AN_SKU",
                "AN_PRODUTO",
            ],
            dropna=False,
        )
        .agg(
            QUANTIDADE=(
                "AN_CTRC",
                "size",
            ),
            VALOR_CARGA=(
                "AN_VALOR_CARGA",
                "sum",
            ),
        )
        .reset_index()
        .rename(
            columns={
                "AN_SKU": "SKU",
                "AN_PRODUTO": "PRODUTO",
            }
        )
        .sort_values(
            by=[
                "QUANTIDADE",
                "VALOR_CARGA",
            ],
            ascending=[
                False,
                False,
            ],
        )
        .reset_index(drop=True)
    )

    ranking["PERCENTUAL"] = percentual(
        ranking["QUANTIDADE"]
    )

    ranking["PERCENTUAL_ACUMULADO"] = (
        percentual_acumulado(
            ranking["QUANTIDADE"]
        )
    )

    valor_total = float(
        ranking["VALOR_CARGA"]
        .sum()
    )

    if valor_total > 0:
        ranking["PERCENTUAL_VALOR"] = (
            ranking["VALOR_CARGA"]
            .div(valor_total)
            .mul(100)
            .round(2)
        )
    else:
        ranking["PERCENTUAL_VALOR"] = 0.0

    ranking["VALOR_MEDIO_CTRC"] = (
        ranking["VALOR_CARGA"]
        .div(
            ranking["QUANTIDADE"]
            .replace(0, pd.NA)
        )
        .fillna(0.0)
        .round(2)
    )

    return ranking[
        [
            "SKU",
            "PRODUTO",
            "QUANTIDADE",
            "PERCENTUAL",
            "PERCENTUAL_ACUMULADO",
            "VALOR_CARGA",
            "PERCENTUAL_VALOR",
            "VALOR_MEDIO_CTRC",
        ]
    ]


# =========================================================
# Status e regras
# =========================================================

def gerar_status_debitos(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    base = base[
        base["AN_STATUS_DEBITO"]
        .ne("")
    ].copy()

    if base.empty:
        return pd.DataFrame(
            columns=[
                "STATUS_DEBITO",
                "QUANTIDADE",
                "PERCENTUAL",
                "VALOR_CARGA",
            ]
        )

    resultado = (
        base
        .groupby(
            "AN_STATUS_DEBITO",
            dropna=False,
        )
        .agg(
            QUANTIDADE=(
                "AN_CTRC",
                "size",
            ),
            VALOR_CARGA=(
                "AN_VALOR_CARGA",
                "sum",
            ),
        )
        .reset_index()
        .rename(
            columns={
                "AN_STATUS_DEBITO": (
                    "STATUS_DEBITO"
                ),
            }
        )
        .sort_values(
            "QUANTIDADE",
            ascending=False,
        )
        .reset_index(drop=True)
    )

    resultado["PERCENTUAL"] = percentual(
        resultado["QUANTIDADE"]
    )

    return resultado[
        [
            "STATUS_DEBITO",
            "QUANTIDADE",
            "PERCENTUAL",
            "VALOR_CARGA",
        ]
    ]

def gerar_tipos_operacao(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = preparar_base_analitica(df)

    base = base[
        base["AN_CTRC"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    ].copy()

    base = base[
        base["AN_TIPO_OPERACAO"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    ].copy()

    if base.empty:
        return pd.DataFrame(
            columns=[
                "TIPO_OPERACAO",
                "QUANTIDADE",
                "PERCENTUAL",
            ]
        )

    # Para este indicador, cada CTRC deve ser
    # contabilizado somente uma vez.
    base = base.drop_duplicates(
        subset=["AN_CTRC"],
        keep="first",
    )

    resultado = (
        base
        .groupby(
            "AN_TIPO_OPERACAO",
            dropna=False,
        )
        .agg(
            QUANTIDADE=(
                "AN_CTRC",
                "nunique",
            ),
        )
        .reset_index()
        .rename(
            columns={
                "AN_TIPO_OPERACAO":
                    "TIPO_OPERACAO",
            }
        )
        .sort_values(
            "QUANTIDADE",
            ascending=False,
        )
        .reset_index(drop=True)
    )

    resultado["PERCENTUAL"] = percentual(
        resultado["QUANTIDADE"]
    )

    return resultado[
        [
            "TIPO_OPERACAO",
            "QUANTIDADE",
            "PERCENTUAL",
        ]
    ]

def gerar_regras_debito(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    base = base[
        base["AN_REGRA_DEBITO"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    ].copy()

    resultado = _ranking_generico(
        base,
        coluna_grupo="AN_REGRA_DEBITO",
        nome_coluna_saida="REGRA_DEBITO",
    )

    if resultado.empty:
        return pd.DataFrame(
            columns=[
                "REGRA_DEBITO",
                "DESCRICAO",
                "ACAO",
                "QUANTIDADE",
                "PERCENTUAL",
                "PERCENTUAL_ACUMULADO",
                "VALOR_CARGA",
            ]
        )

    resultado["REGRA_DEBITO"] = (
        resultado["REGRA_DEBITO"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    resultado["DESCRICAO"] = (
        resultado["REGRA_DEBITO"]
        .map(
            lambda regra: (
                DESCRICOES_REGRAS_DEBITO
                .get(
                    regra,
                    {
                        "descricao": (
                            "Regra de validação ainda "
                            "não documentada."
                        ),
                    },
                )
                .get("descricao", "")
            )
        )
    )

    resultado["ACAO"] = (
        resultado["REGRA_DEBITO"]
        .map(
            lambda regra: (
                DESCRICOES_REGRAS_DEBITO
                .get(
                    regra,
                    {
                        "acao": (
                            "Revisar a regra e definir "
                            "a ação operacional."
                        ),
                    },
                )
                .get("acao", "")
            )
        )
    )

    return resultado[
        [
            "REGRA_DEBITO",
            "DESCRICAO",
            "ACAO",
            "QUANTIDADE",
            "PERCENTUAL",
            "PERCENTUAL_ACUMULADO",
            "VALOR_CARGA",
        ]
    ]


# =========================================================
# Evolução temporal
# =========================================================

def gerar_evolucao_mensal(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    base = base[
        base["AN_DATA"]
        .notna()
    ].copy()

    if base.empty:
        return pd.DataFrame(
            columns=[
                "MES",
                "QUANTIDADE",
                "DEBITOS",
                "PENDENTES",
                "VALOR_CARGA",
            ]
        )

    base["FLAG_DEBITO"] = (
        base["AN_STATUS_DEBITO"]
        .eq("DEBITO")
        .astype(int)
    )

    base["FLAG_PENDENTE"] = (
        base["AN_STATUS_DEBITO"]
        .eq("PENDENTE")
        .astype(int)
    )

    resultado = (
        base
        .groupby(
            "AN_MES",
            dropna=False,
        )
        .agg(
            QUANTIDADE=(
                "AN_CTRC",
                "size",
            ),
            DEBITOS=(
                "FLAG_DEBITO",
                "sum",
            ),
            PENDENTES=(
                "FLAG_PENDENTE",
                "sum",
            ),
            VALOR_CARGA=(
                "AN_VALOR_CARGA",
                "sum",
            ),
        )
        .reset_index()
        .rename(
            columns={
                "AN_MES": "MES",
            }
        )
        .sort_values(
            "MES",
            ascending=True,
        )
        .reset_index(drop=True)
    )

    return resultado


def gerar_evolucao_diaria(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = remover_duplicidades_analiticas(
        preparar_base_analitica(df)
    )

    base = base[
        base["AN_DATA"]
        .notna()
    ].copy()

    if base.empty:
        return pd.DataFrame(
            columns=[
                "DATA",
                "QUANTIDADE",
                "DEBITOS",
                "PENDENTES",
                "VALOR_CARGA",
            ]
        )

    base["FLAG_DEBITO"] = (
        base["AN_STATUS_DEBITO"]
        .eq("DEBITO")
        .astype(int)
    )

    base["FLAG_PENDENTE"] = (
        base["AN_STATUS_DEBITO"]
        .eq("PENDENTE")
        .astype(int)
    )

    resultado = (
        base
        .groupby(
            "AN_DIA",
            dropna=False,
        )
        .agg(
            QUANTIDADE=(
                "AN_CTRC",
                "size",
            ),
            DEBITOS=(
                "FLAG_DEBITO",
                "sum",
            ),
            PENDENTES=(
                "FLAG_PENDENTE",
                "sum",
            ),
            VALOR_CARGA=(
                "AN_VALOR_CARGA",
                "sum",
            ),
        )
        .reset_index()
        .rename(
            columns={
                "AN_DIA": "DATA",
            }
        )
        .sort_values(
            "DATA",
            ascending=True,
        )
        .reset_index(drop=True)
    )

    return resultado


# =========================================================
# Pacote completo
# =========================================================

def gerar_analytics(
    df: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """
    Retorna todas as bases analíticas prontas para exportação.
    """
    return {
        "INDICADORES": gerar_indicadores(df),
        "RANKING_CLIENTES": gerar_ranking_clientes(df),
        "RANKING_GRUPOS_CLIENTES": (gerar_ranking_grupos_clientes(df)),
        "RANKING_UNIDADES": gerar_ranking_unidades(df),
        "RANKING_OCORRENCIAS": gerar_ranking_ocorrencias(df),
        "RANKING_PRODUTOS": gerar_ranking_produtos(df),
        "STATUS_DEBITOS": gerar_status_debitos(df),
        "REGRAS_DEBITO": gerar_regras_debito(df),
        "EVOLUCAO_MENSAL": gerar_evolucao_mensal(df),
        "EVOLUCAO_DIARIA": gerar_evolucao_diaria(df),
    }