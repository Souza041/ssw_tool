"""Datas do evento OC63; não confundir com emissão ou inclusão no SSW."""
from datetime import date, datetime


def interpretar_data_oc63(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor or "").strip()
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d",
                    "%d/%m/%y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    return None


def motivo_bloqueio_data_oc63(valor, inicio: date, fim: date, *, codigo="63"):
    if inicio > fim:
        raise ValueError("Janela de ocorrência invertida.")
    data = interpretar_data_oc63(valor)
    if data is None:
        return f"data_oc{codigo}_ausente_ou_invalida"
    if not inicio <= data <= fim:
        return f"data_oc{codigo}_fora_da_janela"
    return None
