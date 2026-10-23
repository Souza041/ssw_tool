"""Política da espera BIG; não autoriza reenvio de uma OC73."""
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/Sao_Paulo")
CODIGOS_ESPERA_BIG = frozenset({"77"})
INTERVALO_RECHECAGEM = timedelta(minutes=10)


def rechecagem_big_habilitada():
    # Opt-in independente das travas de envio existentes.
    return os.getenv("OC73_RECHECAGEM_BIG_ENABLED", "").strip().upper() == "SIM"


def agora_local():
    return datetime.now(TIMEZONE)


def janela_rechecagem(data_referencia):
    if data_referencia.weekday() not in (0, 1, 2, 3):
        raise ValueError("Espera BIG exige execução original de segunda a quinta.")
    inicio = datetime.combine(data_referencia, time(23, 30), TIMEZONE)
    # A rodada de 00h30 tem essa última faixa de minuto para concluir a consulta.
    limite = datetime.combine(
        data_referencia + timedelta(days=1), time(0, 30, 59), TIMEZONE,
    )
    return inicio, limite


def proxima_rechecagem(agora, limite):
    if agora.tzinfo is None or limite.tzinfo is None:
        raise ValueError("Relógio da rechecagem deve conter timezone.")
    if agora > limite:
        return None
    ultima_rodada = limite.replace(second=0, microsecond=0)
    if agora >= ultima_rodada:
        return None  # A rodada final não cria outra consulta dentro do mesmo minuto.
    # Slots fixos: 23h40, 23h50, 00h00, 00h10, 00h20, 00h30.
    # Não acumula o tempo gasto pelas consultas anteriores.
    inicio = ultima_rodada - timedelta(hours=1)
    passo = INTERVALO_RECHECAGEM.total_seconds()
    numero_rodada = int((agora - inicio).total_seconds() // passo) + 1
    return min(inicio + numero_rodada * INTERVALO_RECHECAGEM, ultima_rodada)


def data_mysql(valor):
    return valor.astimezone(TIMEZONE).replace(tzinfo=None)


def data_local(valor):
    if valor.tzinfo is None:
        return valor.replace(tzinfo=TIMEZONE)
    return valor.astimezone(TIMEZONE)
