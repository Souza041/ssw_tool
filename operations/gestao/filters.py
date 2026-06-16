from datetime import datetime, timedelta

import pandas as pd

import os

OC_IGNORAR = {"1"}

OC_GESTAO = {
    "2", "3", "5", "8", "12", "13", "14", "15", "16", "17", "18", "19",
    "22", "23", "24", "25", "26", "27", "28", "32", "35", "39", "43",
    "44", "51", "58", "60", "70", "74", "76", "78", "79",
}

OC_OPERACIONAL = {
    "4", "29", "30", "31", "33", "37", "55", "59", "68", "69", "73",
    "75", "77", "80", "85", "96",
}

OC_PARCEIROS = OC_OPERACIONAL

def classificar_destino_alerta(row) -> str:
    unidade = str(row.get("_UNIDADE_ALERTA", "")).strip().upper()
    oc = str(row.get("_OC_NORMALIZADA", "")).strip()

    if unidade == "FEC":
        return "INTERNO"

    if oc in OC_OPERACIONAL:
        return "PARCEIRO"
    
    if oc in OC_GESTAO:
        return "INTERNO"

    return "INTERNO"

def encontrar_coluna(df: pd.DataFrame, possibilidades: list[str]) -> str:
    colunas = {col.upper().strip(): col for col in df.columns}

    for nome in possibilidades:
        if nome.upper().strip() in colunas:
            return colunas[nome.upper().strip()]

    raise ValueError(f"Nenhuma coluna encontrada entre: {possibilidades}")


def converter_data(series: pd.Series) -> pd.Series:
    return pd.to_datetime(
        series,
        format="%d/%m/%Y",
        errors="coerce",
    )


def filtrar_pedidos_455(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    hoje = datetime.now().date()
    
    op455_pre_alerta_dias = int(os.getenv("OP455_PRE_ALERTA_DIAS", "1"))
    limite_pre_alerta = hoje + timedelta(days=op455_pre_alerta_dias)

    coluna_previsao = encontrar_coluna(df, [
        "PREVISAO DE ENTREGA",
        "PREVISÃO DE ENTREGA",
        "PREVISAO ENTREGA",
        "PREVISÃO ENTREGA",
        "DT PREV ENTREGA",
        "DATA PREVISAO",
    ])

    coluna_unidade = encontrar_coluna(df, [
        "UNIDADE RECEPTORA",
        "UNI",
        "UNIDADE",
        "FILIAL",
        "DESTINO",
    ])

    coluna_oc = encontrar_coluna(df, [
        "CODIGO DA ULTIMA OCORRENCIA",
        "CÓDIGO DA ÚLTIMA OCORRÊNCIA",
        "CODIGO OC",
        "CÓDIGO OC",
        "OC",
        "OCORRENCIA",
        "OCORRÊNCIA",
    ])

    coluna_status = encontrar_coluna(df, [
        "DESCRICAO DA ULTIMA OCORRENCIA",
        "DESCRIÇÃO DA ÚLTIMA OCORRÊNCIA",
        "STATUS",
        "SITUACAO",
        "SITUAÇÃO",
    ])

    base = df.copy()

    base["_DATA_PREVISAO"] = converter_data(base[coluna_previsao]).dt.date

    base["_UNIDADE_ALERTA"] = (
        base[coluna_unidade]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    base["_OC_NORMALIZADA"] = (
        base[coluna_oc]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    base["_STATUS_NORMALIZADO"] = (
        base[coluna_status]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    base["_DESTINO_ALERTA"] = base.apply(classificar_destino_alerta, axis=1)

    base["DIAS_EM_ATRASO"] = base["_DATA_PREVISAO"].apply(
        lambda data: (hoje - data).days if pd.notna(data) else None
    )

    base = base[
        ~base["_OC_NORMALIZADA"].isin(OC_IGNORAR)
    ].copy()

    base_operacional = base[
        base["_OC_NORMALIZADA"].isin(OC_OPERACIONAL)
    ].copy()

    base_gestao = base[
        base["_OC_NORMALIZADA"].isin(OC_GESTAO)
    ].copy()

    em_atraso = base_operacional[
        base_operacional["_DATA_PREVISAO"].notna()
        & (base_operacional["_DATA_PREVISAO"] < hoje)
    ].copy()

    em_atraso = em_atraso.sort_values(
        by=["DIAS_EM_ATRASO", "_DATA_PREVISAO"],
        ascending=[False, True],
    )

    pre_alerta = base_operacional[
        base_operacional["_DATA_PREVISAO"].notna()
        & (base_operacional["_DATA_PREVISAO"] >= hoje)
        & (base_operacional["_DATA_PREVISAO"] <= limite_pre_alerta)
        & ~base_operacional["_STATUS_NORMALIZADO"].str.contains("ROTA", na=False)
    ].copy()

    pre_alerta = pre_alerta.sort_values(
        by=["_DATA_PREVISAO"],
        ascending=True,
    )

    pendencia_gestao = base_gestao[
        base_gestao["_DATA_PREVISAO"].notna()
        & (base_gestao["_DATA_PREVISAO"] <= limite_pre_alerta)
    ].copy()

    pendencia_gestao = pendencia_gestao.sort_values(
        by=["DIAS_EM_ATRASO", "_DATA_PREVISAO"],
        ascending=[False, True],
    )

    return {
        "em_atraso": em_atraso,
        "pre_alerta": pre_alerta,
        "pendencia_gestao": pendencia_gestao,
    }