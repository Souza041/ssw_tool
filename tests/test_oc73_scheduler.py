"""Testes offline do scheduler OC73; nenhuma integração externa é usada."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Lock

from modules.ocorrencia_73 import scheduler as scheduler_oc73


class SchedulerFalso:
    def __init__(self):
        self.running = False
        self.jobs = []
        self.starts = 0

    def add_job(self, func, **kwargs):
        self.jobs.append((func, kwargs))

    def start(self):
        self.running = True
        self.starts += 1


def test_scheduler_desabilitado_nao_cria_jobs(monkeypatch):
    falso = SchedulerFalso()
    monkeypatch.setattr(scheduler_oc73, "_scheduler", falso)
    monkeypatch.setenv("OCORRENCIA_73_ENABLED", "false")

    assert scheduler_oc73.iniciar_scheduler_ocorrencia_73() is False
    assert falso.jobs == []
    assert falso.starts == 0


def test_scheduler_registra_jobs_recorrentes_separados(monkeypatch):
    falso = SchedulerFalso()
    monkeypatch.setattr(scheduler_oc73, "_scheduler", falso)
    monkeypatch.setenv("OCORRENCIA_73_ENABLED", "true")

    assert scheduler_oc73.iniciar_scheduler_ocorrencia_73() is True

    configuracoes = {item[1]["id"]: item[1] for item in falso.jobs}
    assert set(configuracoes) == {
        "ocorrencia_73_cwb",
        "ocorrencia_73_big",
    }

    for configuracao in configuracoes.values():
        assert configuracao["max_instances"] == 1
        assert configuracao["coalesce"] is True
        assert configuracao["misfire_grace_time"] == 60
        assert configuracao["replace_existing"] is True
        assert configuracao["trigger"].timezone.key == "America/Sao_Paulo"

    assert configuracoes["ocorrencia_73_cwb"]["kwargs"] == {
        "fluxo": "CWB"
    }
    assert configuracoes["ocorrencia_73_big"]["kwargs"] == {
        "fluxo": "BIG"
    }


def test_inicializacao_repetida_nao_duplica_jobs(monkeypatch):
    falso = SchedulerFalso()
    monkeypatch.setattr(scheduler_oc73, "_scheduler", falso)
    monkeypatch.setenv("OCORRENCIA_73_ENABLED", "true")

    assert scheduler_oc73.iniciar_scheduler_ocorrencia_73() is True
    assert scheduler_oc73.iniciar_scheduler_ocorrencia_73() is False
    assert len(falso.jobs) == 2
    assert falso.starts == 1


def test_reinicio_apos_horario_nao_agenda_disparo_antigo(monkeypatch):
    falso = SchedulerFalso()
    monkeypatch.setattr(scheduler_oc73, "_scheduler", falso)
    monkeypatch.setenv("OCORRENCIA_73_ENABLED", "true")
    scheduler_oc73.iniciar_scheduler_ocorrencia_73()

    agora = datetime(
        2026,
        10,
        15,
        23,
        31,
        tzinfo=scheduler_oc73.TIMEZONE,
    )

    for _, configuracao in falso.jobs:
        proximo = configuracao["trigger"].get_next_fire_time(None, agora)
        assert proximo > agora
        assert (proximo.hour, proximo.minute) == (23, 30)


def test_sabado_nao_instancia_repositorio(monkeypatch):
    class RepositorioProibido:
        def __init__(self):
            raise AssertionError("Banco não deveria ser acessado no sábado")

    monkeypatch.setattr(
        scheduler_oc73,
        "Ocorrencia73Repository",
        RepositorioProibido,
    )

    resultado = scheduler_oc73.executar_ocorrencia_73_agendada(
        "CWB",
        date(2026, 10, 10),
    )
    assert resultado is None


def test_reserva_distribuida_impede_execucao_concorrente(monkeypatch):
    class RepositorioConcorrente:
        mutex = Lock()
        reservada = False

        def reservar_execucao_agendada(self, **kwargs):
            with self.mutex:
                if self.reservada:
                    return {
                        "reservada": False,
                        "motivo": "execucao_ja_registrada",
                        "execucao_id": 700,
                    }
                type(self).reservada = True
                return {
                    "reservada": True,
                    "motivo": "reservada",
                    "execucao_id": 700,
                    "triggered_by": "scheduler:CWB",
                }

    chamadas = []

    class ServicoFalso:
        def __init__(self, repository):
            self.repository = repository

        def executar(self, **kwargs):
            chamadas.append(kwargs)
            return {"dry_run": True}

    monkeypatch.setattr(
        scheduler_oc73,
        "Ocorrencia73Repository",
        RepositorioConcorrente,
    )
    monkeypatch.setattr(
        scheduler_oc73,
        "Ocorrencia73Service",
        ServicoFalso,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados = list(pool.map(
            lambda _: scheduler_oc73.executar_ocorrencia_73_agendada(
                "CWB", date(2026, 10, 12)
            ),
            range(2),
        ))

    assert len(chamadas) == 1
    assert sum(resultado is not None for resultado in resultados) == 1


def test_falha_de_um_fluxo_nao_impede_o_outro(monkeypatch):
    class RepositorioFalso:
        proximo_id = 800

        def reservar_execucao_agendada(self, *, fluxo, **kwargs):
            type(self).proximo_id += 1
            return {
                "reservada": True,
                "motivo": "reservada",
                "execucao_id": self.proximo_id,
                "triggered_by": f"scheduler:{fluxo}",
            }

    chamadas = []

    class ServicoFalso:
        def __init__(self, repository):
            pass

        def executar(self, *, fluxo, **kwargs):
            chamadas.append(fluxo)
            if fluxo == "CWB":
                raise RuntimeError("falha isolada CWB")
            return {"dry_run": True, "total_relatorio": 0}

    monkeypatch.setattr(
        scheduler_oc73,
        "Ocorrencia73Repository",
        RepositorioFalso,
    )
    monkeypatch.setattr(
        scheduler_oc73,
        "Ocorrencia73Service",
        ServicoFalso,
    )

    cwb = scheduler_oc73.executar_ocorrencia_73_agendada(
        "CWB", date(2026, 10, 12)
    )
    big = scheduler_oc73.executar_ocorrencia_73_agendada(
        "BIG", date(2026, 10, 12)
    )

    assert cwb is None
    assert big is not None
    assert chamadas == ["CWB", "BIG"]
