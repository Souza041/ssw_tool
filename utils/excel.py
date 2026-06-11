from pathlib import Path

import pandas as pd


EXTENSOES_SUPORTADAS = {
    ".xlsx",
    ".xls",
    ".csv",
}


def carregar_planilha(path: Path) -> pd.DataFrame:
    ext = path.suffix.lower()

    if ext not in EXTENSOES_SUPORTADAS:
        raise ValueError(
            f"Formato não suportado: {path.suffix}. "
            f"Use XLSX, XLS ou CSV."
        )

    if ext == ".csv":
        try:
            return pd.read_csv(path, sep=None, engine="python").fillna("")
        except UnicodeDecodeError:
            return pd.read_csv(
                path,
                dtype=str,
                encoding="latin1",
            ).fillna("")

    return pd.read_excel(
        path,
        dtype=str,
    ).fillna("")