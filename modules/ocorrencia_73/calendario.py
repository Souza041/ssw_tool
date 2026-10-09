from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class JanelaOC73:
    fluxo: str
    data_inicial: date
    data_final: date

    @property
    def data_inicial_ssw(self) -> str:
        return self.data_inicial.strftime("%d%m%y")

    @property
    def data_final_ssw(self) -> str:
        return self.data_final.strftime("%d%m%y")


def calcular_janelas_oc73(
    data_referencia: date | None = None,
) -> list[JanelaOC73]:

    if data_referencia is None:
        data_referencia = datetime.now(TIMEZONE).date()

    dia_semana = data_referencia.weekday()
    janelas = []

    # Sábado: nenhuma execução.
    if dia_semana == 5:
        return janelas

    # Domingo: inclui sábado e domingo.
    inicio_cwb = (
        data_referencia - timedelta(days=1)
        if dia_semana == 6
        else data_referencia
    )

    janelas.append(
        JanelaOC73(
            fluxo="CWB",
            data_inicial=inicio_cwb,
            data_final=data_referencia,
        )
    )

    # BIG: segunda a quinta.
    if dia_semana in (0, 1, 2, 3):
        janelas.append(
            JanelaOC73(
                fluxo="BIG",
                data_inicial=data_referencia,
                data_final=data_referencia,
            )
        )

    return janelas