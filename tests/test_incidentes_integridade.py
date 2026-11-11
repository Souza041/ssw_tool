import pytest

import pandas as pd

from modules.incidentes.integridade import (validar_quantidade_linhas, COLUNAS_ORIGEM_OP930, validar_identidade_registros,)


def test_quantidade_preservada():
    logs = []
    validar_quantidade_linhas(etapa="OP101", quantidade_esperada=304, quantidade_obtida=304, log=logs.append)
    assert len(logs) == 1
    assert "Integridade OK" in logs[0]

@pytest.mark.parametrize("obtida", [303, 305, 0])
def test_quantidade_divergente_bloqueia(obtida):
    with pytest.raises(ValueError, match="Integridade comprometida"):
        validar_quantidade_linhas(etapa="OP101", quantidade_esperada=304, quantidade_obtida=obtida, log=lambda mensagem: None)

def criar_registro(ctrc, codigo):
    registro = {
        coluna: ""
        for coluna in COLUNAS_ORIGEM_OP930
    }

    registro["CTRC"] = ctrc
    registro["COD_OCOR"] = codigo
    registro["SERIE_CTE"] = "1"

    return registro

def test_identidade_aceita_reordenacao_e_duplicatas():
    original = pd.DataFrame([
        criar_registro("JOI123456", "57"),
        criar_registro("JOI123456", "57"),
        criar_registro("CWB987654", "12"),
    ])

    atual = original.iloc[::-1].reset_index(drop=True)

    validar_identidade_registros(
        original=original,
        atual=atual,
        etapa="TESTE",
        log=lambda mensagem: None,
    )

def test_identidade_bloqueia_alteracao():
    original = pd.DataFrame([
        criar_registro("JOI123456", "57"),
    ])

    atual = original.copy()
    atual.at[0, "COD_OCOR"] = "11"

    with pytest.raises(
        ValueError,
        match="Integridade comprometida",
    ):
        validar_identidade_registros(
            original=original,
            atual=atual,
            etapa="TESTE",
            log=lambda mensagem: None,
        )

def test_identidade_bloqueia_remocao_duplicata():
    original = pd.DataFrame([
        criar_registro("JOI123456", "57"),
        criar_registro("JOI123456", "57"),
    ])

    atual = original.iloc[1:].copy()

    with pytest.raises(
        ValueError,
        match="Integridade comprometida",
    ):
        validar_identidade_registros(
            original=original,
            atual=atual,
            etapa="TESTE",
            log=lambda mensagem: None,
        )
