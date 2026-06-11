from pathlib import Path

import pandas as pd

import unicodedata

from operations.op001.coleta import OP001Coleta


MAPA_COLUNAS = {
    "CNPJ DESTINATARIO": "CNPJ_DESTINATARIO",
    "NUMERO DO TRANSPORTE": "TRANSPORTE",
    "ORDEM DE VENDA / ORDEM INVERSA": "ORDEM_INVERSA",
    "CLIENTE": "CLIENTE",
    "MUNICIPIO DESTINO": "MUNICIPIO_DESTINO",
    "UF DESTINO": "UF_DESTINO",
    "NOTA FISCAL": "NOTA_FISCAL",
    "SKU": "SKU",
}

COLUNAS_OBRIGATORIAS = {
    "CLIENTE",
    "MUNICIPIO_DESTINO",
    "UF_DESTINO",
    "TRANSPORTE",
    "ORDEM_INVERSA",
    "CNPJ_DESTINATARIO",
}

COLUNA_RESULTADO_COLETA = "COLETA_GERADA"
COLUNA_RESULTADO_SEQ = "SEQ_COLETA"
COLUNA_RESULTADO_STATUS = "STATUS_BOT"
COLUNA_RESULTADO_MSG = "MENSAGEM_BOT"


def normalizar_texto(texto: str) -> str:
    texto = str(texto).strip().upper()

    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ASCII", "ignore").decode("ASCII")

    return texto


def normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df.columns = [
        normalizar_texto(col)
        for col in df.columns
    ]

    return df


def validar_colunas(df: pd.DataFrame) -> None:
    ausentes = COLUNAS_OBRIGATORIAS - set(df.columns)

    if ausentes:
        raise ValueError(
            f"Colunas obrigatórias ausentes: {', '.join(sorted(ausentes))}"
        )


def processar_planilha_transporte(
    op001: OP001Coleta,
    input_file: Path,
    output_file: Path,
) -> Path:
    df = pd.read_excel(input_file, dtype=str).fillna("")
    df = normalizar_colunas(df)

    df = df.rename(columns=MAPA_COLUNAS)

    validar_colunas(df)

    for col in [
        COLUNA_RESULTADO_COLETA,
        COLUNA_RESULTADO_SEQ,
        COLUNA_RESULTADO_STATUS,
        COLUNA_RESULTADO_MSG,
    ]:
        if col not in df.columns:
            df[col] = ""

    output_file.parent.mkdir(parents=True, exist_ok=True)

    for index, row in df.iterrows():
        try:
            resultado = op001.salvar_coleta_transporte(
                nome_cliente=str(row["CLIENTE"]).strip(),
                municipio_destino=str(row["MUNICIPIO_DESTINO"]).strip(),
                uf_destino=str(row["UF_DESTINO"]).strip(),
                transporte=str(row["TRANSPORTE"]).strip(),
                ordem_inversa=str(row["ORDEM_INVERSA"]).strip(),
                cnpj_destinatario=str(row["CNPJ_DESTINATARIO"]).strip(),
            )

            df.at[index, COLUNA_RESULTADO_COLETA] = resultado.get("coleta", "")
            df.at[index, COLUNA_RESULTADO_SEQ] = resultado.get("seq_coleta", "")
            df.at[index, COLUNA_RESULTADO_STATUS] = "OK" if resultado.get("sucesso") else "ERRO"
            df.at[index, COLUNA_RESULTADO_MSG] = resultado.get("mensagem", "")

        except Exception as exc:
            df.at[index, COLUNA_RESULTADO_STATUS] = "ERRO"
            df.at[index, COLUNA_RESULTADO_MSG] = str(exc)

        df.to_excel(output_file, index=False)

    return output_file