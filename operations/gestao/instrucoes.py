import os
import re

import pandas as pd

from operations.op101.instructions import OP101Instructions
from ssw.client import SSWClient


def to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "sim", "yes", "s"}


def obter_ctrcs_para_instrucao(filtros: dict[str, pd.DataFrame]) -> list[str]:
    bases = []

    for chave in ["em_atraso", "pre_alerta", "pendencia_gestao"]:
        df = filtros.get(chave)

        if df is not None and not df.empty:
            bases.append(df)

    if not bases:
        return []

    base = pd.concat(bases, ignore_index=True)

    if "Serie/Numero CTRC" not in base.columns:
        raise ValueError("Coluna 'Serie/Numero CTRC' não encontrada para lançar instrução.")

    ctrcs = (
        base["Serie/Numero CTRC"]
        .dropna()
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .drop_duplicates()
        .tolist()
    )

    limite = int(os.getenv("LIMITE_INSTRUCOES_TESTE", "0"))

    if limite > 0:
        ctrcs = ctrcs[:limite]

    return ctrcs


def processar_instrucoes_gestao(
    client: SSWClient,
    filtros: dict[str, pd.DataFrame],
    logger=None,
    job=None,
) -> None:
    lanca_instrucao = to_bool(os.getenv("LANCA_INSTRUCAO_SSW"), False)

    if not lanca_instrucao:
        msg = "Lançamento de instruções OP101 desativado no .env."

        if logger:
            logger.info(msg)

        if job:
            from web.jobs import add_log
            add_log(job, msg)

        return

    texto_instrucao = os.getenv(
        "TEXTO_INSTRUCAO_SSW",
        "Pré-alerta enviado via e-mail",
    )

    ctrcs = obter_ctrcs_para_instrucao(filtros)

    if not ctrcs:
        msg = "Nenhum CTRC encontrado para lançamento de instrução."

        if logger:
            logger.info(msg)

        if job:
            from web.jobs import add_log
            add_log(job, msg)

        return

    if logger:
        logger.info("Iniciando lançamento OP101. Total: %s", len(ctrcs))

    if job:
        from web.jobs import add_log
        add_log(job, f"Iniciando lançamento OP101. Total: {len(ctrcs)}")

    op101 = OP101Instructions(client)
    op101.open()

    sucesso = 0
    falha = 0

    for idx, ctrc in enumerate(ctrcs, start=1):
        try:
            if job:
                from web.jobs import add_log
                add_log(job, f"Lançando instrução {idx}/{len(ctrcs)} | CTRC={ctrc}")

            op101.lancar_instrucao(
                serie_numero_ctrc=ctrc,
                texto=texto_instrucao,
            )

            sucesso += 1

        except Exception as exc:
            falha += 1

            if logger:
                logger.exception("Falha ao lançar instrução no CTRC %s: %s", ctrc, exc)

            if job:
                from web.jobs import add_log
                add_log(job, f"Falha OP101 | CTRC={ctrc} | erro={exc}")

    msg_final = f"Lançamento OP101 finalizado. Sucesso={sucesso} | Falha={falha}"

    if logger:
        logger.info(msg_final)

    if job:
        from web.jobs import add_log
        add_log(job, msg_final)