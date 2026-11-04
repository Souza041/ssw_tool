from pathlib import Path

import pandas as pd

import gc

from operations.op930.clientes import GRUPOS_ATIVOS_MVP
from operations.op930.parser import (
    extrair_volume_op930,
    tratar_op930,
)
from modules.incidentes.volume_publisher import (
    VolumePublisher,
)
from operations.op930.report import OP930Report
from operations.incidentes.enrich import enriquecer_base_com_op101
from operations.incidentes.op101_history import OP101History
from operations.incidentes.analytics import preparar_base_analitica
from operations.incidentes.xml_enricher import baixar_xmls_cte
from operations.incidentes.publisher import IncidentPublisher
from ssw.client import SSWClient


from operations.op156.queue import RelatorioSemDados

from operations.incidentes.xml_parser_enricher import (enriquecer_base_com_xml_cte,)

from operations.incidentes.exporter import (exportar_incidentes,)

from modules.incidentes.mysql_gate import importar_snapshot_validado

from modules.incidentes.publicacao_integrada import (
    finalizar_publicacao_incidentes,
)

from modules.incidentes.integridade import (
    validar_quantidade_linhas,
)

def executar_incidentes_op930(
    client: SSWClient,
    data_inicial: str,
    data_final: str,
    output_dir: Path,
    grupos: dict[str, list[str]] | None = None,
    timeout_seconds: int = 300,
    job=None,
    log_func=None,
    importar_mysql: bool = False,
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
    volumes = []

    total = sum(len(cnpjs) for cnpjs in grupos.values())
    atual = 0
    falhas_extracao = []
    sem_dados = []
    extraidos = []

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
                sem_dados.append((grupo, cnpj))
                log(f"{grupo}: nenhum relatório encontrado para {cnpj}.")

                if job:
                    from web.jobs import set_progress
                    set_progress(job, atual, total)

                continue

            except TimeoutError:
                falhas_extracao.append((grupo, cnpj, "TIMEOUT"))
                log(f"{grupo}: tempo limite ao aguardar relatório para {cnpj}.")

                if job:
                    from web.jobs import set_progress
                    set_progress(job, atual, total)

                continue

            except Exception as exc:
                falhas_extracao.append((grupo, cnpj, str(exc)))
                log(
                    f"{grupo}: relatório não gerado ou falhou. "
                    f"Erro: {exc}"
                )

                if job:
                    from web.jobs import set_progress
                    set_progress(job, atual, total)

                continue

            extraidos.append((grupo, cnpj))
            log(f"Arquivo baixado: {arquivo.name}")

            log(
                f"Extraindo volume bruto de {arquivo.name}..."
            )

            volume = extrair_volume_op930(
                file_path=arquivo,
                grupo=grupo,
                cnpj=cnpj,
            )

            quantidade_bruta = len(volume)

            log(
                f"{grupo}: "
                f"{quantidade_bruta} linhas na base bruta de volume."
            )

            if not volume.empty:
                if "CTRC" not in volume.columns:
                    raise ValueError(
                        f"Coluna CTRC não encontrada "
                        f"na base de volume de {grupo}."
                    )

                volume["CTRC"] = (
                    volume["CTRC"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

                volume = volume[
                    volume["CTRC"].ne("")
                    & volume["CTRC"].str.lower().ne("nan")
                ].copy()

                volume = (
                    volume
                    .drop_duplicates(
                        subset=["CTRC"],
                        keep="first",
                    )
                    .reset_index(drop=True)
                )

                log(
                    f"{grupo}: volume reduzido de "
                    f"{quantidade_bruta} para "
                    f"{len(volume)} CTRCs únicos."
                )

                if not volume.empty:
                    volumes.append(volume)

            log(
                f"{grupo}: {len(volume)} linhas "
                "na base bruta de volume."
            )

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

    if not volumes:
        raise ValueError(
            "Nenhuma base de volume OP930 foi gerada."
        )

    log("Consolidando bases...")

    # ---------------------------------------------------------
    # VOLUME TOTAL DE CTRCs
    # ---------------------------------------------------------

    base_volume = pd.concat(
        volumes,
        ignore_index=True,
    )

    log(
        "Base de volume OP930 consolidada: "
        f"{len(base_volume)} registros brutos."
    )

    if "CTRC" not in base_volume.columns:
        raise ValueError(
            "Coluna CTRC não encontrada na base de volume OP930."
        )

    # Normaliza CTRC
    base_volume["CTRC"] = (
        base_volume["CTRC"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # Remove CTRCs vazios / inválidos
    base_volume = base_volume[
        base_volume["CTRC"].ne("")
        & base_volume["CTRC"].str.lower().ne("nan")
    ].copy()

    # ---------------------------------------------------------
    # IMPORTANTE:
    # Para o denominador da Taxa por Mil precisamos de CTRCs
    # únicos, e não de todas as linhas de ocorrência da OP930.
    # ---------------------------------------------------------

    base_volume = (
        base_volume
        .drop_duplicates(
            subset=["CTRC"],
            keep="first",
        )
        .reset_index(drop=True)
    )

    log(
        "Base de volume OP930 reduzida para "
        f"{len(base_volume)} CTRCs únicos."
    )

    # A lista original pode conter centenas de milhares de linhas.
    # Não precisamos mais dela daqui para frente.
    del volumes
    gc.collect()

    log(
        "Memória das bases brutas de volume liberada."
    )

    volume_dir = (
        output_dir
        / "op930"
        / "volume"
    )

    volume_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    arquivo_volume = (
        volume_dir
        / "volume_ctrcs_op930.xlsx"
    )

    log(
        "Salvando base de volume OP930 "
        f"com {len(base_volume)} CTRCs únicos..."
    )

    base_volume.to_excel(
        arquivo_volume,
        index=False,
    )

    log(
        "Base de volume OP930 gerada: "
        f"{len(base_volume)} registros."
    )

    # ---------------------------------------------------------
    # BASE DE INCIDENTES
    # ---------------------------------------------------------

    base_final = pd.concat(
        bases,
        ignore_index=True,
    )

    quantidade_esperada = len(base_final)

    log(
        "Base consolidada inicial: "
        f"{len(base_final)} registros."
    )

    # ---------------------------------------------------------
    # 1. Salva o consolidado inicial, antes da consulta OP101
    # ---------------------------------------------------------

    output_file = (
        consolidado_dir
        / "incidentes_op930_base.xlsx"
    )

    base_final.to_excel(
        output_file,
        index=False,
    )

    log(
        f"Base consolidada inicial gerada: "
        f"{output_file.name}"
    )

    # ---------------------------------------------------------
    # 2. Enriquece a base consultando a OP101
    # ---------------------------------------------------------
    log("Iniciando enriquecimento pela OP101...")

    op101_history = OP101History(client)

    base_enriquecida = enriquecer_base_com_op101(
        df=base_final,
        op101=op101_history,
        job=job,
        log_func=log_func,
    )

    validar_quantidade_linhas(
        etapa="OP101",
        quantidade_esperada=quantidade_esperada,
        quantidade_obtida=len(base_enriquecida),
        log=log,
    )

    log("Enriquecimento OP101 finalizado.")

    base_enriquecida = preparar_base_analitica(
        base_enriquecida
    )

    log("Preparando colunas analíticas para dashboard...")

    validar_quantidade_linhas(
        etapa="ANALYTICS",
        quantidade_esperada=quantidade_esperada,
        quantidade_obtida=len(base_enriquecida),
        log=log,
    )

    log("Colunas analíticas preparadas.")

    log("Iniciando download dos XMLs vinculados aos CTRCs...")

    base_enriquecida = baixar_xmls_cte(
        df=base_enriquecida,
        client=client,
        output_dir=output_dir / "op930" / "xml",
        job=job,
        log_func=log_func,
        apenas_debitos=False,
    )

    validar_quantidade_linhas(
        etapa="DOWNLOAD_XML",
        quantidade_esperada=quantidade_esperada,
        quantidade_obtida=len(base_enriquecida),
        log=log,
    )

    log("Download dos XMLs finalizado.")

    log("Iniciando leitura dos XMLs dos CT-es...")

    base_enriquecida = enriquecer_base_com_xml_cte(
        df=base_enriquecida,
        job=job,
        log_func=log_func,
    )

    validar_quantidade_linhas(
        etapa="PARSER_XML",
        quantidade_esperada=quantidade_esperada,
        quantidade_obtida=len(base_enriquecida),
        log=log,
    )

    log("Leitura dos XMLs dos CT-es finalizada.")

    # ---------------------------------------------------------
    # 3. Cria diretórios de auditoria e base final
    # ---------------------------------------------------------
    auditoria_dir = (
        output_dir
        / "op930"
        / "auditoria"
    )

    final_dir = (
        output_dir
        / "op930"
        / "final"
    )

    auditoria_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    final_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ---------------------------------------------------------
    # 4. Salva todos os registros enriquecidos para auditoria
    # ---------------------------------------------------------
    arquivo_auditoria = (
        auditoria_dir
        / "incidentes_op930_auditoria.xlsx"
    )

    exportar_incidentes(
        df=base_enriquecida,
        caminho_saida=arquivo_auditoria,
    )

    log(
        f"Base de auditoria gerada: "
        f"{arquivo_auditoria.name}"
    )

    # ---------------------------------------------------------
    # 5. Filtra somente débitos efetivamente validados
    # ---------------------------------------------------------
    if "DEBITO_VALIDADO" in base_enriquecida.columns:
        base_debitos = base_enriquecida[
            base_enriquecida["DEBITO_VALIDADO"]
            .astype(str)
            .str.strip()
            .str.upper()
            == "SIM"
        ].copy()
    else:
        base_debitos = (
            base_enriquecida
            .iloc[0:0]
            .copy()
        )

    arquivo_final = (
        final_dir
        / "incidentes_op930_debitos_validos.xlsx"
    )

    base_debitos.to_excel(
        arquivo_final,
        index=False,
    )

    log(
        f"Base final de débitos gerada: "
        f"{len(base_debitos)} registros."
    )

    # ---------------------------------------------------------
    # Sprint 3.4 - Finalizacao integrada e auditavel
    # ---------------------------------------------------------

    from modules.incidentes.persistence import importar_auditoria

    resultado_publicacao = finalizar_publicacao_incidentes(
        arquivo_auditoria=arquivo_auditoria,
        arquivo_volume=arquivo_volume,
        importar_mysql=importar_mysql,
        total=total,
        extraidos=extraidos,
        sem_dados=sem_dados,
        falhas_extracao=falhas_extracao,
        importar_auditoria=importar_auditoria,
        importar_snapshot_validado=importar_snapshot_validado,
        incident_publisher_factory=IncidentPublisher,
        volume_publisher_factory=VolumePublisher,
        log=log,
    )

    log(
        "Resultado final da publicacao: "
        f"{resultado_publicacao}"
    )

    return arquivo_auditoria
