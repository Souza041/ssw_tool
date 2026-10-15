from datetime import date

from modules.ocorrencia_73.calendario import (
    AGENDAMENTOS_OC73,
    TIMEZONE,
    calcular_janelas_oc73,
)


def test_segunda():
    janelas = calcular_janelas_oc73(date(2026, 10, 12))

    assert [j.fluxo for j in janelas] == ["CWB", "BIG"]
    assert all(
        j.data_inicial == date(2026, 10, 12)
        for j in janelas
    )


def test_sexta():
    janelas = calcular_janelas_oc73(date(2026, 10, 9))

    assert [j.fluxo for j in janelas] == ["CWB"]


def test_sabado():
    janelas = calcular_janelas_oc73(date(2026, 10, 10))

    assert janelas == []


def test_domingo():
    janelas = calcular_janelas_oc73(date(2026, 10, 11))

    assert len(janelas) == 1

    cwb = janelas[0]

    assert cwb.fluxo == "CWB"
    assert cwb.data_inicial_ssw == "101026"
    assert cwb.data_final_ssw == "111026"


def test_quinta():
    janelas = calcular_janelas_oc73(date(2026, 10, 8))

    assert [j.fluxo for j in janelas] == ["CWB", "BIG"]


def test_cwb_segunda_a_sexta_periodo_do_proprio_dia():
    for dia in range(12, 17):
        data = date(2026, 10, dia)
        janelas = calcular_janelas_oc73(data, fluxo="CWB")
        assert len(janelas) == 1
        assert janelas[0].data_inicial == data
        assert janelas[0].data_final == data


def test_big_segunda_a_quinta_e_ausente_sexta_domingo():
    for dia in range(12, 16):
        data = date(2026, 10, dia)
        janela = calcular_janelas_oc73(data, fluxo="BIG")
        assert [(j.data_inicial, j.data_final) for j in janela] == [
            (data, data)
        ]

    assert calcular_janelas_oc73(
        date(2026, 10, 16), fluxo="BIG"
    ) == []
    assert calcular_janelas_oc73(
        date(2026, 10, 18), fluxo="BIG"
    ) == []


def test_domingo_na_virada_do_mes():
    janela = calcular_janelas_oc73(
        date(2026, 11, 1), fluxo="CWB"
    )[0]
    assert janela.data_inicial == date(2026, 10, 31)
    assert janela.data_final == date(2026, 11, 1)


def test_domingo_na_virada_do_ano():
    janela = calcular_janelas_oc73(
        date(2023, 1, 1), fluxo="CWB"
    )[0]
    assert janela.data_inicial == date(2022, 12, 31)
    assert janela.data_final == date(2023, 1, 1)


def test_agendamentos_usam_brasilia_as_23h30():
    assert TIMEZONE.key == "America/Sao_Paulo"
    assert {item.fluxo for item in AGENDAMENTOS_OC73} == {"CWB", "BIG"}
    assert all(
        (item.hora, item.minuto) == (23, 30)
        for item in AGENDAMENTOS_OC73
    )
    dias = {
        item.fluxo: item.dias_apscheduler
        for item in AGENDAMENTOS_OC73
    }
    assert dias == {
        "CWB": "mon,tue,wed,thu,fri,sun",
        "BIG": "mon,tue,wed,thu",
    }
