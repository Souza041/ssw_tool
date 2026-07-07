import unicodedata
from datetime import datetime
import pandas as pd


def norm_col(s):
    s = str(s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.split())


def carregar_dataframe(file_path):
    file_path = str(file_path)

    if file_path.lower().endswith(".sswweb"):
        df = pd.read_csv(
            file_path,
            sep=";",
            encoding="latin1",
            skiprows=1,
            dtype=str,
            engine="python",
        )
    else:
        df = pd.read_excel(file_path, dtype=str)

    df.columns = [str(c).strip() for c in df.columns]
    return df


def find_col(df, nomes):
    mapa = {norm_col(c): c for c in df.columns}

    for nome in nomes:
        key = norm_col(nome)
        if key in mapa:
            return mapa[key]

    return None


def get(row, col):
    if not col:
        return ""

    v = row.get(col, "")

    if pd.isna(v):
        return ""

    return str(v).strip()


def to_float(v):
    v = str(v or "").strip()

    if not v:
        return 0.0

    try:
        return float(v.replace(".", "").replace(",", "."))
    except Exception:
        return 0.0


def to_int(v):
    v = str(v or "").strip()

    if not v:
        return 0

    try:
        return int(float(v.replace(",", ".")))
    except Exception:
        return 0


def data_existe(v):
    v = str(v or "").strip()
    return bool(v and v not in ["00/00/00", "00/00/0000", "nan", "None"])


def montar_item_v34(row, cols):
    entrega_realizada = get(row, cols["entrega"])
    dias_atraso = to_int(get(row, cols["dias_atraso"]))

    baixa_mobile = resolver_baixa_mobile(row, cols)

    status = "Entregue" if data_existe(entrega_realizada) else "Em aberto"

    if status == "Entregue":
        if dias_atraso > 0:
            prazo = "Atrasado"
        elif dias_atraso < 0:
            prazo = "Antecipado"
        else:
            prazo = "No prazo"
    else:
        prazo = "Em aberto"

    ocorr = get(row, cols["ocorrencia"])
    ocorr73 = "SIM" if ocorr == "73" else "NÃO"

    return {
        "ctrc": get(row, cols["ctrc"]),
        "cte": get(row, cols["cte"]),
        "nf": get(row, cols["nf"]),

        "emissao": get(row, cols["emissao"]),
        "diaEmissao": get(row, cols["emissao"])[:2],
        "previsao": get(row, cols["previsao"]),
        "entrega": entrega_realizada,

        "status": status,
        "prazo": prazo,
        "diasAtraso": dias_atraso,

        "ocorrencia": ocorr,
        "ocorrenciaDescricao": get(row, cols["ocorrencia_desc"]),
        "ocorr73": ocorr73,

        "cliente": (
            get(row, cols["cliente_pagador"])
            or get(row, cols["cliente_remetente"])
            or get(row, cols["cliente_destinatario"])
        ),

        "remetente": get(row, cols["cliente_remetente"]),
        "destinatario": get(row, cols["cliente_destinatario"]),

        "uf": get(row, cols["uf_entrega"]) or get(row, cols["uf_destinatario"]),
        "cidade": get(row, cols["cidade_entrega"]) or get(row, cols["cidade_destinatario"]),

        "unidade": get(row, cols["unidade_emissora"]),
        "unidadeReceptora": get(row, cols["unidade_receptora"]),
        "usuario": get(row, cols["login"]),

        "romaneio": "SIM" if get(row, cols["canhoto"]) else "NÃO",
        "baixaMobile": baixa_mobile,

        "operacao": get(row, cols["tipo_documento"]) or get(row, cols["tipo_frete"]),

        "frete": to_float(get(row, cols["valor_frete"])),
        "fretePeso": to_float(get(row, cols["frete_peso"])),
        "peso": to_float(get(row, cols["peso"])),
        "cubagem": to_float(get(row, cols["cubagem"])),
        "volumes": to_int(get(row, cols["qtd_volumes"]) or get(row, cols["volumes"])),

        "parceiro": get(row, cols["unidade_receptora"]),
        "cidParceiros": get(row, cols["cidade_entrega"]) or get(row, cols["cidade_destinatario"]),
        "ufParceiro": get(row, cols["uf_entrega"]) or get(row, cols["uf_destinatario"]),
        "endereco": get(row, cols["endereco"]),
        "h": baixa_mobile,
        "i": "SIM" if get(row, cols["canhoto"]) else "NAO",
        "l": "SIM" if ocorr == "73" else "NAO",
    }


def processar_op455_snapshot(file_path):
    df = carregar_dataframe(file_path)

    cols = {
        "ctrc": find_col(df, ["Serie/Numero CTRC"]),
        "cte": find_col(df, ["Serie/Numero CT-e"]),
        "nf": find_col(df, ["Numero da Nota Fiscal", "Notas Fiscais"]),

        "emissao": find_col(df, ["Data de Emissao"]),
        "previsao": find_col(df, ["Previsao de Entrega"]),
        "entrega": find_col(df, ["Data da Entrega Realizada"]),
        "dias_atraso": find_col(df, ["Quantidade de Dias de Atraso"]),

        "ocorrencia": find_col(df, ["Codigo da Ultima Ocorrencia"]),
        "ocorrencia_desc": find_col(df, ["Descricao da Ultima Ocorrencia"]),

        "cliente_pagador": find_col(df, ["Cliente Pagador"]),
        "cliente_remetente": find_col(df, ["Cliente Remetente"]),
        "cliente_destinatario": find_col(df, ["Cliente Destinatario"]),

        "uf_entrega": find_col(df, ["UF de Entrega"]),
        "cidade_entrega": find_col(df, ["Cidade de Entrega"]),
        "uf_destinatario": find_col(df, ["UF do Destinatario"]),
        "cidade_destinatario": find_col(df, ["Cidade do Destinatario"]),

        "unidade_emissora": find_col(df, ["Unidade Emissora"]),
        "unidade_receptora": find_col(df, ["Unidade Receptora"]),
        "login": find_col(df, ["Login"]),

        "canhoto": find_col(df, ["Capa de Canhoto de NF"]),
        "tipo_baixa": find_col(df, ["Tipo de Baixa"]),
        "tipo_documento": find_col(df, ["Tipo do Documento"]),
        "tipo_frete": find_col(df, ["Tipo do Frete"]),

        "valor_frete": find_col(df, ["Valor do Frete"]),
        "frete_peso": find_col(df, ["Frete Peso"]),
        "peso": find_col(df, ["Peso Real em Kg"]),
        "cubagem": find_col(df, ["Cubagem em m3"]),
        "qtd_volumes": find_col(df, ["Quantidade de Volumes"]),
        "volumes": find_col(df, ["Volumes"]),

        "endereco": find_col(df, ["Endereco", "Endereço"]),
    }

    print("COLUNAS DETECTADAS METRICAS:")
    for k, v in cols.items():
        print(k, "=>", v)

    print("AMOSTRA TIPO DE BAIXA:")
    col_tipo_baixa = cols.get("tipo_baixa")
    if col_tipo_baixa:
        print(df[col_tipo_baixa].dropna().astype(str).str.strip().value_counts().head(20))
    else:
        print("Coluna 'tipo_baixa' não encontrada.")

    print("AMOSTRA TIPO DO DOCUMENTO:")
    col_tipo_documento = cols.get("tipo_documento")
    if col_tipo_documento:
        print(df[col_tipo_documento].dropna().astype(str).str.strip().value_counts().head(20))
    else:
        print("Coluna 'tipo_documento' não encontrada.")
    
    print("Colunas COM POSSÍVEL MOBILE/ROMANEIO/BAIXA:")
    for col in df.columns:
        c = norm_col(col)
        if any(x in c for x in ["mobile", "romaneio", "canhoto", "baixa", "capa"]):
            print("_" * 60)
            print(col)
            print(df[col].dropna().astype(str).str.strip().value_counts().head(10))

    data = [montar_item_v34(row, cols) for _, row in df.iterrows()]

    return {
        "meta": {
            "source": "OP455",
            "generated_at": datetime.now().isoformat(),
            "file_name": str(file_path),
            "total": len(data),
            "colunas_detectadas": cols,
        },
        "DATA": data,
        "DATA_COLETA": [],
        "DATA_AVALIACAO": [],
        "DATA_CUSTO": [],
    }

def resolver_baixa_mobile(row, cols):
    desc = get(row, cols.get("ocorrencia_desc")).upper()
    desc = " ".join(desc.split())

    mobile_patterns = [
        "ENTREGA REALIZADA (SSWMOBILE)",
        "ENTREGA REALIZADA NORMALMENTE (SSWMOBILE)",
    ]

    if any(p in desc for p in mobile_patterns):
        return "MOBILE"

    return "MANUAL"