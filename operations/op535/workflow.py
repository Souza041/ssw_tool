from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from pathlib import Path

from operations.op535.cache import OP535Cache
from operations.op535.brasil_api import (
    BrasilAPI,
    BrasilAPIRateLimit,
    classificar_regime,
    extrair_dados_cache,
)
from operations.op535.parser import (
    classificar_documento,
    ler_fornecedores,
    somente_digitos,
)
from operations.op535.report import OP535Report
from ssw.client import SSWClient


def executar_op535(
    client: SSWClient,
    output_dir: Path,
    timeout_seconds: int = 300,
    job=None,
) -> Path:
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if job:
        from web.jobs import add_log, set_progress

        add_log(
            job,
            "Abrindo OP535 e solicitando relatório.",
        )

    op535 = OP535Report(client)

    arquivo_origem = op535.gerar_e_baixar(
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
    )

    if job:
        add_log(
            job,
            f"Relatório OP535 baixado: "
            f"{arquivo_origem.name}",
        )

        add_log(
            job,
            "Lendo fornecedores e separando "
            "CPF de CNPJ.",
        )

    colunas, registros = ler_fornecedores(
        arquivo_origem
    )

    empresas = []
    cpfs = 0
    invalidos = 0

    for registro in registros:
        documento = registro.get(
            "CPF/CNPJ",
            "",
        )

        tipo = classificar_documento(documento)

        if tipo == "CPF":
            cpfs += 1
            continue

        if tipo != "CNPJ":
            invalidos += 1
            continue

        registro["CPF/CNPJ"] = somente_digitos(
            documento
        )

        empresas.append(registro)

    cnpjs_unicos = sorted({
        registro["CPF/CNPJ"]
        for registro in empresas
    })

    # --------------------------------------------------
    # TESTE TEMPORÁRIO
    # --------------------------------------------------
    # Depois que validarmos o cache, isso vira:
    #
    # cnpjs_consulta = cnpjs_unicos
    #
    cnpjs_consulta = cnpjs_unicos

    if job:
        add_log(
            job,
            f"Total recebido: {len(registros)}.",
        )

        add_log(
            job,
            f"CPFs descartados: {cpfs}.",
        )

        add_log(
            job,
            f"Registros inválidos: {invalidos}.",
        )

        add_log(
            job,
            f"CNPJs encontrados: {len(empresas)}.",
        )

        add_log(
            job,
            (
                f"CNPJs únicos selecionados: "
                f"{len(cnpjs_consulta)}."
            ),
        )

    # --------------------------------------------------
    # CACHE
    # --------------------------------------------------

    cache = OP535Cache()

    cache_encontrado = cache.buscar_varios(
        cnpjs_consulta
    )

    regimes = {}

    # Tudo que já existe no banco entra imediatamente
    # no dicionário de regimes.
    for cnpj, registro_cache in cache_encontrado.items():
        status = registro_cache.get(
            "status_consulta"
        )

        if status == "NAO_ENCONTRADO":
            regimes[cnpj] = "CNPJ não encontrado"
        else:
            regimes[cnpj] = (
                registro_cache.get(
                    "regime_tributario"
                )
                or "Não identificado"
            )

    # Só consultamos externamente quem NÃO está
    # persistido no cache.
    cnpjs_pendentes = [
        cnpj
        for cnpj in cnpjs_consulta
        if cnpj not in cache_encontrado
    ]

    if job:
        add_log(
            job,
            (
                f"Encontrados no cache: "
                f"{len(cache_encontrado)}."
            ),
        )

        add_log(
            job,
            (
                f"Pendentes na BrasilAPI: "
                f"{len(cnpjs_pendentes)}."
            ),
        )

        set_progress(
            job,
            len(cache_encontrado),
            len(cnpjs_consulta),
        )

    # --------------------------------------------------
    # BRASIL API
    # --------------------------------------------------

    api = BrasilAPI()

    for indice, cnpj in enumerate(
        cnpjs_pendentes,
        start=1,
    ):

        try:
            retorno = api.consultar(cnpj)

        except BrasilAPIRateLimit as exc:
            if job:
                add_log(
                    job,
                    (
                        "BrasilAPI bloqueou novas consultas "
                        f"por limite de requisições: {exc}"
                    ),
                )

            raise RuntimeError(
                "Limite de requisições da BrasilAPI "
                "atingido. Os resultados já consultados "
                "foram preservados no cache."
            )

        status = retorno.get("status")

        if status == "OK":
            dados = retorno["dados"]

            regime = classificar_regime(
                dados
            )

            dados_cache = extrair_dados_cache(
                dados
            )

            # Persiste IMEDIATAMENTE.
            cache.salvar(
                cnpj=cnpj,
                razao_social=dados_cache[
                    "razao_social"
                ],
                regime_tributario=regime,
                ano_regime=dados_cache[
                    "ano_regime"
                ],
                optante_simples=dados_cache[
                    "optante_simples"
                ],
                optante_mei=dados_cache[
                    "optante_mei"
                ],
                status_consulta="OK",
            )

        elif status == "NAO_ENCONTRADO":
            regime = "CNPJ não encontrado"

            # 404 também fica persistido para não
            # consultar novamente em toda execução.
            cache.salvar_nao_encontrado(
                cnpj
            )

        else:
            regime = "Erro na consulta"

            erro = retorno.get(
                "erro",
                "Erro não informado",
            )

            if job:
                add_log(
                    job,
                    (
                        f"[ERRO] {cnpj} → "
                        f"{erro}"
                    ),
                )

        regimes[cnpj] = regime

        concluidos = (
            len(cache_encontrado)
            + indice
        )

        if job:
            set_progress(
                job,
                concluidos,
                len(cnpjs_consulta),
            )

            if (
                indice == 1
                or indice % 100 == 0
                or indice == len(cnpjs_pendentes)
            ):
                add_log(
                    job,
                    (
                        f"BrasilAPI: "
                        f"{indice}/{len(cnpjs_pendentes)} "
                        f"consultas concluídas."
                    ),
                )

    if job:
        if not cnpjs_pendentes:
            add_log(
                job,
                (
                    "Todos os CNPJs selecionados já estavam "
                    "no cache. Nenhuma consulta à BrasilAPI "
                    "foi necessária."
                ),
            )
        else:
            add_log(
                job,
                (
                    f"Consulta concluída. "
                    f"Cache: {len(cache_encontrado)} | "
                    f"BrasilAPI: {len(cnpjs_pendentes)}."
                ),
            )

    colunas_saida = list(colunas)

    indice_documento = colunas_saida.index(
        "CPF/CNPJ"
    )

    colunas_saida[indice_documento] = "CNPJ"

    colunas_saida.insert(
        indice_documento + 1,
        "Regime Tributário",
    )

    arquivo_saida = (
        output_dir
        / "OP535_REGIME_TRIBUTARIO.xlsx"
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Fornecedores"

    # --------------------------------------------------
    # CABEÇALHO
    # --------------------------------------------------

    for coluna_idx, nome_coluna in enumerate(
        colunas_saida,
        start=1,
    ):
        cell = ws.cell(
            row=1,
            column=coluna_idx,
            value=nome_coluna,
        )

        cell.font = Font(
            bold=True,
            color="FFFFFF",
        )

        cell.fill = PatternFill(
            fill_type="solid",
            fgColor="1D4ED8",
        )

        cell.alignment = Alignment(
            vertical="center",
        )

    # --------------------------------------------------
    # DADOS
    # --------------------------------------------------

    for linha_idx, registro in enumerate(
        empresas,
        start=2,
    ):
        linha = dict(registro)

        cnpj = linha.pop(
            "CPF/CNPJ"
        )

        linha["CNPJ"] = cnpj

        linha["Regime Tributário"] = (
            regimes.get(
                cnpj,
                "Não consultado",
            )
        )

        for coluna_idx, nome_coluna in enumerate(
            colunas_saida,
            start=1,
        ):
            valor = linha.get(
                nome_coluna,
                "",
            )

            cell = ws.cell(
                row=linha_idx,
                column=coluna_idx,
                value=valor,
            )

            # Importantíssimo:
            # CNPJ fica como TEXTO.
            #
            # Senão o Excel pode fazer cagada com
            # zeros à esquerda.
            if nome_coluna == "CNPJ":
                cell.number_format = "@"

    # --------------------------------------------------
    # UX DA PLANILHA
    # --------------------------------------------------

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # Largura automática com limite para não criar
    # colunas absurdamente grandes.
    for coluna_idx, nome_coluna in enumerate(
        colunas_saida,
        start=1,
    ):
        maior = len(str(nome_coluna))

        for row in ws.iter_rows(
            min_row=2,
            max_row=min(
                ws.max_row,
                500,
            ),
            min_col=coluna_idx,
            max_col=coluna_idx,
        ):
            valor = row[0].value

            if valor is not None:
                maior = max(
                    maior,
                    len(str(valor)),
                )

        ws.column_dimensions[
            get_column_letter(coluna_idx)
        ].width = min(
            maior + 2,
            45,
        )

    wb.save(
        arquivo_saida
    )

    if job:
        add_log(
            job,
            f"Relatório final gerado: "
            f"{arquivo_saida.name}",
        )

    return arquivo_saida