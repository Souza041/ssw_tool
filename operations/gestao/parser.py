import csv
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from datetime import datetime

OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


COLUNAS_TRATADAS = [
    "Serie/Numero CTRC",
    "Data de Emissao",
    "Tipo do Documento",
    "CNPJ Pagador",
    "Cliente Pagador",
    "Cliente Destinatario",
    "Cidade de Entrega",
    "UF de Entrega",
    "Unidade Receptora",
    "Numero da Nota Fiscal",
    "Mercadoria",
    "Codigo da Ultima Ocorrencia",
    "Data da Ultima Ocorrencia",
    "Descricao da Ultima Ocorrencia",
    "Previsao de Entrega",
    "Entrega Programada",
    "Data da Entrega Realizada",
]

TIPOS_DOCUMENTO_BLOQUEADOS = {
    "DEVOLUCAO",
    "REVERSA",
    "COMPLEMETAR FRETE",
    "CORTESIA",
}

def limpar_texto(value: object) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\xa0", "")
        .replace("�", "")
        .strip()
    )


def carregar_sswweb_455(file_path: Path) -> pd.DataFrame:
    header: list[str] = []
    rows: list[list[str]] = []

    with open(file_path, "r", encoding="latin1", errors="ignore", newline="") as file:
        reader = csv.reader(file, delimiter=";")

        for row in reader:
            if not row:
                continue

            tipo = limpar_texto(row[0])

            if tipo == "1":
                header = [limpar_texto(col) for col in row[1:]]
                continue

            if tipo == "2":
                rows.append([limpar_texto(col) for col in row[1:]])
                continue

    if not header:
        raise ValueError(f"Cabeçalho tipo '1' não encontrado em: {file_path.name}")

    if not rows:
        raise ValueError(f"Dados tipo '2' não encontrados em: {file_path.name}")

    normalized_rows = []

    for row in rows:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))

        elif len(row) > len(header):
            row = row[:len(header)]

        normalized_rows.append(row)

    return pd.DataFrame(normalized_rows, columns=header)


def formatar_data_br(series: pd.Series) -> pd.Series:
    datas = pd.to_datetime(
        series,
        format="%d/%m/%Y",
        errors="coerce",
    )

    return datas.dt.strftime("%d/%m/%Y").fillna("")


def normalizar_codigo_oc(series: pd.Series) -> pd.Series:
    return (
        series
        .astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.lstrip("0")
        .replace("", "0")
    )

def normalizar_texto_filtro(value: object) -> str:
    return (
        limpar_texto(value)
        .upper()
        .replace(".", "")
        .replace("-", " ")
        .replace("_", " ")
        .strip()
    )


def classificar_mercadoria(value: object) -> str:
    texto = normalizar_texto_filtro(value)

    if "ECOMMERCE" in texto:
        return "ECOMMERCE"

    if "FRACIONADO" in texto:
        return "FRACIONADO"

    if "NEXT DAY" in texto or "NEXTDAY" in texto:
        return "NEXT DAY"

    if "L BRANCA" in texto or "LINHA BRANCA" in texto:
        return "LINHA BRANCA"

    return "OUTROS"


def filtrar_mercadorias_permitidas(df: pd.DataFrame) -> pd.DataFrame:
    if "Mercadoria" not in df.columns:
        raise ValueError("Coluna 'Mercadoria' não encontrada no relatório 455.")

    base = df.copy()

    base["TIPO_MERCADORIA"] = base["Mercadoria"].map(classificar_mercadoria)

    permitidos = {
        "ECOMMERCE",
        "FRACIONADO",
        "NEXT DAY",
        "LINHA BRANCA",
    }

    return base[
        base["TIPO_MERCADORIA"].isin(permitidos)
    ].copy()

def filtrar_tipos_documento_permitidos(df: pd.DataFrame) -> pd.DataFrame:
    if "Tipo do Documento" not in df.columns:
        raise ValueError("Coluna 'Tipo do Documento' não encontrada no relatório 455.")

    base = df.copy()

    tipo_normalizado = (
        base["Tipo do Documento"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    return base[
        ~tipo_normalizado.isin(TIPOS_DOCUMENTO_BLOQUEADOS)
    ].copy()

def montar_base_tratada(df: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in COLUNAS_TRATADAS if col not in df.columns]

    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes na OP455: {missing}")

    base = df[COLUNAS_TRATADAS].copy()

    for col in base.columns:
        base[col] = base[col].map(limpar_texto)

    colunas_data = [
        "Data de Emissao",
        "Data da Ultima Ocorrencia",
        "Previsao de Entrega",
        "Entrega Programada",
        "Data da Entrega Realizada",
    ]

    for col in colunas_data:
        base[col] = formatar_data_br(base[col])

    base["Codigo da Ultima Ocorrencia"] = normalizar_codigo_oc(
        base["Codigo da Ultima Ocorrencia"]
    )

    base = filtrar_mercadorias_permitidas(base)
    base = filtrar_tipos_documento_permitidos(base)

    return base


def filtrar_emissao_recente(df: pd.DataFrame, dias: int = 1) -> pd.DataFrame:
    hoje = datetime.now().date()
    inicio = hoje - timedelta(days=dias)

    datas = pd.to_datetime(
        df["Data de Emissao"],
        dayfirst=True,
        errors="coerce",
    ).dt.date

    return df[
        datas.notna()
        & (datas >= inicio)
        & (datas <= hoje)
    ].copy()


def tratar_relatorio_455(file_path: Path) -> tuple[Path, pd.DataFrame]:
    df_raw = carregar_sswweb_455(file_path)
    base_tratada = montar_base_tratada(df_raw)

    # Para bater com o arquivo tratado que você mandou:
    # mantém emissão de hoje e ontem.
    #base_tratada = filtrar_emissao_recente(base_tratada, dias=1)

    output_file = OUTPUT_DIR / f"base_455_tratada_{timestamp()}.xlsx"

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        base_tratada.to_excel(writer, index=False, sheet_name="base_tratada")

    return output_file, base_tratada