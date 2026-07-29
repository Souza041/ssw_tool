from pathlib import Path

import pandas as pd

from operations.incidentes.danfe_downloader import (
    XMLCTeDownloader,
)
from operations.incidentes.enrich import (
    encontrar_coluna_ctrc,
)
from ssw.client import SSWClient


def baixar_xmls_cte(
    df: pd.DataFrame,
    client: SSWClient,
    output_dir: Path | str,
    job=None,
    log_func=None,
    apenas_debitos: bool = False,
) -> pd.DataFrame:
    def log(msg: str) -> None:
        if log_func:
            log_func(msg)

        if job:
            from web.jobs import add_log
            add_log(job, msg)

    base = df.copy()

    coluna_ctrc = encontrar_coluna_ctrc(base)

    downloader = XMLCTeDownloader(
        client=client,
        output_dir=output_dir,
    )

    total = len(base)
    baixados_cache: dict[str, Path] = {}
    erros_cache: dict[str, str] = {}

    for posicao, (indice, row) in enumerate(
        base.iterrows(),
        start=1,
    ):
        ctrc = str(
            row.get(coluna_ctrc, "")
        ).strip()

        sequencial = str(
            row.get("SEQUENCIAL_CTRC", "")
        ).strip()

        debito_validado = (
            str(
                row.get(
                    "DEBITO_VALIDADO",
                    "",
                )
            )
            .strip()
            .upper()
        )

        base.at[
            indice,
            "STATUS_DOWNLOAD_XML",
        ] = ""

        base.at[
            indice,
            "ERRO_DOWNLOAD_XML",
        ] = ""

        base.at[
            indice,
            "ARQUIVO_ZIP_CTE",
        ] = ""

        if apenas_debitos and debito_validado != "SIM":
            base.at[
                indice,
                "STATUS_DOWNLOAD_XML",
            ] = "IGNORADO_NAO_DEBITO"

            continue

        if not sequencial:
            base.at[
                indice,
                "STATUS_DOWNLOAD_XML",
            ] = "SEM_SEQUENCIAL"

            base.at[
                indice,
                "ERRO_DOWNLOAD_XML",
            ] = (
                "Sequencial do CTRC não informado."
            )

            continue

        log(
            f"Baixando XML {posicao}/{total} | "
            f"CTRC={ctrc} | sequencial={sequencial}"
        )

        if sequencial in erros_cache:
            base.at[
                indice,
                "STATUS_DOWNLOAD_XML",
            ] = "ERRO_CACHE"

            base.at[
                indice,
                "ERRO_DOWNLOAD_XML",
            ] = erros_cache[sequencial]

            continue

        try:
            veio_do_cache = (
                sequencial in baixados_cache
            )

            if not veio_do_cache:
                baixados_cache[
                    sequencial
                ] = downloader.baixar(
                    sequencial=sequencial,
                    ctrc=ctrc,
                )

            arquivo_zip = baixados_cache[
                sequencial
            ]

            base.at[
                indice,
                "STATUS_DOWNLOAD_XML",
            ] = (
                "CACHE"
                if veio_do_cache
                else "OK"
            )

            base.at[
                indice,
                "STATUS_DOWNLOAD_XML",
            ] = "OK"

            base.at[
                indice,
                "ARQUIVO_ZIP_CTE",
            ] = str(arquivo_zip)

        except Exception as exc:
            mensagem_erro = str(exc)

            erros_cache[sequencial] = mensagem_erro

            base.at[
                indice,
                "STATUS_DOWNLOAD_XML",
            ] = "ERRO"

            base.at[
                indice,
                "ERRO_DOWNLOAD_XML",
            ] = mensagem_erro

            log(
                f"Erro ao baixar XML | "
                f"CTRC={ctrc} | "
                f"sequencial={sequencial} | "
                f"erro={mensagem_erro}"
            )

    return base