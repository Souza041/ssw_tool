import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd

from utils.excel import carregar_planilha
from web.jobs import add_log, set_progress

from .common import competencia_ssw, data_ssw, somente_digitos, valor_ssw
from .op475 import OP475Despesas
from .op506 import OP506Indenizacao


CNPJ_HISTORICO_CONCATENADO = {"05117268000806"}

COLUNAS = {
    "VALOR": "Valor",
    "MOTIVO": "MOTIVO",
    "NF": "NF",
    "CTRC": "CTRC",
    "UNIDADE": "UNIDADE",
    "LANCAMENTO": "LANÇ",
    "VENCIMENTO": "Vcto",
    "DESCRICAO": "SKU",
    "HISTORICO_CONCATENADO": "Concatenado Campo Histórico SSW475",
    "CNPJ": "CNPJ LANC",
}

MODO_COMPLETO = "completo"
MODO_OP475 = "op475"
MODO_OP506 = "op506"

MODOS_VALIDOS = {
    MODO_COMPLETO,
    MODO_OP475,
    MODO_OP506,
}

RESULTADO_SERIE = "SÉRIE BOT"
RESULTADO_OP475 = "STATUS OP475"
RESULTADO_OP506 = "STATUS OP506"
RESULTADO_BOT = "STATUS BOT"
RESULTADO_MSG = "MENSAGEM BOT"
RESULTADO_SEQ_CTRC = "SEQ CTRC BOT"


def _normalizar_cabecalho(texto: str) -> str:
    valor = str(texto or "").strip().upper()
    valor = unicodedata.normalize("NFKD", valor)
    return valor.encode("ASCII", "ignore").decode("ASCII")


def _resolver_colunas(
    df: pd.DataFrame,
    modo: str,
) -> dict[str, str]:
    mapa = {
        _normalizar_cabecalho(c): c
        for c in df.columns
    }

    resolvidas = {}

    obrigatorias_475 = {
        "VALOR",
        "MOTIVO",
        "NF",
        "UNIDADE",
        "VENCIMENTO",
        "CNPJ",
    }

    obrigatorias_506 = {
        "VALOR",
        "MOTIVO",
        "CTRC",
        "UNIDADE",
        "LANCAMENTO",
        "DESCRICAO",
    }

    if modo == MODO_OP475:
        obrigatorias = obrigatorias_475

    elif modo == MODO_OP506:
        obrigatorias = obrigatorias_506

    else:
        obrigatorias = (
            obrigatorias_475
            | obrigatorias_506
        )

        # No fluxo completo, a OP475 é justamente
        # quem cria o número do lançamento.
        obrigatorias.discard("LANCAMENTO")

    for chave, nome in COLUNAS.items():
        normal = _normalizar_cabecalho(nome)

        if normal in mapa:
            resolvidas[chave] = mapa[normal]
            continue

        if chave in obrigatorias:
            raise ValueError(
                f"Coluna obrigatória ausente "
                f"para modo {modo}: {nome}"
            )

    for candidato in (
        "COMPETENCIA",
        "MES COMPETENCIA",
        "MÊS COMPETÊNCIA",
    ):
        normal = _normalizar_cabecalho(
            candidato
        )

        if normal in mapa:
            resolvidas["COMPETENCIA"] = mapa[normal]
            break

    return resolvidas


def _nf_limpa(valor) -> str:
    texto = str(valor or "").strip()
    if texto.endswith(".0"):
        texto = texto[:-2]
    return somente_digitos(texto)


def _lancamento_existente(valor) -> str:
    texto = str(valor or "").strip()
    if not texto or texto.lower() in {"nan", "none"}:
        return ""
    if texto.endswith(".0"):
        texto = texto[:-2]
    return somente_digitos(texto)

def _valor_coluna(
    row,
    col: dict[str, str],
    chave: str,
    padrao="",
):
    nome = col.get(chave)

    if not nome:
        return padrao

    return row.get(nome, padrao)

def processar_planilha_debitos(
    *,
    client,
    input_file: Path,
    output_file: Path,
    modo: str = MODO_COMPLETO,
    job=None,
) -> Path:

    modo = str(modo or "").strip().lower()

    if modo not in MODOS_VALIDOS:
        raise ValueError(
            f"Modo de processamento inválido: {modo}"
        )

    executar_475 = modo in {
        MODO_OP475,
        MODO_COMPLETO,
    }

    executar_506 = modo in {
        MODO_OP506,
        MODO_COMPLETO,
    }

    df = carregar_planilha(input_file)
    col = _resolver_colunas(
        df,
        modo,
    )

    if executar_475 and "LANCAMENTO" not in col:
        df["LANÇ"] = ""
        col["LANCAMENTO"] = "LANÇ"

    for nome in [
        RESULTADO_SERIE,
        RESULTADO_OP475,
        RESULTADO_OP506,
        RESULTADO_BOT,
        RESULTADO_MSG,
        RESULTADO_SEQ_CTRC,
    ]:
        if nome not in df.columns:
            df[nome] = ""

    # Define apenas uma série inicial considerando ocorrências da NF no arquivo.
    # A OP475 valida a disponibilidade real no SSW e incrementa automaticamente
    # até encontrar a primeira série livre.
    contador_nf: dict[str, int] = defaultdict(int)
    series: dict[int, int] = {}
    proxima_serie_nf: dict[str, int] = {}                                 


    if executar_475 and "NF" in col:
        for index, row in df.iterrows():
            nf = _nf_limpa(
                _valor_coluna(
                    row,
                    col,
                    "NF",
                )
            )

            if nf:
                contador_nf[nf] += 1
                series[index] = contador_nf[nf]

    total = len(df)
    if job:
        set_progress(job, 0, total)

    op475 = OP475Despesas(client)
    op506 = OP506Indenizacao(client)

    output_file.parent.mkdir(parents=True, exist_ok=True)

    for posicao, (index, row) in enumerate(df.iterrows(), start=1):
        nf = _nf_limpa(
            _valor_coluna(
                row,
                col,
                "NF",
            )
        )

        ctrc = str(
            _valor_coluna(
                row,
                col,
                "CTRC",
            )
            or ""
        ).strip().upper()

        cnpj = somente_digitos(
            _valor_coluna(
                row,
                col,
                "CNPJ",
            )
        )

        unidade = str(
            _valor_coluna(
                row,
                col,
                "UNIDADE",
            )
            or ""
        ).strip().upper()

        lancamento_previo = _lancamento_existente(
            _valor_coluna(
                row,
                col,
                "LANCAMENTO",
            )
        )
        status_475_anterior = str(row.get(RESULTADO_OP475, "") or "").strip().upper()
        status_506_anterior = str(row.get(RESULTADO_OP506, "") or "").strip().upper()
        retomar_op506 = bool(
            modo == MODO_COMPLETO
            and lancamento_previo
            and status_475_anterior == "OK"
            and status_506_anterior != "OK"
        )

        lancamento = lancamento_previo

        etapa = "VALIDAÇÃO"

        try:

            if not unidade:
                raise ValueError(
                    "UNIDADE é obrigatória."
                )

            if executar_475 and not retomar_op506:
                if not nf:
                    raise ValueError(
                        "NF é obrigatória para OP475."
                    )

                if not cnpj:
                    raise ValueError(
                        "CNPJ LANC é obrigatório para OP475."
                    )

            if executar_506:
                if not ctrc:
                    raise ValueError(
                        "CTRC é obrigatório para OP506."
                    )

                if (
                    modo == MODO_OP506
                    and not lancamento_previo
                ):
                    raise ValueError(
                        "LANÇ é obrigatório para executar "
                        "somente a OP506."
                    )

            # validações...

            if (
                modo == MODO_OP475
                and lancamento_previo
            ):
                df.at[
                    index,
                    RESULTADO_OP475,
                ] = (
                    status_475_anterior
                    or "JÁ EXISTE"
                )

                df.at[
                    index,
                    RESULTADO_OP506,
                ] = (
                    status_506_anterior
                    or "NÃO EXECUTADO"
                )

                df.at[
                    index,
                    RESULTADO_BOT,
                ] = "IGNORADO"

                df.at[
                    index,
                    RESULTADO_MSG,
                ] = (
                    f"Linha ignorada por já possuir "
                    f"lançamento {lancamento_previo}."
                )

                if job:
                    add_log(
                        job,
                        f"{posicao}/{total} | "
                        f"NF={nf or '-'} | "
                        f"LANÇ={lancamento_previo} | "
                        "OP475 ignorada.",
                    )

                continue
            if executar_475 and not retomar_op506:
                etapa = "OP475"
                serie = max(
                    series.get(index, 1),
                    proxima_serie_nf.get(nf, 1),
                )

                valor = valor_ssw(
                    _valor_coluna(
                        row,
                        col,
                        "VALOR",
                    )
                )

                vencimento = data_ssw(
                    _valor_coluna(
                        row,
                        col,
                        "VENCIMENTO",
                    )
                )

                competencia_raw = (
                    row[col["COMPETENCIA"]]
                    if "COMPETENCIA" in col
                    else ""
                )

                competencia = competencia_ssw(
                    competencia_raw,
                    vencimento,
                )

                motivo = str(
                    _valor_coluna(
                        row,
                        col,
                        "MOTIVO",
                    )
                    or ""
                ).strip()

                historico_concat = str(
                    _valor_coluna(
                        row,
                        col,
                        "HISTORICO_CONCATENADO",
                    )
                    or ""
                ).strip()

                if (
                    cnpj in CNPJ_HISTORICO_CONCATENADO
                    and not historico_concat
                ):
                    raise ValueError(
                        "Coluna/campo de histórico concatenado "
                        "é obrigatório para CNPJ Whirlpool."
                    )

                historico = (
                    historico_concat
                    if cnpj in CNPJ_HISTORICO_CONCATENADO
                    else motivo
                )

                if not motivo:
                    raise ValueError(
                        "MOTIVO vazio."
                    )

                if not historico:
                    raise ValueError(
                        "Histórico da OP475 vazio."
                    )

                if job:
                    add_log(
                        job,
                        f"{posicao}/{total} | "
                        f"NF={nf} | "
                        f"série inicial={serie} | "
                        f"UNIDADE={unidade} | "
                        f"iniciando OP475.",
                    )

                op475.open()

                resultado_475 = op475.lancar(
                    cnpj=cnpj,
                    nf=nf,
                    serie=serie,
                    valor=valor,
                    vencimento=vencimento,
                    competencia=competencia,
                    historico=historico,
                    unidade_lancamento=unidade,
                )

                lancamento = resultado_475[
                    "lancamento"
                ]

                serie_utilizada = resultado_475[
                    "serie"
                ]

                proxima_serie_nf[nf] = (
                    serie_utilizada + 1
                )

                df.at[
                    index,
                    col["LANCAMENTO"],
                ] = lancamento

                df.at[
                    index,
                    RESULTADO_SERIE,
                ] = serie_utilizada

                df.at[
                    index,
                    RESULTADO_OP475,
                ] = "OK"

                # Persistência crítica antes da 506
                df.to_excel(
                    output_file,
                    index=False,
                )

                if job:
                    add_log(
                        job,
                        f"NF={nf} | "
                        f"série={serie_utilizada} | "
                        f"lançamento {lancamento} criado.",
                    )

            if modo == MODO_OP475:
                df.at[
                    index,
                    RESULTADO_OP506,
                ] = "NÃO EXECUTADO"

                df.at[
                    index,
                    RESULTADO_BOT,
                ] = "OK"

                df.at[
                    index,
                    RESULTADO_MSG,
                ] = (
                    f"Lançamento {lancamento} "
                    "criado na OP475."
                )

                if job:
                    add_log(
                        job,
                        f"NF={nf} | "
                        "OP475 concluída. "
                        "OP506 não solicitada.",
                    )

                continue

            if retomar_op506:
                lancamento = lancamento_previo

                df.at[
                    index,
                    RESULTADO_OP475,
                ] = "OK"

                if job:
                    add_log(
                        job,
                        f"{posicao}/{total} | "
                        f"NF={nf} | "
                        f"retomando OP506 com "
                        f"lançamento {lancamento}.",
                    )

            if executar_506:
                etapa = "OP506"
                if not lancamento:
                    raise ValueError(
                        "Número de lançamento "
                        "ausente para OP506."
                    )

                valor = valor_ssw(
                    _valor_coluna(
                        row,
                        col,
                        "VALOR",
                    )
                )

                motivo = str(
                    _valor_coluna(
                        row,
                        col,
                        "MOTIVO",
                    )
                    or ""
                ).strip()

                descricao = str(
                    _valor_coluna(
                        row,
                        col,
                        "DESCRICAO",
                    )
                    or ""
                ).strip()

                if not motivo:
                    raise ValueError(
                        "MOTIVO vazio."
                    )

                if not descricao:
                    raise ValueError(
                        "SKU/Descrição da mercadoria vazia."
                    )

                if modo == MODO_OP506:
                    df.at[
                        index,
                        RESULTADO_OP475,
                    ] = (
                        status_475_anterior
                        or "NÃO EXECUTADO"
                    )

                if job:
                    add_log(
                        job,
                        f"CTRC={ctrc} | "
                        f"LANÇ={lancamento} | "
                        f"UNIDADE={unidade} | "
                        f"iniciando OP506.",
                    )

                op506.open(
                    unidade
                )

                resultado_506 = op506.indenizar(
                    ctrc=ctrc,
                    lancamento=lancamento,
                    descricao_mercadoria=descricao,
                    motivo=motivo,
                    valor=valor,
                    unidade_responsavel=unidade,
                )

                df.at[
                    index,
                    RESULTADO_OP506,
                ] = "OK"

                df.at[
                    index,
                    RESULTADO_SEQ_CTRC,
                ] = resultado_506.get(
                    "seq_ctrc",
                    "",
                )

                df.at[
                    index,
                    RESULTADO_BOT,
                ] = "OK"

                df.at[
                    index,
                    RESULTADO_MSG,
                ] = resultado_506.get(
                    "mensagem",
                    "Processado com sucesso.",
                )

                if job:
                    add_log(
                        job,
                        f"CTRC={ctrc} | "
                        "OP506 concluída com sucesso.",
                    )

        except Exception as exc:
            if etapa == "OP506":
                df.at[
                    index,
                    RESULTADO_OP506,
                ] = "ERRO"

            elif etapa == "OP475":
                df.at[
                    index,
                    RESULTADO_OP475,
                ] = "ERRO"

                if modo == MODO_COMPLETO:
                    df.at[
                        index,
                        RESULTADO_OP506,
                    ] = "NÃO EXECUTADO"

            else:
                # Erro durante validação inicial.
                if modo == MODO_OP506:
                    df.at[
                        index,
                        RESULTADO_OP506,
                    ] = "ERRO"

                    df.at[
                        index,
                        RESULTADO_OP475,
                    ] = (
                        status_475_anterior
                        or "NÃO EXECUTADO"
                    )

                else:
                    df.at[
                        index,
                        RESULTADO_OP475,
                    ] = "ERRO"

            df.at[
                index,
                RESULTADO_BOT,
            ] = "ERRO"

            df.at[
                index,
                RESULTADO_MSG,
            ] = str(exc)

            if job:
                referencia = (
                    f"NF={nf}"
                    if nf
                    else f"CTRC={ctrc or '-'}"
                )

                add_log(
                    job,
                    f"Erro na linha {posicao} | "
                    f"{referencia} | "
                    f"{exc}",
                )

        finally:
            if job:
                set_progress(job, posicao, total)
                add_log(job, f"Progresso: {posicao}/{total}")
            df.to_excel(output_file, index=False)

    return output_file
