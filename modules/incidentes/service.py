from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from modules.incidentes.filters import (
    aplicar_filtros_dashboard,
    aplicar_filtros_volume,
    encontrar_coluna_logica,
    filtros_estao_ativos,
    gerar_todas_opcoes_filtro,
    normalizar_colunas,
    normalizar_valor_filtro,
    serie_data,
    serie_texto,
)
from operations.incidentes.analytics import (
    gerar_tipos_operacao,
)
from modules.incidentes.repository import (
    IncidentesRepository,
)
from modules.incidentes.schemas import (
    AttentionRecord,
    ChartData,
    ChartSeries,
    DashboardCharts,
    DashboardFilterOptions,
    DashboardFilters,
    DashboardKPIs,
    DashboardMeta,
    DashboardResponse,
    DebitRuleRow,
)
from modules.incidentes.serializers import (
    dataframe_para_registros,
)


class IncidentesService:
    def __init__(
        self,
        repository: Optional[
            IncidentesRepository
        ] = None,
    ):
        self.repository = (
            repository
            or IncidentesRepository()
        )

    # --------------------------------------------------
    # Dashboard principal
    # --------------------------------------------------

    def dashboard(
        self,
        filtros: Optional[
            DashboardFilters
        ] = None,
    ) -> DashboardResponse:
        filtros = filtros or DashboardFilters()

        snapshot = self.repository.snapshot_atual()

        if snapshot is None:
            raise FileNotFoundError(
                "Nenhum snapshot disponível "
                "para o dashboard."
            )

        auditoria_original = (
            self.repository.auditoria()
        )

        auditoria = aplicar_filtros_dashboard(
            auditoria_original,
            filtros,
        )

        volume_original = (
            self.repository.carregar_volume()
        )

        volume = aplicar_filtros_volume(
            volume_original,
            filtros,
        )

        opcoes = gerar_todas_opcoes_filtro(
            auditoria_original
        )

        return DashboardResponse(
            success=True,
            message="Dashboard carregado com sucesso.",
            meta=DashboardMeta(
                generated_at=datetime.now(),
                source_updated_at=datetime.fromtimestamp(
                    snapshot.atualizado_em
                ),
                total_before_filters=len(
                    auditoria_original
                ),
                total_after_filters=len(
                    auditoria
                ),
                filters_applied=(
                    filtros_estao_ativos(
                        filtros
                    )
                ),
                source_name=snapshot.nome,
            ),
            applied_filters=filtros,
            filter_options=(
                DashboardFilterOptions(
                    **opcoes
                )
            ),
            kpis=self._gerar_kpis(
                auditoria
            ),
            charts=self._gerar_graficos(
                auditoria,
                volume,
                filtros,
            ),
            rules=self._gerar_regras(
                auditoria
            ),
            attention_records=(
                self._gerar_registros_atencao(
                    auditoria
                )
            ),
        )

    # --------------------------------------------------
    # KPIs
    # --------------------------------------------------

    def _gerar_kpis(
        self,
        df: pd.DataFrame,
    ) -> DashboardKPIs:
        base = normalizar_colunas(df)

        total_registros = len(base)

        coluna_ctrc = self._encontrar_coluna(
            base,
            [
                "CTRC",
                "NUMERO_CTRC",
                "NUM_CTRC",
                "CONHECIMENTO",
            ],
        )

        coluna_valor = self._encontrar_coluna(
            base,
            [
                "VALOR_CARGA_CTE",
                "VALOR_CARGA",
                "VALOR_TOTAL_CARGA",
            ],
        )

        coluna_cliente = (
            encontrar_coluna_logica(
                base,
                "cliente",
            )
        )

        coluna_grupo = (
            encontrar_coluna_logica(
                base,
                "grupo_cliente",
            )
        )

        coluna_unidade = (
            encontrar_coluna_logica(
                base,
                "unidade",
            )
        )

        coluna_produto = (
            encontrar_coluna_logica(
                base,
                "produto",
            )
        )

        coluna_status = (
            encontrar_coluna_logica(
                base,
                "status_debito",
            )
        )

        coluna_xml = self._encontrar_coluna(
            base,
            [
                "STATUS_XML",
                "XML_STATUS",
                "STATUS_CTE_XML",
            ],
        )

        total_ctrcs = self._nunique(
            base,
            coluna_ctrc,
        )

        if total_ctrcs == 0:
            total_ctrcs = total_registros

        valor_total_carga = (
            self._somar_coluna_numerica(
                base,
                coluna_valor,
            )
        )

        contagem_status = (
            self._contar_valores(
                base,
                coluna_status,
            )
        )

        total_debitos = self._buscar_contagem(
            contagem_status,
            "DEBITO",
        )

        total_nao_debitos = (
            self._buscar_contagem(
                contagem_status,
                "NAO_DEBITO",
                "NÃO DÉBITO",
                "NAO DEBITO",
            )
        )

        total_pendentes = (
            self._buscar_contagem(
                contagem_status,
                "PENDENTE",
            )
        )

        total_sem_historico = (
            self._buscar_contagem(
                contagem_status,
                "SEM_HISTORICO",
                "SEM HISTÓRICO",
                "SEM HISTORICO",
            )
        )

        contagem_xml = self._contar_valores(
            base,
            coluna_xml,
        )

        xml_ok = self._buscar_contagem(
            contagem_xml,
            "OK",
            "SUCESSO",
            "PROCESSADO",
        )

        xml_com_erro = sum(
            quantidade
            for status, quantidade
            in contagem_xml.items()
            if status not in {
                "",
                "OK",
                "SUCESSO",
                "PROCESSADO",
            }
        )

        percentual_debitos = (
            round(
                (
                    total_debitos
                    / total_registros
                    * 100
                ),
                2,
            )
            if total_registros
            else 0.0
        )

        percentual_pendentes = (
            round(
                (
                    total_pendentes
                    / total_registros
                    * 100
                ),
                2,
            )
            if total_registros
            else 0.0
        )

        return DashboardKPIs(
            total_ctrcs=total_ctrcs,
            total_registros=total_registros,
            valor_total_carga=round(
                valor_total_carga,
                2,
            ),
            total_debitos=total_debitos,
            total_nao_debitos=(
                total_nao_debitos
            ),
            total_pendentes=total_pendentes,
            total_sem_historico=(
                total_sem_historico
            ),
            clientes_distintos=self._nunique(
                base,
                coluna_cliente,
            ),
            grupos_clientes_distintos=(
                self._nunique(
                    base,
                    coluna_grupo,
                )
            ),
            unidades_distintas=self._nunique(
                base,
                coluna_unidade,
            ),
            produtos_distintos=self._nunique(
                base,
                coluna_produto,
            ),
            xml_ok=xml_ok,
            xml_com_erro=xml_com_erro,
            percentual_debitos=(
                percentual_debitos
            ),
            percentual_pendentes=(
                percentual_pendentes
            ),
        )

    # --------------------------------------------------
    # Gráficos
    # --------------------------------------------------

    def _gerar_graficos(
        self,
        df: pd.DataFrame,
        volume: pd.DataFrame,
        filtros: DashboardFilters,
    ) -> DashboardCharts:
        return DashboardCharts(
            evolucao_diaria=(
                self._grafico_evolucao_diaria(
                    df
                )
            ),
            evolucao_mensal=(
                self._grafico_evolucao_mensal(
                    df
                )
            ),
            status_debitos=(
                self._grafico_status_debitos(
                    df
                )
            ),
            ranking_ocorrencias=(
                self._grafico_ranking(
                    df,
                    nome_logico=(
                        "codigo_ocorrencia"
                    ),
                    titulo=(
                        "Ranking de ocorrências"
                    ),
                    categoria="ocorrencia",
                    limite=10,
                )
            ),
            taxa_por_mil=(
                self._grafico_taxa_por_mil(
                    df,
                    volume,
                    filtros,
                )
            ),
            ranking_grupos_clientes=(
                self._grafico_ranking(
                    df,
                    nome_logico=(
                        "grupo_cliente"
                    ),
                    titulo=(
                        "Ranking de grupos "
                        "de clientes"
                    ),
                    categoria="grupo_cliente",
                    limite=10,
                )
            ),
            ranking_clientes=(
                self._grafico_ranking(
                    df,
                    nome_logico="cliente",
                    titulo=(
                        "Ranking de clientes"
                    ),
                    categoria="cliente",
                    limite=10,
                )
            ),
            ranking_unidades=(
                self._grafico_ranking(
                    df,
                    nome_logico="unidade",
                    titulo=(
                        "Ranking de unidades"
                    ),
                    categoria="unidade",
                    limite=10,
                )
            ),
            ranking_produtos=(
                self._grafico_ranking(
                    df,
                    nome_logico="produto",
                    titulo=(
                        "Ranking de produtos"
                    ),
                    categoria="produto",
                    limite=10,
                )
            ),
            tipos_operacao=(
                self._grafico_tipos_operacao(df)
            ),
        )

    def _grafico_tipos_operacao(
        self,
        df: pd.DataFrame,
    ) -> ChartData:
        analise = gerar_tipos_operacao(df)

        rows = []

        for _, linha in analise.iterrows():
            rows.append(
                {
                    "tipo_operacao": str(
                        linha["TIPO_OPERACAO"]
                    ),
                    "quantidade": int(
                        linha["QUANTIDADE"]
                    ),
                    "percentual": float(
                        linha["PERCENTUAL"]
                    ),
                }
            )

        return ChartData(
            title="Natureza da operação",
            category_key="tipo_operacao",
            series=[
                ChartSeries(
                    key="quantidade",
                    label="CTRCs",
                )
            ],
            rows=rows,
        )

    def _grafico_taxa_por_mil(
        self,
        incidentes: pd.DataFrame,
        volume: pd.DataFrame,
        filtros: DashboardFilters,
    ) -> ChartData:
        base_incidentes = normalizar_colunas(
            incidentes
        )

        base_volume = normalizar_colunas(
            volume
        )

        coluna_ctrc_incidente = (
            self._encontrar_coluna(
                base_incidentes,
                [
                    "CTRC",
                    "AN_CTRC",
                    "NUMERO_CTRC",
                    "NUM_CTRC",
                    "CONHECIMENTO",
                ],
            )
        )

        coluna_ocorrencia = (
            encontrar_coluna_logica(
                base_incidentes,
                "codigo_ocorrencia",
            )
        )

        coluna_ctrc_volume = (
            self._encontrar_coluna(
                base_volume,
                [
                    "CTRC",
                    "NUMERO_CTRC",
                    "NUM_CTRC",
                    "CONHECIMENTO",
                ],
            )
        )

        if (
            base_incidentes.empty
            or base_volume.empty
            or coluna_ctrc_incidente is None
            or coluna_ocorrencia is None
            or coluna_ctrc_volume is None
        ):
            return ChartData(
                title="Taxa por Mil",
                category_key="ocorrencia",
                series=[
                    ChartSeries(
                        key="taxa_por_mil",
                        label="Taxa por mil",
                    )
                ],
                rows=[],
            )

        # --------------------------------------------------
        # Denominador
        # --------------------------------------------------

        ctrcs_volume = (
            serie_texto(
                base_volume,
                coluna_ctrc_volume,
            )
        )

        ctrcs_volume = ctrcs_volume[
            ctrcs_volume.ne("")
            & ctrcs_volume.str.lower().ne("nan")
            & ctrcs_volume.str.lower().ne("none")
        ]

        total_ctrcs_emitidos = int(
            ctrcs_volume.nunique()
        )

        if total_ctrcs_emitidos <= 0:
            return ChartData(
                title="Taxa por Mil",
                category_key="ocorrencia",
                series=[
                    ChartSeries(
                        key="taxa_por_mil",
                        label="Taxa por mil",
                    )
                ],
                rows=[],
            )

        # --------------------------------------------------
        # Numerador
        # --------------------------------------------------

        temporaria = pd.DataFrame(
            {
                "CTRC": serie_texto(
                    base_incidentes,
                    coluna_ctrc_incidente,
                ),
                "OCORRENCIA": serie_texto(
                    base_incidentes,
                    coluna_ocorrencia,
                ),
            }
        )

        temporaria = temporaria[
            temporaria["CTRC"].ne("")
            & temporaria["OCORRENCIA"].ne("")
        ].copy()

        # Um mesmo CTRC conta uma única vez
        # dentro de cada ocorrência.
        temporaria = (
            temporaria.drop_duplicates(
                subset=[
                    "CTRC",
                    "OCORRENCIA",
                ]
            )
        )

        agrupado = (
            temporaria
            .groupby(
                "OCORRENCIA",
                dropna=False,
            )
            .agg(
                QUANTIDADE=(
                    "CTRC",
                    "nunique",
                )
            )
            .reset_index()
        )

        agrupado["TAXA_POR_MIL"] = (
            agrupado["QUANTIDADE"]
            / total_ctrcs_emitidos
            * 1000
        )

        agrupado = (
            agrupado
            .sort_values(
                by=[
                    "TAXA_POR_MIL",
                    "QUANTIDADE",
                ],
                ascending=[
                    False,
                    False,
                ],
            )
            .head(10)
        )

        rows = []

        for _, linha in agrupado.iterrows():
            rows.append(
                {
                    "ocorrencia": str(
                        linha["OCORRENCIA"]
                    ),
                    "quantidade": int(
                        linha["QUANTIDADE"]
                    ),
                    "taxa_por_mil": round(
                        float(
                            linha[
                                "TAXA_POR_MIL"
                            ]
                        ),
                        2,
                    ),
                    "total_ctrcs_emitidos": (
                        total_ctrcs_emitidos
                    ),
                }
            )

        return ChartData(
            title="Taxa por Mil",
            category_key="ocorrencia",
            series=[
                ChartSeries(
                    key="taxa_por_mil",
                    label="Taxa por mil",
                )
            ],
            rows=rows,
        )

    def _grafico_status_debitos(
        self,
        df: pd.DataFrame,
    ) -> ChartData:
        base = normalizar_colunas(df)

        coluna = encontrar_coluna_logica(
            base,
            "status_debito",
        )

        rows = self._ranking_coluna(
            base,
            coluna,
            categoria="status",
            limite=None,
        )

        return ChartData(
            title="Status dos débitos",
            category_key="status",
            series=[
                ChartSeries(
                    key="quantidade",
                    label="Quantidade",
                )
            ],
            rows=rows,
        )

    def _grafico_ranking(
        self,
        df: pd.DataFrame,
        nome_logico: str,
        titulo: str,
        categoria: str,
        limite: Optional[int],
    ) -> ChartData:
        base = normalizar_colunas(df)

        coluna = encontrar_coluna_logica(
            base,
            nome_logico,
        )

        rows = self._ranking_coluna(
            base,
            coluna,
            categoria=categoria,
            limite=limite,
        )

        return ChartData(
            title=titulo,
            category_key=categoria,
            series=[
                ChartSeries(
                    key="quantidade",
                    label="Quantidade",
                ),
                ChartSeries(
                    key="percentual",
                    label="Percentual",
                    value_type="percent",
                ),
            ],
            rows=rows,
        )

    def _grafico_evolucao_diaria(
        self,
        df: pd.DataFrame,
    ) -> ChartData:
        return self._grafico_evolucao(
            df=df,
            frequencia="D",
            formato="%d/%m/%Y",
            titulo="Evolução diária",
            categoria="data",
        )

    def _grafico_evolucao_mensal(
        self,
        df: pd.DataFrame,
    ) -> ChartData:
        return self._grafico_evolucao(
            df=df,
            frequencia="ME",
            formato="%m/%Y",
            titulo="Evolução mensal",
            categoria="mes",
        )

    def _grafico_evolucao(
        self,
        df: pd.DataFrame,
        frequencia: str,
        formato: str,
        titulo: str,
        categoria: str,
    ) -> ChartData:
        base = normalizar_colunas(df)

        coluna_data = (
            encontrar_coluna_logica(
                base,
                "data",
            )
        )

        if coluna_data is None:
            rows: List[Dict[str, Any]] = []
        else:
            datas = serie_data(
                base,
                coluna_data,
            )

            validos = datas.dropna()

            if validos.empty:
                rows = []
            else:
                agrupamento = (
                    validos
                    .dt.to_period(
                        "D"
                        if frequencia == "D"
                        else "M"
                    )
                    .value_counts()
                    .sort_index()
                )

                rows = []

                for periodo, quantidade in (
                    agrupamento.items()
                ):
                    timestamp = (
                        periodo.to_timestamp()
                    )

                    rows.append(
                        {
                            categoria: (
                                timestamp.strftime(
                                    formato
                                )
                            ),
                            "quantidade": int(
                                quantidade
                            ),
                        }
                    )

        return ChartData(
            title=titulo,
            category_key=categoria,
            series=[
                ChartSeries(
                    key="quantidade",
                    label="Quantidade",
                )
            ],
            rows=rows,
        )

    # --------------------------------------------------
    # Regras
    # --------------------------------------------------

    def _gerar_regras(
        self,
        df: pd.DataFrame,
    ) -> List[DebitRuleRow]:
        base = normalizar_colunas(df)

        coluna_regra = (
            encontrar_coluna_logica(
                base,
                "regra_debito",
            )
        )

        coluna_valor = self._encontrar_coluna(
            base,
            [
                "VALOR_CARGA_CTE",
                "VALOR_CARGA",
                "VALOR_TOTAL_CARGA",
            ],
        )

        if coluna_regra is None:
            return []

        regras = serie_texto(
            base,
            coluna_regra,
        )

        base_temporaria = pd.DataFrame(
            {
                "REGRA": regras,
            }
        )

        if coluna_valor is not None:
            base_temporaria["VALOR"] = (
                self._serie_numerica(
                    base[coluna_valor]
                )
            )
        else:
            base_temporaria["VALOR"] = 0.0

        base_temporaria = (
            base_temporaria[
                base_temporaria[
                    "REGRA"
                ].ne("")
            ]
        )

        if base_temporaria.empty:
            return []

        agrupado = (
            base_temporaria
            .groupby(
                "REGRA",
                dropna=False,
            )
            .agg(
                QUANTIDADE=(
                    "REGRA",
                    "size",
                ),
                VALOR_CARGA=(
                    "VALOR",
                    "sum",
                ),
            )
            .reset_index()
            .sort_values(
                by="QUANTIDADE",
                ascending=False,
            )
        )

        total = int(
            agrupado[
                "QUANTIDADE"
            ].sum()
        )

        agrupado["PERCENTUAL"] = (
            agrupado["QUANTIDADE"]
            / total
            * 100
            if total
            else 0
        )

        agrupado[
            "PERCENTUAL_ACUMULADO"
        ] = agrupado[
            "PERCENTUAL"
        ].cumsum()

        retorno: List[DebitRuleRow] = []

        for linha in dataframe_para_registros(
            agrupado
        ):
            regra = str(
                linha.get(
                    "REGRA",
                    "",
                )
            )

            retorno.append(
                DebitRuleRow(
                    regra=regra,
                    descricao=self._descricao_regra(
                        regra
                    ),
                    quantidade=int(
                        linha.get(
                            "QUANTIDADE",
                            0,
                        )
                        or 0
                    ),
                    percentual=round(
                        float(
                            linha.get(
                                "PERCENTUAL",
                                0,
                            )
                            or 0
                        ),
                        2,
                    ),
                    percentual_acumulado=round(
                        float(
                            linha.get(
                                "PERCENTUAL_ACUMULADO",
                                0,
                            )
                            or 0
                        ),
                        2,
                    ),
                    valor_carga=round(
                        float(
                            linha.get(
                                "VALOR_CARGA",
                                0,
                            )
                            or 0
                        ),
                        2,
                    ),
                )
            )

        return retorno

    # --------------------------------------------------
    # Registros de atenção
    # --------------------------------------------------

    def _gerar_registros_atencao(
        self,
        df: pd.DataFrame,
        limite: int = 100,
    ) -> List[AttentionRecord]:
        base = normalizar_colunas(df)

        coluna_status = (
            encontrar_coluna_logica(
                base,
                "status_debito",
            )
        )

        if coluna_status is None:
            return []

        status = (
            serie_texto(
                base,
                coluna_status,
            )
            .map(normalizar_valor_filtro)
        )

        mascara = status.isin(
            {
                "pendente",
                "sem_historico",
            }
        )

        pendencias = base[mascara].head(
            limite
        )

        retorno: List[AttentionRecord] = []

        for _, linha in pendencias.iterrows():
            retorno.append(
                AttentionRecord(
                    ctrc=self._valor_linha(
                        linha,
                        [
                            "CTRC",
                            "NUMERO_CTRC",
                            "NUM_CTRC",
                        ],
                    ),
                    numero_nota=self._valor_linha(
                        linha,
                        [
                            "NRO_NOTA_FISCAL",
                            "NUMERO_NOTA_FISCAL",
                            "NUMERO_NOTA",
                            "NOTA_FISCAL",
                            "NF",
                        ],
                    ),
                    data_inclusao_ctrc=self._data_linha(
                        linha,
                        [
                            "EMISSAO_CTRC",
                            "DATA_EMISSAO_CTRC",
                            "DATA_INCLUSAO_CTRC",
                            "DATA_CTRC",
                        ],
                    ),
                    data_ocorrencia=(
                        self._data_linha(
                            linha,
                            [
                                "DATA_OCOR",
                                "DATA_OCORRENCIA",
                                "DIA_INCLUSAO_OCOR",
                                "DATA",
                            ],
                        )
                    ),
                    grupo_cliente=(
                        self._valor_logico_linha(
                            linha,
                            "grupo_cliente",
                        )
                    ),
                    cliente=(
                        self._valor_logico_linha(
                            linha,
                            "cliente",
                        )
                    ),
                    unidade=(
                        self._valor_logico_linha(
                            linha,
                            "unidade",
                        )
                    ),
                    tipo_operacao=(
                        self._valor_logico_linha(
                            linha,
                            "tipo_operacao",
                        )
                    ),
                    codigo_ocorrencia=(
                        self._valor_logico_linha(
                            linha,
                            "codigo_ocorrencia",
                        )
                    ),
                    descricao_ocorrencia=(
                        self._valor_linha(
                            linha,
                            [
                                "DESC_OCOR",
                                "DESCRICAO_OCORRENCIA",
                                "DESCRICAO_OCOR",
                            ],
                        )
                    ),
                    status_debito=(
                        self._valor_logico_linha(
                            linha,
                            "status_debito",
                        )
                    ),
                    regra_debito=(
                        self._valor_logico_linha(
                            linha,
                            "regra_debito",
                        )
                    ),
                    produto=(
                        self._valor_logico_linha(
                            linha,
                            "produto",
                        )
                    ),
                    valor_carga=(
                        self._numero_linha(
                            linha,
                            [
                                "VALOR_CARGA_CTE",
                                "VALOR_CARGA",
                                "VALOR_TOTAL_CARGA",
                            ],
                        )
                    ),
                    status_xml=self._valor_linha(
                        linha,
                        [
                            "STATUS_XML",
                            "XML_STATUS",
                        ],
                    ),
                    mensagem=self._valor_linha(
                        linha,
                        [
                            "MENSAGEM",
                            "MOTIVO",
                            "OBSERVACAO",
                        ],
                    ),
                )
            )

        return retorno

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    @staticmethod
    def _encontrar_coluna(
        df: pd.DataFrame,
        aliases: List[str],
    ) -> Optional[str]:
        for alias in aliases:
            if alias in df.columns:
                return alias

        return None

    @staticmethod
    def _serie_numerica(
        serie: pd.Series,
    ) -> pd.Series:
        if pd.api.types.is_numeric_dtype(
            serie
        ):
            return pd.to_numeric(
                serie,
                errors="coerce",
            ).fillna(0.0)

        texto = (
            serie
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(
                "R$",
                "",
                regex=False,
            )
            .str.replace(
                " ",
                "",
                regex=False,
            )
        )

        possui_virgula = texto.str.contains(
            ",",
            regex=False,
        )

        texto.loc[possui_virgula] = (
            texto.loc[possui_virgula]
            .str.replace(
                ".",
                "",
                regex=False,
            )
            .str.replace(
                ",",
                ".",
                regex=False,
            )
        )

        return pd.to_numeric(
            texto,
            errors="coerce",
        ).fillna(0.0)

    def _somar_coluna_numerica(
        self,
        df: pd.DataFrame,
        coluna: Optional[str],
    ) -> float:
        if coluna is None:
            return 0.0

        return float(
            self._serie_numerica(
                df[coluna]
            ).sum()
        )

    @staticmethod
    def _nunique(
        df: pd.DataFrame,
        coluna: Optional[str],
    ) -> int:
        if coluna is None:
            return 0

        valores = serie_texto(
            df,
            coluna,
        )

        valores = valores[
            valores.ne("")
            & valores.str.lower().ne("nan")
            & valores.str.lower().ne("none")
        ]

        return int(valores.nunique())

    @staticmethod
    def _contar_valores(
        df: pd.DataFrame,
        coluna: Optional[str],
    ) -> Dict[str, int]:
        if coluna is None:
            return {}

        serie = (
            serie_texto(
                df,
                coluna,
            )
            .map(normalizar_valor_filtro)
        )

        serie = serie[
            serie.ne("")
        ]

        return {
            str(chave): int(valor)
            for chave, valor in (
                serie.value_counts().items()
            )
        }

    @staticmethod
    def _buscar_contagem(
        contagens: Dict[str, int],
        *valores: str,
    ) -> int:
        chaves = {
            normalizar_valor_filtro(valor)
            for valor in valores
        }

        return sum(
            contagens.get(chave, 0)
            for chave in chaves
        )

    @staticmethod
    def _ranking_coluna(
        df: pd.DataFrame,
        coluna: Optional[str],
        categoria: str,
        limite: Optional[int],
    ) -> List[Dict[str, Any]]:
        if coluna is None:
            return []

        serie = serie_texto(
            df,
            coluna,
        )

        serie = serie[
            serie.ne("")
            & serie.str.lower().ne("nan")
            & serie.str.lower().ne("none")
        ]

        if serie.empty:
            return []

        contagem = (
            serie
            .value_counts()
            .rename_axis(categoria)
            .reset_index(
                name="quantidade"
            )
        )

        total = int(
            contagem[
                "quantidade"
            ].sum()
        )

        contagem["percentual"] = (
            contagem["quantidade"]
            / total
            * 100
            if total
            else 0
        )

        if limite is not None:
            contagem = contagem.head(
                limite
            )

        contagem["percentual"] = (
            contagem["percentual"]
            .round(2)
        )

        return dataframe_para_registros(
            contagem
        )

    @staticmethod
    def _acao_regra(
        regra: str,
    ) -> str:
        regra_normalizada = (
            str(regra)
            .strip()
            .upper()
        )

        mapa = {
            "CODIGO_CONFIRMADO": (
                "Manter a classificação como débito."
            ),
            "CODIGO_NAO_PARAMETRIZADO": (
                "Revisar o código de ocorrência e "
                "incluir na parametrização."
            ),
            "OCORRENCIA_DIVERGENTE": (
                "Validar o histórico e a ocorrência."
            ),
            "SEM_HISTORICO": (
                "Consultar o histórico no SSW."
            ),
        }

        return mapa.get(
            regra_normalizada,
            "Revisar o registro manualmente.",
        )

    @staticmethod
    def _valor_linha(
        linha: pd.Series,
        aliases: List[str],
    ) -> str:
        for alias in aliases:
            if alias not in linha.index:
                continue

            valor = linha.get(alias)

            if pd.isna(valor):
                continue

            texto = str(valor).strip()

            if texto:
                return texto

        return ""

    def _valor_logico_linha(
        self,
        linha: pd.Series,
        nome_logico: str,
    ) -> str:
        aliases = {
            "grupo_cliente": [
                "GRUPO_CLIENTE",
                "NOME_GRUPO_CLIENTE",
                "GRUPO_ECONOMICO",
            ],
            "cliente": [
                "NOME_PAGADOR",
                "CLIENTE",
                "NOME_CLIENTE",
                "PAGADOR",
            ],
            "unidade": [
                "UNID_OCOR",
                "UNIDADE_OCOR",
                "UNIDADE",
            ],
            "tipo_operacao": [
                "TIPO_OPERACAO",
                "AN_TIPO_OPERACAO",
                "TIPO_DE_OPERACAO",
                "OPERACAO",
            ],
            "codigo_ocorrencia": [
                "COD_OCOR",
                "CODIGO_OCORRENCIA",
            ],
            "status_debito": [
                "STATUS_VALIDACAO_DEBITO",
                "STATUS_DEBITO",
            ],
            "regra_debito": [
                "REGRA_VALIDACAO_DEBITO",
                "REGRA_DEBITO",
            ],
            "produto": [
                "DESCRICAO_PRODUTO_PREDOMINANTE",
                "PRODUTO_PREDOMINANTE",
                "PRODUTO",
            ],
        }

        return self._valor_linha(
            linha,
            aliases.get(
                nome_logico,
                [],
            ),
        )

    @staticmethod
    def _data_linha(
        linha: pd.Series,
        aliases: List[str],
    ):
        for alias in aliases:
            if alias not in linha.index:
                continue

            valor = pd.to_datetime(
                linha.get(alias),
                errors="coerce",
                dayfirst=True,
            )

            if pd.notna(valor):
                return valor.date()

        return None

    @staticmethod
    def _descricao_regra(
        regra: str,
    ) -> str:
        mapa = {
            "CODIGO_CONFIRMADO": (
                "Código classificado como débito"
            ),
            "CODIGO_NAO_PARAMETRIZADO": (
                "Código ainda não parametrizado"
            ),
            "OCORRENCIA_DIVERGENTE": (
                "Ocorrência divergente do histórico"
            ),
            "SEM_HISTORICO": (
                "Histórico não encontrado"
            ),
        }

        regra_normalizada = (
            str(regra)
            .strip()
            .upper()
        )

        return mapa.get(
            regra_normalizada,
            regra_normalizada
            .replace("_", " ")
            .title(),
        )

    def _numero_linha(
        self,
        linha: pd.Series,
        aliases: List[str],
    ) -> float:
        for alias in aliases:
            if alias not in linha.index:
                continue

            serie = pd.Series(
                [linha.get(alias)]
            )

            valor = self._serie_numerica(
                serie
            ).iloc[0]

            return round(
                float(valor),
                2,
            )

        return 0.0