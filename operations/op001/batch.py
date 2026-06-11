from pathlib import Path

import pandas as pd

from operations.op001.coleta import OP001Coleta

from utils.excel import carregar_planilha


COLUNA_NFD = "NFD"
COLUNA_CNPJ = "CNPJ"

COLUNA_RESULTADO_COLETA = "COLETA_GERADA"
COLUNA_RESULTADO_SEQ = "SEQ_COLETA"
COLUNA_RESULTADO_STATUS = "STATUS_BOT"
COLUNA_RESULTADO_MSG = "MENSAGEM_BOT"


def normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [
        str(col).strip().upper()
        for col in df.columns
    ]
    return df


def validar_colunas(df: pd.DataFrame) -> None:
    obrigatorias = {
        COLUNA_NFD,
        COLUNA_CNPJ,
    }

    ausentes = obrigatorias - set(df.columns)

    if ausentes:
        raise ValueError(
            f"Colunas obrigatórias ausentes na planilha: {', '.join(sorted(ausentes))}"
        )


def processar_planilha_nfd(
    op001: OP001Coleta,
    input_file: Path,
    output_file: Path,
    solicitante: str = "AutomacaoColeta",
    tipo_frete: str = "F",
    cnpj_destinatario: str = "76487032004031",
    hora_limite: str = "1800",
) -> Path:
    df = carregar_planilha(input_file)
    df = normalizar_colunas(df)
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
        nfd = str(row[COLUNA_NFD]).strip()
        cnpj = str(row[COLUNA_CNPJ]).strip()

        if not nfd or not cnpj:
            df.at[index, COLUNA_RESULTADO_STATUS] = "ERRO"
            df.at[index, COLUNA_RESULTADO_MSG] = "NFD ou CNPJ vazio."
            df.to_excel(output_file, index=False)
            continue

        try:
            resultado = op001.salvar_coleta_nfd(
                nfd=nfd,
                cnpj=cnpj,
                solicitante=solicitante,
                tipo_frete=tipo_frete,
                cnpj_destinatario=cnpj_destinatario,
                hora_limite=hora_limite,
            )

            df.at[index, COLUNA_RESULTADO_COLETA] = resultado.get("coleta", "")
            df.at[index, COLUNA_RESULTADO_SEQ] = resultado.get("seq_coleta", "")
            df.at[index, COLUNA_RESULTADO_STATUS] = "OK" if resultado.get("sucesso") else "ERRO"
            df.at[index, COLUNA_RESULTADO_MSG] = resultado.get("mensagem", "")

        except Exception as exc:
            df.at[index, COLUNA_RESULTADO_STATUS] = "ERRO"
            df.at[index, COLUNA_RESULTADO_MSG] = str(exc)

        # Salva a cada linha para não perder progresso
        df.to_excel(output_file, index=False)

    return output_file