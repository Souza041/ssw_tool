from datetime import datetime

import pandas as pd

from operations.incidentes.debit_validator import validar_debito
from operations.incidentes.op101_history import OP101History


def encontrar_coluna_ctrc(df: pd.DataFrame) -> str:
    possibilidades = [
        "CTRC",
        "SERIE_NUMERO_CTRC",
        "SERIE/NUMERO CTRC",
        "SERIE NUMERO CTRC",
    ]

    mapa = {
        str(col).strip().upper(): col
        for col in df.columns
    }

    for possibilidade in possibilidades:
        chave = possibilidade.upper()

        if chave in mapa:
            return mapa[chave]

    raise ValueError(
        f"Coluna CTRC não encontrada. Colunas: {list(df.columns)}"
    )


def formatar_data_hora(ocorrencia: dict | None) -> str:
    if not ocorrencia:
        return ""

    return str(
        ocorrencia.get("data_hora", "")
    ).strip()


def enriquecer_base_com_op101(
    df: pd.DataFrame,
    op101: OP101History,
    job=None,
    log_func=None,
) -> pd.DataFrame:
    def log(msg: str) -> None:
        if log_func:
            log_func(msg)

        if job:
            from web.jobs import add_log
            add_log(job, msg)

    base = df.copy()

    coluna_ctrc = encontrar_coluna_ctrc(base)

    total = len(base)

    historicos_cache: dict[str, dict] = {}

    for indice, row in base.iterrows():
        ctrc = str(row.get(coluna_ctrc, "")).strip()

        if not ctrc:
            base.at[indice, "STATUS_OP101"] = "SEM_CTRC"
            base.at[indice, "ERRO_OP101"] = "CTRC não informado."

            base.at[
                indice,
                "TIPO_OPERACAO",
            ] = "NAO_IDENTIFICADO"

            base.at[
                indice,
                "STATUS_VALIDACAO_DEBITO",
            ] = "PENDENTE"

            base.at[
                indice,
                "DEBITO_VALIDADO",
            ] = "NAO"

            base.at[
                indice,
                "MOTIVO_VALIDACAO_DEBITO",
            ] = "CTRC não informado."

            base.at[
                indice,
                "REGRA_VALIDACAO_DEBITO",
            ] = "SEM_CTRC"

            continue

        log(
            f"Consultando OP101 {indice + 1}/{total} | CTRC={ctrc}"
        )

        try:
            if ctrc not in historicos_cache:
                historicos_cache[ctrc] = op101.consultar_historico(
                    serie_numero_ctrc=ctrc,
                    limite=5,
                )

            historico = historicos_cache[ctrc]

            ocorrencias = historico["ocorrencias"]
            ultima = historico["ultima_ocorrencia"]
            ultimo_registro = historico.get(
                "ultimo_registro"
            )

            sequencial_ctrc = str(
                historico.get("sequencial", "")
            ).strip()

            tipo_operacao = str(
                historico.get(
                    "tipo_operacao",
                    "NAO_IDENTIFICADO",
                )
            ).strip().upper()

            if not tipo_operacao:
                tipo_operacao = "NAO_IDENTIFICADO"

            base.at[
                indice,
                "SEQUENCIAL_CTRC",
            ] = sequencial_ctrc

            base.at[
                indice,
                "TIPO_OPERACAO",
            ] = tipo_operacao

            base.at[
                indice,
                "OP101_ULTIMO_REGISTRO_DATA",
            ] = (
                ultimo_registro.get("data_hora", "")
                if ultimo_registro
                else ""
            )

            base.at[
                indice,
                "OP101_ULTIMO_REGISTRO_TEXTO",
            ] = (
                (
                    ultimo_registro.get(
                        "ocorrencia_original",
                        "",
                    )
                    + " "
                    + ultimo_registro.get(
                        "complemento",
                        "",
                    )
                ).strip()
                if ultimo_registro
                else ""
            )

            base.at[indice, "STATUS_OP101"] = "OK"
            base.at[indice, "ERRO_OP101"] = ""

            for posicao in range(1, 6):
                ocorrencia = (
                    ocorrencias[posicao - 1]
                    if len(ocorrencias) >= posicao
                    else None
                )

                prefixo = f"OP101_OC_{posicao}"

                if not ocorrencia:
                    base.at[
                        indice,
                        f"{prefixo}_CODIGO",
                    ] = ""

                    base.at[
                        indice,
                        f"{prefixo}_DATA_HORA",
                    ] = ""

                    base.at[
                        indice,
                        f"{prefixo}_USUARIO",
                    ] = ""

                    base.at[
                        indice,
                        f"{prefixo}_DESCRICAO",
                    ] = ""

                    base.at[
                        indice,
                        f"{prefixo}_COMPLEMENTO",
                    ] = ""

                    continue

                base.at[
                    indice,
                    f"{prefixo}_CODIGO",
                ] = ocorrencia.get("codigo", "")

                base.at[
                    indice,
                    f"{prefixo}_DATA_HORA",
                ] = ocorrencia.get("data_hora", "")

                base.at[
                    indice,
                    f"{prefixo}_USUARIO",
                ] = ocorrencia.get("usuario", "")

                base.at[
                    indice,
                    f"{prefixo}_DESCRICAO",
                ] = ocorrencia.get("descricao", "")

                base.at[
                    indice,
                    f"{prefixo}_COMPLEMENTO",
                ] = ocorrencia.get("complemento", "")

            codigo_relatorio = str(
                row.get("_OC_NORMALIZADA", "")
            ).strip()

            codigo_ultima = (
                str(
                    ultima.get("codigo", "")
                ).strip()
                if ultima
                else ""
            )

            base.at[
                indice,
                "OC_RELATORIO",
            ] = codigo_relatorio

            base.at[
                indice,
                "OC_ULTIMA_OP101",
            ] = codigo_ultima

            ocorrencia_confere = (
                "SIM"
                if codigo_relatorio == codigo_ultima
                else "NAO"
            )

            base.at[
                indice,
                "OC_RELATORIO_CONFERE",
            ] = ocorrencia_confere

            decisao = validar_debito(
                ultima_ocorrencia=ultima,
                ocorrencia_relatorio_confere=ocorrencia_confere,
            )

            base.at[
                indice,
                "STATUS_VALIDACAO_DEBITO",
            ] = decisao["status"]

            base.at[
                indice,
                "DEBITO_VALIDADO",
            ] = (
                "SIM"
                if decisao["debito"]
                else "NAO"
            )

            base.at[
                indice,
                "MOTIVO_VALIDACAO_DEBITO",
            ] = decisao["motivo"]

            base.at[
                indice,
                "REGRA_VALIDACAO_DEBITO",
            ] = decisao["regra"]

        except Exception as exc:
            base.at[indice, "STATUS_OP101"] = "ERRO"
            base.at[indice, "ERRO_OP101"] = str(exc)

            base.at[
                indice,
                "OP101_ULTIMO_REGISTRO_DATA",
            ] = ""

            base.at[
                indice,
                "OP101_ULTIMO_REGISTRO_TEXTO",
            ] = ""

            base.at[
                indice,
                "OC_ULTIMA_OP101",
            ] = ""

            base.at[
                indice,
                "OC_RELATORIO_CONFERE",
            ] = ""

            base.at[
                indice,
                "STATUS_VALIDACAO_DEBITO",
            ] = "ERRO"

            base.at[
                indice,
                "DEBITO_VALIDADO",
            ] = "NAO"

            base.at[
                indice,
                "MOTIVO_VALIDACAO_DEBITO",
            ] = str(exc)

            base.at[
                indice,
                "REGRA_VALIDACAO_DEBITO",
            ] = "ERRO_OP101"

            base.at[
                indice,
                "SEQUENCIAL_CTRC",
            ] = ""

            base.at[
                indice,
                "TIPO_OPERACAO",
            ] = "NAO_IDENTIFICADO"

            log(
                f"Erro OP101 | CTRC={ctrc} | erro={exc}"
            )

    return base