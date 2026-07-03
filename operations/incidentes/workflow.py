from pathlib import Path

import pandas as pd

from operations.op930.clientes import GRUPOS_ATIVOS_MVP
from operations.op930.parser import tratar_op930
from operations.op930.report import OP930Report
from ssw.client import SSWClient

from operations.op156.queue import RelatorioSemDados


def executar_incidentes_op930(
    client: SSWClient,
    data_inicial: str,
    data_final: str,
    output_dir: Path,
    grupos: dict[str, list[str]] | None = None,
    timeout_seconds: int = 300,
    job=None,
    log_func=None,
) -> Path:
    def log(msg: str):
        if log_func:
            log_func(msg)

        if job:
            from web.jobs import add_log
            add_log(job, msg)

    grupos = grupos or GRUPOS_ATIVOS_MVP

    bruto_dir = output_dir / "op930" / "bruto"
    tratado_dir = output_dir / "op930" / "tratado"
    consolidado_dir = output_dir / "op930" / "consolidado"

    bruto_dir.mkdir(parents=True, exist_ok=True)
    tratado_dir.mkdir(parents=True, exist_ok=True)
    consolidado_dir.mkdir(parents=True, exist_ok=True)

    op930 = OP930Report(client)

    bases = []

    total = sum(len(cnpjs) for cnpjs in grupos.values())
    atual = 0

    if job:
        from web.jobs import set_progress
        set_progress(job, 0, total)

    for grupo, cnpjs in grupos.items():
        for cnpj in cnpjs:
            atual += 1

            log(f"Gerando OP930 {atual}/{total} | {grupo} | {cnpj}")

            try:
                arquivo = op930.gerar_e_baixar_por_cnpj(
                    output_dir=bruto_dir,
                    data_inicial=data_inicial,
                    data_final=data_final,
                    cnpj=cnpj,
                    grupo=grupo,
                    timeout_seconds=timeout_seconds,
                )

            except RelatorioSemDados:
                log(f"{grupo}: nenhum relatório encontrado.")

                if job:
                    from web.jobs import set_progress
                    set_progress(job, atual, total)

                continue

            except Exception as exc:
                log(f"{grupo}: relatório não gerado ou falhou. Erro: {exc}")

                if job:
                    from web.jobs import set_progress
                    set_progress(job, atual, total)

                continue

            except TimeoutError:
                log(f"{grupo}: nenhum relatório encontrado.")

                if job:
                    from web.jobs import set_progress
                    set_progress(job, atual, total)

                continue

            log(f"Arquivo baixado: {arquivo.name}")

            log(f"Tratando relatório {arquivo.name}...")

            base = tratar_op930(
                file_path=arquivo,
                grupo=grupo,
                cnpj=cnpj,
            )

            log(f"Salvando arquivo tratado...")

            arquivo_tratado = tratado_dir / f"{arquivo.stem}_TRATADO.xlsx"

            base.to_excel(
                arquivo_tratado,
                index=False,
            )

            log(f"Arquivo tratado salvo: {arquivo_tratado.name}")

            log(f"{grupo}: {len(base)} linhas após filtro.")

            bases.append(base)

            if job:
                from web.jobs import set_progress
                set_progress(job, atual, total)

    if not bases:
        raise ValueError("Nenhuma base gerada.")
    
    log("Consolidando bases...")

    base_final = pd.concat(bases, ignore_index=True)

    log(f"Base consolidada: {len(base_final)} registros.")

    output_file = consolidado_dir / "incidentes_op930_base.xlsx"

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        base_final.to_excel(writer, index=False, sheet_name="base")

    log(f"Base consolidada gerada: {output_file.name}")

    return output_file