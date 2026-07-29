from pathlib import Path

from operations.incidentes.analytics import (
    gerar_analytics,
)

import pandas as pd


COLUNAS_PRODUTOS = [
    "CTRC",
    "SEQUENCIAL_CTRC",

    "CLIENTE",
    "CNPJ_CLIENTE",

    "UNIDADE_CTRC",

    "CODIGO_OCORRENCIA_RELATORIO",
    "DESCRICAO_OCORRENCIA_RELATORIO",

    "STATUS_DEBITO",
    "REGRA_DEBITO",

    "CHAVE_CTE",
    "NUMERO_CTE",
    "SERIE_CTE",
    "DATA_EMISSAO_CTE",

    "REMETENTE_NOME_CTE",
    "DESTINATARIO_NOME_CTE",

    "MUNICIPIO_ORIGEM_CTE",
    "UF_ORIGEM_CTE",
    "MUNICIPIO_DESTINO_CTE",
    "UF_DESTINO_CTE",

    "VALOR_CARGA_CTE",

    "PRODUTO_PREDOMINANTE",
    "SKU_PRODUTO_PREDOMINANTE",
    "DESCRICAO_PRODUTO_PREDOMINANTE",

    "QUANTIDADE_NFE_CTE",
    "CHAVES_NFE",

    "TIPO_DOCUMENTO_OUTRO",
    "DESCRICAO_DOCUMENTO_OUTRO",
    "NUMERO_DOCUMENTO_OUTRO",

    "STATUS_DOWNLOAD_XML",
    "STATUS_PARSER_XML",
]


def encontrar_coluna(
    df: pd.DataFrame,
    opcoes: list[str],
) -> str | None:
    mapa = {
        str(coluna).strip().upper(): coluna
        for coluna in df.columns
    }

    for opcao in opcoes:
        encontrada = mapa.get(
            opcao.strip().upper()
        )

        if encontrada:
            return encontrada

    return None


def serie_texto(
    df: pd.DataFrame,
    coluna: str | None,
) -> pd.Series:
    if coluna and coluna in df.columns:
        return (
            df[coluna]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    return pd.Series(
        [""] * len(df),
        index=df.index,
        dtype="object",
    )


def preparar_base_produtos(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = df.copy()

    coluna_ctrc = encontrar_coluna(
        base,
        [
            "CTRC",
            "CONHECIMENTO",
            "NRO_CTRC",
            "CTRC/NF",
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

    coluna_cnpj_cliente = encontrar_coluna(
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
            "UNIDADE_OCOR",
            "UNIDADE_OCORRENCIA",
            "UNIDADE_CTRC",
            "UNIDADE",
            "FILIAL",
        ],
    )

    coluna_ocorrencia = encontrar_coluna(
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

    aliases = {
        "CTRC": coluna_ctrc,
        "CLIENTE": coluna_cliente,
        "CNPJ_CLIENTE": coluna_cnpj_cliente,
        "UNIDADE_CTRC": coluna_unidade,

        "CODIGO_OCORRENCIA_RELATORIO": (
            coluna_ocorrencia
        ),

        "DESCRICAO_OCORRENCIA_RELATORIO": (
            coluna_descricao_ocorrencia
        ),

        "STATUS_DEBITO": (
            coluna_status_debito
        ),

        "REGRA_DEBITO": (
            coluna_regra_debito
        ),
    }

    for destino, origem in aliases.items():
        if destino not in base.columns:
            base[destino] = serie_texto(
                base,
                origem,
            )

    for coluna in COLUNAS_PRODUTOS:
        if coluna not in base.columns:
            base[coluna] = ""

    produtos = base[
        COLUNAS_PRODUTOS
    ].copy()

    produtos = produtos[
        produtos["STATUS_PARSER_XML"]
        .fillna("")
        .astype(str)
        .str.upper()
        .isin(["OK", "CACHE"])
    ].copy()

    produtos["PRODUTO_PREDOMINANTE"] = (
        produtos["PRODUTO_PREDOMINANTE"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    produtos["SKU_PRODUTO_PREDOMINANTE"] = (
        produtos["SKU_PRODUTO_PREDOMINANTE"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    produtos[
        "DESCRICAO_PRODUTO_PREDOMINANTE"
    ] = (
        produtos[
            "DESCRICAO_PRODUTO_PREDOMINANTE"
        ]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    return produtos.reset_index(
        drop=True
    )


def preparar_resumo(
    df: pd.DataFrame,
) -> pd.DataFrame:
    total_registros = len(df)

    coluna_ctrc = encontrar_coluna(
        df,
        [
            "CTRC",
            "CONHECIMENTO",
            "NRO_CTRC",
            "CTRC/NF",
        ],
    )

    coluna_cliente = encontrar_coluna(
        df,
        [
            "NOME_PAGADOR",
            "CLIENTE",
            "NOME_CLIENTE",
        ],
    )

    coluna_unidade = encontrar_coluna(
        df,
        [
            "UNID_OCOR",
            "UNIDADE_OCOR",
            "UNIDADE_OCORRENCIA",
            "UNIDADE_CTRC",
            "UNIDADE",
        ],
    )

    coluna_produto = encontrar_coluna(
        df,
        [
            "PRODUTO_PREDOMINANTE",
            "DESCRICAO_PRODUTO_PREDOMINANTE",
        ],
    )

    ctrcs_unicos = int(
        serie_texto(
            df,
            coluna_ctrc,
        )
        .replace("", pd.NA)
        .nunique()
    )

    clientes_distintos = int(
        serie_texto(
            df,
            coluna_cliente,
        )
        .replace("", pd.NA)
        .nunique()
    )

    unidades_distintas = int(
        serie_texto(
            df,
            coluna_unidade,
        )
        .replace("", pd.NA)
        .nunique()
    )

    produtos_distintos = int(
        serie_texto(
            df,
            coluna_produto,
        )
        .replace("", pd.NA)
        .nunique()
    )

    coluna_status_debito = encontrar_coluna(
        df,
        [
            "STATUS_VALIDACAO_DEBITO",
            "STATUS_DEBITO",
        ],
    )

    status_debito = (
        serie_texto(
            df,
            coluna_status_debito,
        )
        .str.upper()
    )

    xml_ok = int(
        df.get(
            "STATUS_PARSER_XML",
            pd.Series(dtype="object"),
        )
        .fillna("")
        .astype(str)
        .str.upper()
        .isin(["OK", "CACHE"])
        .sum()
    )

    xml_erro = int(
        df.get(
            "STATUS_PARSER_XML",
            pd.Series(dtype="object"),
        )
        .fillna("")
        .astype(str)
        .str.upper()
        .isin(["ERRO", "ERRO_CACHE"])
        .sum()
    )

    com_sku = int(
        df.get(
            "SKU_PRODUTO_PREDOMINANTE",
            pd.Series(dtype="object"),
        )
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )

    com_chave_nfe = int(
        df.get(
            "CHAVES_NFE",
            pd.Series(dtype="object"),
        )
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
        .sum()
    )

    debitos = int(
        status_debito
        .eq("DEBITO")
        .sum()
    )

    pendentes = int(
        status_debito
        .eq("PENDENTE")
        .sum()
    )

    nao_debitos = int(
        status_debito
        .eq("NAO_DEBITO")
        .sum()
    )

    sem_historico = int(
        status_debito
        .eq("SEM_HISTORICO")
        .sum()
    )

    resumo = [
        {
            "INDICADOR": "CTRCs únicos",
            "VALOR": ctrcs_unicos,
        },
        {
            "INDICADOR": "Clientes distintos",
            "VALOR": clientes_distintos,
        },
        {
            "INDICADOR": "Unidades distintas",
            "VALOR": unidades_distintas,
        },
        {
            "INDICADOR": "Produtos distintos",
            "VALOR": produtos_distintos,
        },
        {
            "INDICADOR": "Total de registros",
            "VALOR": total_registros,
        },
        {
            "INDICADOR": "XML processado com sucesso",
            "VALOR": xml_ok,
        },
        {
            "INDICADOR": "XML com erro",
            "VALOR": xml_erro,
        },
        {
            "INDICADOR": "Registros com SKU identificado",
            "VALOR": com_sku,
        },
        {
            "INDICADOR": "Registros com chave de NF-e",
            "VALOR": com_chave_nfe,
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
    ]

    return pd.DataFrame(
        resumo
    )


def preparar_ranking_produtos(
    produtos: pd.DataFrame,
) -> pd.DataFrame:
    base = produtos.copy()

    base["CTRC"] = (
        base["CTRC"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    base = base.drop_duplicates(
        subset=[
            "CTRC",
            "CHAVE_CTE",
            "PRODUTO_PREDOMINANTE",
        ],
        keep="first",
    ).copy()

    base["PRODUTO_RANKING"] = (
        base["DESCRICAO_PRODUTO_PREDOMINANTE"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    sem_descricao = (
        base["PRODUTO_RANKING"] == ""
    )

    base.loc[
        sem_descricao,
        "PRODUTO_RANKING",
    ] = (
        base.loc[
            sem_descricao,
            "PRODUTO_PREDOMINANTE",
        ]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    base = base[
        base["PRODUTO_RANKING"] != ""
    ].copy()

    if base.empty:
        return pd.DataFrame(
            columns=[
                "SKU",
                "PRODUTO",
                "QUANTIDADE_OCORRENCIAS",
                "VALOR_TOTAL_CARGA",
            ]
        )

    base["VALOR_CARGA_CTE"] = pd.to_numeric(
        base["VALOR_CARGA_CTE"],
        errors="coerce",
    ).fillna(0)

    ranking = (
        base.groupby(
            [
                "SKU_PRODUTO_PREDOMINANTE",
                "PRODUTO_RANKING",
            ],
            dropna=False,
        )
        .agg(
            QUANTIDADE_OCORRENCIAS=(
                "CTRC",
                "count",
            ),
            VALOR_TOTAL_CARGA=(
                "VALOR_CARGA_CTE",
                "sum",
            ),
        )
        .reset_index()
        .rename(
            columns={
                "SKU_PRODUTO_PREDOMINANTE": (
                    "SKU"
                ),
                "PRODUTO_RANKING": (
                    "PRODUTO"
                ),
            }
        )
        .sort_values(
            [
                "QUANTIDADE_OCORRENCIAS",
                "VALOR_TOTAL_CARGA",
            ],
            ascending=[
                False,
                False,
            ],
        )
        .reset_index(
            drop=True
        )
    )

    return ranking


def ajustar_planilha(
    writer: pd.ExcelWriter,
    nome_aba: str,
    formato_moeda: bool = False,
) -> None:
    worksheet = writer.sheets[
        nome_aba
    ]

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = (
        worksheet.dimensions
    )

    for coluna in worksheet.columns:
        valores = [
            str(celula.value or "")
            for celula in coluna
        ]

        largura = min(
            max(
                len(valor)
                for valor in valores
            ) + 2,
            60,
        )

        letra = coluna[0].column_letter

        worksheet.column_dimensions[
            letra
        ].width = max(
            largura,
            12,
        )

    if formato_moeda:
        for linha in worksheet.iter_rows(
            min_row=2,
        ):
            for celula in linha:
                cabecalho = worksheet.cell(
                    row=1,
                    column=celula.column,
                ).value

                if cabecalho in {
                    "VALOR_CARGA_CTE",
                    "VALOR_TOTAL_CARGA",
                    "VALOR_CARGA",
                    "VALOR_MEDIO_CTRC",
                }:
                    celula.number_format = (
                        'R$ #,##0.00'
                    )

                elif cabecalho in {
                    "PERCENTUAL",
                    "PERCENTUAL_ACUMULADO",
                    "PERCENTUAL_VALOR",
                }:
                    celula.number_format = (
                        '0.00"%"'
                    )


def exportar_incidentes(
    df: pd.DataFrame,
    caminho_saida: Path | str,
) -> Path:
    caminho = Path(
        caminho_saida
    )

    caminho.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    auditoria = df.copy()

    produtos = preparar_base_produtos(
        auditoria
    )

    resumo = preparar_resumo(
        auditoria
    )

    ranking = preparar_ranking_produtos(
        produtos
    )

    analytics = gerar_analytics(
        auditoria
    )

    with pd.ExcelWriter(
        caminho,
        engine="openpyxl",
    ) as writer:
        auditoria.to_excel(
            writer,
            sheet_name="AUDITORIA",
            index=False,
        )

        produtos.to_excel(
            writer,
            sheet_name="PRODUTOS",
            index=False,
        )

        resumo.to_excel(
            writer,
            sheet_name="RESUMO",
            index=False,
        )

        ranking.to_excel(
            writer,
            sheet_name="RANKING_PRODUTOS",
            index=False,
        )

        for nome_aba, base_analytics in analytics.items():
            # Evita gravar RANKING_PRODUTOS duas vezes.
            if nome_aba == "RANKING_PRODUTOS":
                continue

            base_analytics.to_excel(
                writer,
                sheet_name=nome_aba[:31],
                index=False,
            )

        for nome_aba in analytics:
            if nome_aba == "RANKING_PRODUTOS":
                continue

            ajustar_planilha(
                writer,
                nome_aba[:31],
                formato_moeda=True,
            )

        ajustar_planilha(
            writer,
            "AUDITORIA",
            formato_moeda=True,
        )

        ajustar_planilha(
            writer,
            "PRODUTOS",
            formato_moeda=True,
        )

        ajustar_planilha(
            writer,
            "RESUMO",
        )

        ajustar_planilha(
            writer,
            "RANKING_PRODUTOS",
            formato_moeda=True,
        )

    return caminho