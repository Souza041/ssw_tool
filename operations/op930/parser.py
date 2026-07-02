from pathlib import Path

import pandas as pd


OCORRENCIAS_INTERESSE = {
    "1", "47", "62", "63", "73", "80", "85", "96",
}


def carregar_relatorio_op930(file_path: Path) -> pd.DataFrame:
    if file_path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(file_path, dtype=str).fillna("")

    return pd.read_csv(file_path, sep=";", dtype=str, encoding="latin1").fillna("")


def normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    return df


def encontrar_coluna(df: pd.DataFrame, possibilidades: list[str]) -> str:
    mapa = {col.upper().strip(): col for col in df.columns}

    for nome in possibilidades:
        chave = nome.upper().strip()
        if chave in mapa:
            return mapa[chave]

    raise ValueError(f"Coluna não encontrada entre: {possibilidades}")


def tratar_op930(file_path: Path, grupo: str, cnpj: str) -> pd.DataFrame:
    df = carregar_relatorio_op930(file_path)
    df = normalizar_colunas(df)

    df = df.loc[:, [col for col in df.columns if str(col) != "0"]]

    col_oc = encontrar_coluna(df, [
        "COD_OCOR",
        "COD OCOR",
        "COD_OCORRENCIA",
        "CODIGO_OCORRENCIA",
        "OC",
        "Ocorrência",
        "Ocorrencia",
        "Codigo Ocorrencia",
        "Código Ocorrência",
        "Última Ocorrência",
        "Ultima Ocorrencia",
    ])

    base = df.copy()

    base["_OC_NORMALIZADA"] = (
        base[col_oc]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )

    base = base[
        base["_OC_NORMALIZADA"].isin(OCORRENCIAS_INTERESSE)
    ].copy()

    base["GRUPO_CLIENTE"] = grupo
    base["CNPJ_GRUPO"] = cnpj

    return base