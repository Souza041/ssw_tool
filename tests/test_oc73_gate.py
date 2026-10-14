from datetime import date
from unittest.mock import MagicMock
from modules.ocorrencia_73.gate import PortaoOC73

D = date(2026, 10, 12)


def args():
    return dict(execucao_id=9, serie="CWB", numero="100010", fluxo="CWB",
                data_inicial=D, data_final=D)


def repo(nova=True, transicao=True):
    r = MagicMock()
    r.reservar_ctrc.return_value = dict(id=15, execucao_id=9,
                                         status="reservado", reserva_criada=nova)
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


def test_confirmado_so_com_codigo_73():
    r = repo()
    enviar = MagicMock()
    res = PortaoOC73(r).processar(**args(), enviar=enviar,
                                  confirmar=lambda: {"codigo": "73"})
    assert res.status == "confirmado"
    r.confirmar_lancamento.assert_called_once()
    r.marcar_incerto.assert_not_called()


def test_sem_confirmacao_fica_incerto_sem_retry():
    r = repo()
    enviar = MagicMock()
    res = PortaoOC73(r).processar(**args(), enviar=enviar, confirmar=lambda: None)
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
