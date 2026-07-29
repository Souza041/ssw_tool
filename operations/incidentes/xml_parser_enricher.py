from pathlib import Path
from typing import Any

import pandas as pd

from operations.incidentes.danfe_parser import CTeXMLParser

from operations.incidentes.enrich import (encontrar_coluna_ctrc,)


COLUNAS_XML_CTE = [
    "STATUS_PARSER_XML",
    "ERRO_PARSER_XML",
    "ARQUIVO_XML_CTE",

    "CHAVE_CTE",
    "NUMERO_CTE",
    "SERIE_CTE",
    "DATA_EMISSAO_CTE",

    "MUNICIPIO_ORIGEM_CTE",
    "UF_ORIGEM_CTE",
    "MUNICIPIO_DESTINO_CTE",
    "UF_DESTINO_CTE",

    "EMITENTE_CNPJ_CTE",
    "EMITENTE_NOME_CTE",

    "REMETENTE_CNPJ_CTE",
    "REMETENTE_NOME_CTE",

    "DESTINATARIO_CNPJ_CTE",
    "DESTINATARIO_NOME_CTE",

    "EXPEDIDOR_CNPJ_CTE",
    "EXPEDIDOR_NOME_CTE",

    "RECEBEDOR_CNPJ_CTE",
    "RECEBEDOR_NOME_CTE",

    "VALOR_CARGA_CTE",

    "PRODUTO_PREDOMINANTE",
    "SKU_PRODUTO_PREDOMINANTE",
    "DESCRICAO_PRODUTO_PREDOMINANTE",

    "QUANTIDADE_NFE_CTE",
    "CHAVES_NFE",

    "TIPO_DOCUMENTO_OUTRO",
    "DESCRICAO_DOCUMENTO_OUTRO",
    "NUMERO_DOCUMENTO_OUTRO",
]


MAPEAMENTO_XML_CTE = {
    "arquivo_xml_cte": "ARQUIVO_XML_CTE",

    "chave_cte": "CHAVE_CTE",
    "numero_cte": "NUMERO_CTE",
    "serie_cte": "SERIE_CTE",
    "data_emissao_cte": "DATA_EMISSAO_CTE",

    "municipio_origem": "MUNICIPIO_ORIGEM_CTE",
    "uf_origem": "UF_ORIGEM_CTE",
    "municipio_destino": "MUNICIPIO_DESTINO_CTE",
    "uf_destino": "UF_DESTINO_CTE",

    "emitente_cnpj": "EMITENTE_CNPJ_CTE",
    "emitente_nome": "EMITENTE_NOME_CTE",

    "remetente_cnpj": "REMETENTE_CNPJ_CTE",
    "remetente_nome": "REMETENTE_NOME_CTE",

    "destinatario_cnpj": "DESTINATARIO_CNPJ_CTE",
    "destinatario_nome": "DESTINATARIO_NOME_CTE",

    "expedidor_cnpj": "EXPEDIDOR_CNPJ_CTE",
    "expedidor_nome": "EXPEDIDOR_NOME_CTE",

    "recebedor_cnpj": "RECEBEDOR_CNPJ_CTE",
    "recebedor_nome": "RECEBEDOR_NOME_CTE",

    "valor_carga": "VALOR_CARGA_CTE",

    "produto_predominante": "PRODUTO_PREDOMINANTE",
    "sku_produto_predominante": (
        "SKU_PRODUTO_PREDOMINANTE"
    ),
    "descricao_produto_predominante": (
        "DESCRICAO_PRODUTO_PREDOMINANTE"
    ),

    "quantidade_nfe": "QUANTIDADE_NFE_CTE",
    "chaves_nfe_texto": "CHAVES_NFE",

    "tipo_documento_outro": (
        "TIPO_DOCUMENTO_OUTRO"
    ),
    "descricao_documento_outro": (
        "DESCRICAO_DOCUMENTO_OUTRO"
    ),
    "numero_documento_outro": (
        "NUMERO_DOCUMENTO_OUTRO"
    ),
}


def valor_texto(
    valor: Any,
) -> str:
    if valor is None:
        return ""

    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass

    return str(valor).strip()


def preparar_colunas_xml_cte(
    df: pd.DataFrame,
) -> pd.DataFrame:
    base = df.copy()

    for coluna in COLUNAS_XML_CTE:
        if coluna not in base.columns:
            base[coluna] = ""

    return base


def preencher_resultado_xml(
    base: pd.DataFrame,
    indice,
    resultado: dict,
) -> None:
    for chave_resultado, coluna_dataframe in (
        MAPEAMENTO_XML_CTE.items()
    ):
        valor = resultado.get(
            chave_resultado,
            "",
        )

        if valor is None:
            valor = ""

        base.at[
            indice,
            coluna_dataframe,
        ] = valor


def limpar_resultado_xml(
    base: pd.DataFrame,
    indice,
) -> None:
    for coluna in COLUNAS_XML_CTE:
        if coluna in {
            "STATUS_PARSER_XML",
            "ERRO_PARSER_XML",
        }:
            continue

        base.at[
            indice,
            coluna,
        ] = ""


def enriquecer_base_com_xml_cte(
    df: pd.DataFrame,
    job=None,
    log_func=None,
) -> pd.DataFrame:
    def log(mensagem: str) -> None:
        if log_func:
            log_func(mensagem)

        if job:
            from web.jobs import add_log

            add_log(
                job,
                mensagem,
            )

    base = preparar_colunas_xml_cte(
        df
    )

    coluna_ctrc = encontrar_coluna_ctrc(
        base
    )

    parser = CTeXMLParser()

    total = len(base)

    resultados_cache: dict[str, dict] = {}
    erros_cache: dict[str, str] = {}

    for posicao, (indice, row) in enumerate(
        base.iterrows(),
        start=1,
    ):
        ctrc = valor_texto(
            row.get(coluna_ctrc, "")
        )

        sequencial = valor_texto(
            row.get("SEQUENCIAL_CTRC", "")
        )

        arquivo_zip = valor_texto(
            row.get("ARQUIVO_ZIP_CTE", "")
        )

        status_download = (
            valor_texto(
                row.get(
                    "STATUS_DOWNLOAD_XML",
                    "",
                )
            )
            .upper()
        )

        base.at[
            indice,
            "STATUS_PARSER_XML",
        ] = ""

        base.at[
            indice,
            "ERRO_PARSER_XML",
        ] = ""

        limpar_resultado_xml(
            base=base,
            indice=indice,
        )

        if status_download not in {
            "OK",
            "CACHE",
        }:
            base.at[
                indice,
                "STATUS_PARSER_XML",
            ] = "IGNORADO_DOWNLOAD"

            base.at[
                indice,
                "ERRO_PARSER_XML",
            ] = (
                "XML não processado porque o download "
                f"está com status {status_download or 'VAZIO'}."
            )

            continue

        if not arquivo_zip:
            base.at[
                indice,
                "STATUS_PARSER_XML",
            ] = "SEM_ARQUIVO"

            base.at[
                indice,
                "ERRO_PARSER_XML",
            ] = (
                "Caminho do arquivo ZIP não informado."
            )

            continue

        caminho_zip = Path(
            arquivo_zip
        )

        chave_cache = str(
            caminho_zip.resolve()
        )

        if chave_cache in resultados_cache:
            preencher_resultado_xml(
                base=base,
                indice=indice,
                resultado=resultados_cache[
                    chave_cache
                ],
            )

            base.at[
                indice,
                "STATUS_PARSER_XML",
            ] = "CACHE"

            continue

        if chave_cache in erros_cache:
            base.at[
                indice,
                "STATUS_PARSER_XML",
            ] = "ERRO_CACHE"

            base.at[
                indice,
                "ERRO_PARSER_XML",
            ] = erros_cache[chave_cache]

            continue

        log(
            f"Lendo XML {posicao}/{total} | "
            f"CTRC={ctrc} | "
            f"arquivo={caminho_zip.name}"
        )

        try:
            if not caminho_zip.exists():
                raise FileNotFoundError(
                    "Arquivo ZIP não encontrado: "
                    f"{caminho_zip}"
                )

            resultado = parser.analisar_zip(
                arquivo_zip=caminho_zip,
                ctrc=ctrc,
                sequencial=sequencial,
            )

            resultados_cache[
                chave_cache
            ] = resultado

            preencher_resultado_xml(
                base=base,
                indice=indice,
                resultado=resultado,
            )

            base.at[
                indice,
                "STATUS_PARSER_XML",
            ] = "OK"

        except Exception as exc:
            mensagem_erro = str(exc)

            erros_cache[
                chave_cache
            ] = mensagem_erro

            base.at[
                indice,
                "STATUS_PARSER_XML",
            ] = "ERRO"

            base.at[
                indice,
                "ERRO_PARSER_XML",
            ] = mensagem_erro

            log(
                f"Erro ao ler XML | "
                f"CTRC={ctrc} | "
                f"arquivo={caminho_zip.name} | "
                f"erro={mensagem_erro}"
            )

    quantidade_ok = (
        base["STATUS_PARSER_XML"]
        .isin(["OK", "CACHE"])
        .sum()
    )

    quantidade_erro = (
        base["STATUS_PARSER_XML"]
        .isin(["ERRO", "ERRO_CACHE"])
        .sum()
    )

    log(
        "Leitura dos XMLs finalizada | "
        f"sucesso={quantidade_ok} | "
        f"erros={quantidade_erro} | "
        f"total={total}"
    )

    return base