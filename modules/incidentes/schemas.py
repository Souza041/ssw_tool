from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class DashboardFilters(BaseModel):
    """
    Filtros recebidos pelos endpoints do dashboard.

    Todos os campos são opcionais. Quando uma lista estiver vazia,
    o filtro correspondente não deve restringir os dados.
    """

    data_inicial: Optional[date] = None
    data_final: Optional[date] = None

    grupos_clientes: List[str] = Field(default_factory=list)
    clientes: List[str] = Field(default_factory=list)
    unidades: List[str] = Field(default_factory=list)
    codigos_ocorrencia: List[str] = Field(default_factory=list)
    status_debito: List[str] = Field(default_factory=list)
    regras_debito: List[str] = Field(default_factory=list)
    produtos: List[str] = Field(default_factory=list)

    @field_validator(
        "grupos_clientes",
        "clientes",
        "unidades",
        "codigos_ocorrencia",
        "status_debito",
        "regras_debito",
        "produtos",
        mode="before",
    )
    @classmethod
    def normalizar_listas(
        cls,
        valor: Any,
    ) -> List[str]:
        """
        Aceita:
        - None;
        - uma string única;
        - string separada por vírgulas;
        - lista de strings.
        """
        if valor is None:
            return []

        if isinstance(valor, str):
            itens = valor.split(",")
        elif isinstance(valor, (list, tuple, set)):
            itens = list(valor)
        else:
            itens = [valor]

        resultado: List[str] = []

        for item in itens:
            texto = str(item).strip()

            if texto and texto not in resultado:
                resultado.append(texto)

        return resultado

    @field_validator(
        "data_final",
    )
    @classmethod
    def validar_data_final(
        cls,
        data_final: Optional[date],
        info,
    ) -> Optional[date]:
        data_inicial = info.data.get("data_inicial")

        if (
            data_inicial is not None
            and data_final is not None
            and data_final < data_inicial
        ):
            raise ValueError(
                "A data final não pode ser anterior "
                "à data inicial."
            )

        return data_final


class FilterOption(BaseModel):
    """
    Opção apresentada em selects ou multiselects.
    """

    value: str
    label: str
    count: int = 0


class DashboardFilterOptions(BaseModel):
    """
    Valores disponíveis para preenchimento dos filtros.
    """

    grupos_clientes: List[FilterOption] = Field(
        default_factory=list
    )

    clientes: List[FilterOption] = Field(
        default_factory=list
    )

    unidades: List[FilterOption] = Field(
        default_factory=list
    )

    codigos_ocorrencia: List[FilterOption] = Field(
        default_factory=list
    )

    status_debito: List[FilterOption] = Field(
        default_factory=list
    )

    regras_debito: List[FilterOption] = Field(
        default_factory=list
    )

    produtos: List[FilterOption] = Field(
        default_factory=list
    )


class DashboardKPIs(BaseModel):
    """
    Indicadores principais do dashboard.
    """

    total_ctrcs: int = 0
    total_registros: int = 0

    valor_total_carga: float = 0.0

    total_debitos: int = 0
    total_nao_debitos: int = 0
    total_pendentes: int = 0
    total_sem_historico: int = 0

    clientes_distintos: int = 0
    grupos_clientes_distintos: int = 0
    unidades_distintas: int = 0
    produtos_distintos: int = 0

    xml_ok: int = 0
    xml_com_erro: int = 0

    percentual_debitos: float = 0.0
    percentual_pendentes: float = 0.0


class ChartSeries(BaseModel):
    """
    Série utilizada por gráficos com uma ou mais métricas.
    """

    key: str
    label: str
    value_type: str = "number"


class ChartData(BaseModel):
    """
    Estrutura genérica para gráficos do dashboard.

    Exemplo:

    {
        "title": "Ranking de unidades",
        "category_key": "unidade",
        "series": [
            {
                "key": "quantidade",
                "label": "Quantidade"
            }
        ],
        "rows": [
            {
                "unidade": "GRU",
                "quantidade": 25
            }
        ]
    }
    """

    title: str
    category_key: str

    series: List[ChartSeries] = Field(
        default_factory=list
    )

    rows: List[Dict[str, Any]] = Field(
        default_factory=list
    )


class DashboardCharts(BaseModel):
    """
    Conjunto de gráficos retornados pelo dashboard.
    """

    evolucao_diaria: Optional[ChartData] = None
    evolucao_mensal: Optional[ChartData] = None

    status_debitos: Optional[ChartData] = None
    ranking_ocorrencias: Optional[ChartData] = None
    ranking_grupos_clientes: Optional[ChartData] = None
    ranking_clientes: Optional[ChartData] = None
    ranking_unidades: Optional[ChartData] = None
    ranking_produtos: Optional[ChartData] = None


class DebitRuleRow(BaseModel):
    """
    Linha apresentada na tabela de regras de débito.
    """

    regra: str
    descricao: str = ""
    acao: str = ""

    quantidade: int = 0
    percentual: float = 0.0
    percentual_acumulado: float = 0.0
    valor_carga: float = 0.0


class AttentionRecord(BaseModel):
    """
    Registro que exige atenção ou análise operacional.
    """

    ctrc: str = ""
    numero_nota: str = ""

    data_inclusao_ctrc: Optional[date] = None
    data_ocorrencia: Optional[date] = None

    grupo_cliente: str = ""
    cliente: str = ""
    unidade: str = ""

    codigo_ocorrencia: str = ""
    descricao_ocorrencia: str = ""

    status_debito: str = ""
    regra_debito: str = ""

    produto: str = ""
    valor_carga: float = 0.0

    status_xml: str = ""
    mensagem: str = ""


class DashboardMeta(BaseModel):
    """
    Informações auxiliares da resposta.
    """

    generated_at: datetime
    source_updated_at: Optional[datetime] = None

    total_before_filters: int = 0
    total_after_filters: int = 0

    filters_applied: bool = False
    source_name: str = ""


class DashboardResponse(BaseModel):
    """
    Resposta completa do endpoint principal.

    GET /incidentes/api/dashboard
    """

    success: bool = True
    message: str = ""

    meta: DashboardMeta
    applied_filters: DashboardFilters

    filter_options: DashboardFilterOptions
    kpis: DashboardKPIs
    charts: DashboardCharts

    rules: List[DebitRuleRow] = Field(
        default_factory=list
    )

    attention_records: List[AttentionRecord] = Field(
        default_factory=list
    )


class DashboardErrorResponse(BaseModel):
    """
    Resposta padronizada em caso de erro.
    """

    success: bool = False
    message: str
    detail: Optional[str] = None