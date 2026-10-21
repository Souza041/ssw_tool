from datetime import date
from unittest.mock import MagicMock

import pytest

from modules.ocorrencia_73.gate import PortaoOC73
from tests.test_oc73_bloqueio_persistencia import REFERENCIA_TESTE

D = date(2026, 10, 12)


def args():
    return dict(execucao_id=9, serie="CWB", numero="100010", fluxo="CWB",
                data_inicial=D, data_final=D)


def repo(nova=True, transicao=True):
    r = MagicMock()
    r.reservar_ctrc.return_value = dict(
        id=15,
        referencia=REFERENCIA_TESTE,
        execucao_id=9,
        status="reservado",
        reserva_criada=nova,
    )
    r.iniciar_envio.return_value = transicao
    return r


def test_reserva_existente_nunca_envia():
    r = repo(nova=False)
    enviar, confirmar = MagicMock(), MagicMock()
    res = PortaoOC73(r).processar(**args(), enviar=enviar, confirmar=confirmar)
    assert res.status == "bloqueado_reserva_existente"
    enviar.assert_not_called()
    r.iniciar_envio.assert_not_called()


def test_transicao_falhou_nunca_envia():
    r = repo(transicao=False)
    enviar = MagicMock()
    res = PortaoOC73(r).processar(**args(), enviar=enviar, confirmar=MagicMock())
    assert res.status == "bloqueado_transicao"
    enviar.assert_not_called()


def test_confirmado_so_com_codigo_73_e_referencia():
    r = repo()
    enviar = MagicMock()

    def confirmar(referencia):
        return {
            "codigo": "73",
            "complemento": (
                f"LANCAMENTO AUTOMATICO | REF:{referencia}"
            ),
        }

    res = PortaoOC73(r).processar(
        **args(),
        enviar=enviar,
        confirmar=confirmar,
    )

    assert res.status == "confirmado"
    assert res.tentativa_id == 15

    enviar.assert_called_once_with(REFERENCIA_TESTE)
    r.confirmar_lancamento.assert_called_once()
    r.marcar_incerto.assert_not_called()


def test_sem_confirmacao_fica_incerto_sem_retry():
    r = repo()
    enviar = MagicMock()
    res = PortaoOC73(r).processar(
        **args(),
        enviar=enviar,
        confirmar=lambda referencia: None,
    )
    assert res.status == "incerto"
    enviar.assert_called_once()
    r.marcar_incerto.assert_called_once()


def test_timeout_post_fica_incerto():
    r = repo()
    enviar = MagicMock(side_effect=TimeoutError("timeout"))
    res = PortaoOC73(r).processar(**args(), enviar=enviar, confirmar=MagicMock())
    assert res.status == "incerto"
    enviar.assert_called_once()
    r.marcar_incerto.assert_called_once()


def test_erro_reserva_nunca_envia():
    r = repo()
    r.reservar_ctrc.side_effect = ConnectionError("MySQL indisponível")
    enviar = MagicMock()
    try:
        PortaoOC73(r).processar(**args(), enviar=enviar, confirmar=MagicMock())
    except ConnectionError:
        pass
    else:
        raise AssertionError("Deveria falhar fechado")
    enviar.assert_not_called()

def test_sem_referencia_persistida_nunca_envia():
    r = repo()
    r.reservar_ctrc.return_value.pop("referencia")

    enviar = MagicMock()
    confirmar = MagicMock()

    resultado = PortaoOC73(r).processar(
        **args(),
        enviar=enviar,
        confirmar=confirmar,
    )

    assert resultado.status == "bloqueado_sem_referencia"
    enviar.assert_not_called()
    confirmar.assert_not_called()
    r.iniciar_envio.assert_not_called()

def test_falha_ao_persistir_incerto_exige_intervencao_manual():
    r = repo()
    r.marcar_incerto.side_effect = RuntimeError(
        "MySQL indisponivel"
    )

    enviar = MagicMock(side_effect=TimeoutError("Timeout POST"))
    confirmar = MagicMock()

    with pytest.raises(
        RuntimeError,
        match="intervenção manual necessária",
    ):
        PortaoOC73(r).processar(
            **args(),
            enviar=enviar,
            confirmar=confirmar,
        )

    enviar.assert_called_once()
    confirmar.assert_not_called()
    r.marcar_incerto.assert_called_once()
    r.confirmar_lancamento.assert_not_called()


def test_falha_ao_confirmar_persistencia_nao_reenvia():
    r = repo()
    r.confirmar_lancamento.side_effect = RuntimeError(
        "Falha ao persistir confirmacao"
    )

    enviar = MagicMock()

    def confirmar(referencia):
        return {
            "codigo": "73",
            "complemento": f"BOT OC73 | REF:{referencia}",
        }

    resultado = PortaoOC73(r).processar(
        **args(),
        enviar=enviar,
        confirmar=confirmar,
    )

    assert resultado.status == "incerto"
    enviar.assert_called_once_with(REFERENCIA_TESTE)
    r.confirmar_lancamento.assert_called_once()
    r.marcar_incerto.assert_called_once()
