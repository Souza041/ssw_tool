from datetime import date

from modules.ocorrencia_73.calendario import (
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