from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class AgendamentoOC73:
    fluxo: str
    dias_semana: tuple[int, ...]
    hora: int = 23
    minuto: int = 30

    @property
    def dias_apscheduler(self) -> str:
        nomes = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        return ",".join(nomes[dia] for dia in self.dias_semana)


AGENDAMENTOS_OC73 = (
    AgendamentoOC73(
        fluxo="CWB",
        dias_semana=(0, 1, 2, 3, 4, 6),
    ),
    AgendamentoOC73(
        fluxo="BIG",
        dias_semana=(0, 1, 2, 3),
    ),
)

AGENDAMENTO_POR_FLUXO = {
    agendamento.fluxo: agendamento
    for agendamento in AGENDAMENTOS_OC73
}


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
    fluxo: str | None = None,
) -> list[JanelaOC73]:

    if data_referencia is None:
        data_referencia = datetime.now(TIMEZONE).date()

    if fluxo is not None:
        fluxo = str(fluxo).strip().upper()
        if fluxo not in AGENDAMENTO_POR_FLUXO:
            raise ValueError(f"Fluxo OC73 inválido: {fluxo!r}")

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

    if (
        fluxo in {None, "CWB"}
        and dia_semana in AGENDAMENTO_POR_FLUXO["CWB"].dias_semana
    ):
        janelas.append(
            JanelaOC73(
                fluxo="CWB",
                data_inicial=inicio_cwb,
                data_final=data_referencia,
            )
        )

    # BIG: segunda a quinta.
    if (
        fluxo in {None, "BIG"}
        and dia_semana in AGENDAMENTO_POR_FLUXO["BIG"].dias_semana
    ):
        janelas.append(
            JanelaOC73(
                fluxo="BIG",
                data_inicial=data_referencia,
                data_final=data_referencia,
            )
        )

    return janelas
