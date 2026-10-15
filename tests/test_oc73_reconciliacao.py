
from datetime import date
from unittest.mock import MagicMock

import pytest

from modules.ocorrencia_73.gate import PortaoOC73

from modules.ocorrencia_73.reconciliacao import (
    ReconciliadorOC73,
)


@pytest.fixture
def tentativa():
    return {
        "id": 10,
        "referencia": "OC73-A1B2C3D4E5F60718293A",
        "serie": "CWB",
        "numero": "123456",
        "status": "incerto",
        "data_inicial_pesquisa": date(2026, 10, 12),
        "data_final_pesquisa": date(2026, 10, 12),
    }


def oc73_da_tentativa(tentativa):
    referencia = tentativa["referencia"]
    return {
        "codigo": "73",
        "complemento": f"BOT OC73 | REF:{referencia}",
    }


@pytest.fixture
def repository():
    return MagicMock()


def test_oc73_encontrada_confirma(
    repository,
    tentativa,
):
    consultar = MagicMock(
        return_value=[
            {"codigo": "63"},
            oc73_da_tentativa(tentativa),
        ]
    )

    reconciliador = ReconciliadorOC73(
        repository,
        consultar,
    )

    resultado = reconciliador.reconciliar(
        tentativa
    )

    assert resultado.status == "confirmado"
    assert resultado.confirmou is True
    assert resultado.permite_reenvio is False

    repository.confirmar_lancamento.assert_called_once()

    consultar.assert_called_once_with(
        serie="CWB",
        numero="123456",
        data_inicial=date(2026, 10, 12),
        data_final=date(2026, 10, 12),
    )


def test_oc73_ausente_nao_reenvia(
    repository,
    tentativa,
):
    consultar = MagicMock(
        return_value=[
            {"codigo": "63"},
            {"codigo": "64"},
        ]
    )

    resultado = ReconciliadorOC73(
        repository,
        consultar,
    ).reconciliar(tentativa)

    assert resultado.status == "incerto"
    assert resultado.confirmou is False
    assert resultado.permite_reenvio is False

    repository.confirmar_lancamento.assert_not_called()


def test_falha_consulta_mantem_bloqueio(
    repository,
    tentativa,
):
    consultar = MagicMock(
        side_effect=TimeoutError(
            "Timeout OP101"
        )
    )

    resultado = ReconciliadorOC73(
        repository,
        consultar,
    ).reconciliar(tentativa)

    assert resultado.status == "incerto"
    assert resultado.confirmou is False
    assert resultado.permite_reenvio is False

    repository.confirmar_lancamento.assert_not_called()


def test_historico_indisponivel_nao_confirma(
    repository,
    tentativa,
):
    consultar = MagicMock(return_value=None)

    resultado = ReconciliadorOC73(
        repository,
        consultar,
    ).reconciliar(tentativa)

    assert resultado.confirmou is False
    assert resultado.permite_reenvio is False

    repository.confirmar_lancamento.assert_not_called()


def test_envio_iniciado_pode_ser_reconciliado(
    repository,
    tentativa,
):
    tentativa["status"] = "envio_iniciado"

    consultar = MagicMock(
        return_value=[
            oc73_da_tentativa(tentativa),
        ]
    )

    resultado = ReconciliadorOC73(
        repository,
        consultar,
    ).reconciliar(tentativa)

    assert resultado.status == "confirmado"
    assert resultado.confirmou is True

    repository.confirmar_lancamento.assert_called_once()


def test_confirmado_nao_consulta_novamente(
    repository,
    tentativa,
):
    tentativa["status"] = "confirmado"

    consultar = MagicMock()

    resultado = ReconciliadorOC73(
        repository,
        consultar,
    ).reconciliar(tentativa)

    assert resultado.status == "confirmado"
    assert resultado.permite_reenvio is False

    consultar.assert_not_called()
    repository.confirmar_lancamento.assert_not_called()


def test_reservado_nao_reconcilia(
    repository,
    tentativa,
):
    tentativa["status"] = "reservado"

    consultar = MagicMock()

    resultado = ReconciliadorOC73(
        repository,
        consultar,
    ).reconciliar(tentativa)

    assert resultado.status == "reservado"
    assert resultado.confirmou is False
    assert resultado.permite_reenvio is False

    consultar.assert_not_called()
    repository.confirmar_lancamento.assert_not_called()


def test_falha_persistencia_nao_finge_sucesso(
    repository,
    tentativa,
):
    consultar = MagicMock(
        return_value=[
            oc73_da_tentativa(tentativa),
        ]
    )

    repository.confirmar_lancamento.side_effect = (
        RuntimeError("MySQL indisponível")
    )

    reconciliador = ReconciliadorOC73(
        repository,
        consultar,
    )

    with pytest.raises(
        RuntimeError,
        match="MySQL indisponível",
    ):
        reconciliador.reconciliar(tentativa)


def test_oc73_antiga_sem_referencia_nao_confirma(repository, tentativa):
    consultar = MagicMock(return_value=[{"codigo": "73", "complemento": "BOT OC73 ANTIGO"}])
    resultado = ReconciliadorOC73(repository, consultar).reconciliar(tentativa)
    assert resultado.status == "incerto"
    assert resultado.confirmou is False
    assert resultado.permite_reenvio is False
    repository.confirmar_lancamento.assert_not_called()


def test_oc73_referencia_de_outra_tentativa_nao_confirma(repository, tentativa):
    consultar = MagicMock(return_value=[{
        "codigo": "73",
        "complemento": "REF:OC73-FFFFFFFFFFFFFFFFFFFF",
    }])
    resultado = ReconciliadorOC73(repository, consultar).reconciliar(tentativa)
    assert resultado.status == "incerto"
    assert resultado.confirmou is False
    assert resultado.permite_reenvio is False
    repository.confirmar_lancamento.assert_not_called()


def test_oc73_antiga_antes_da_referencia_correta(repository, tentativa):
    consultar = MagicMock(return_value=[
        {"codigo": "73", "complemento": "LANCAMENTO ANTIGO"},
        oc73_da_tentativa(tentativa),
    ])
    resultado = ReconciliadorOC73(repository, consultar).reconciliar(tentativa)
    assert resultado.status == "confirmado"
    assert resultado.confirmou is True
    repository.confirmar_lancamento.assert_called_once()

def test_tentativa_legada_sem_referencia_nao_reconcilia(
    repository, tentativa
):
    tentativa["referencia"] = None
    consultar = MagicMock()

    resultado = ReconciliadorOC73(
        repository, consultar
    ).reconciliar(tentativa)

    assert resultado.confirmou is False
    assert resultado.permite_reenvio is False
    consultar.assert_not_called()
    repository.confirmar_lancamento.assert_not_called()
