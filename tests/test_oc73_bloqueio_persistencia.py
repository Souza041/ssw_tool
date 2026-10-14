"""Testes offline de bloqueio OC73; nenhum acesso a SSW ou MySQL real."""
from datetime import date
from unittest.mock import MagicMock

import pytest

from modules.ocorrencia_73.gate import PortaoOC73
from modules.ocorrencia_73.service import Ocorrencia73Service

DATA = date(2026, 10, 12)


def processar(repo, enviar, confirmar=None):
    return PortaoOC73(repo).processar(
        execucao_id=101, serie="CWB", numero="100001", fluxo="CWB",
        data_inicial=DATA, data_final=DATA,
        enviar=enviar, confirmar=confirmar or MagicMock(return_value=None),
    )


def reserva(*, criada=True):
    return {
        "id": 501, "status": "reservado", "execucao_id": 101,
        "reserva_criada": criada, "reserva_existente": not criada,
        "autorizada_para_envio": False,
    }


def test_reserva_existente_bloqueia_post():
    repo = MagicMock()
    repo.reservar_ctrc.return_value = reserva(criada=False)
    enviar = MagicMock()
    resultado = processar(repo, enviar)
    assert resultado.status == "bloqueado_reserva_existente"
    enviar.assert_not_called()
    repo.iniciar_envio.assert_not_called()


def test_falha_na_reserva_bloqueia_post():
    repo = MagicMock()
    repo.reservar_ctrc.side_effect = ConnectionError("MySQL indisponivel")
    enviar = MagicMock()
    with pytest.raises(ConnectionError, match="MySQL"):
        processar(repo, enviar)
    enviar.assert_not_called()


def test_transicao_nao_autorizada_bloqueia_post():
    repo = MagicMock()
    repo.reservar_ctrc.return_value = reserva()
    repo.iniciar_envio.return_value = False
    enviar = MagicMock()
    resultado = processar(repo, enviar)
    assert resultado.status == "bloqueado_transicao"
    enviar.assert_not_called()


def test_timeout_apos_envio_marca_incerto_sem_repetir():
    repo = MagicMock()
    repo.reservar_ctrc.return_value = reserva()
    repo.iniciar_envio.return_value = True
    enviar = MagicMock(side_effect=TimeoutError("resposta SSW perdida"))
    resultado = processar(repo, enviar)
    assert resultado.status == "incerto"
    enviar.assert_called_once()
    repo.marcar_incerto.assert_called_once()
    repo.confirmar_lancamento.assert_not_called()


def test_falha_ao_persistir_incerto_interrompe():
    repo = MagicMock()
    repo.reservar_ctrc.return_value = reserva()
    repo.iniciar_envio.return_value = True
    repo.marcar_incerto.side_effect = ConnectionError("MySQL indisponivel")
    enviar = MagicMock(side_effect=TimeoutError("SSW timeout"))
    with pytest.raises(RuntimeError, match="intervenção manual"):
        processar(repo, enviar)
    enviar.assert_called_once()


def test_falha_ao_iniciar_execucao_nao_aciona_fluxo(monkeypatch):
    monkeypatch.setattr("modules.ocorrencia_73.service.DRY_RUN", False)
    repo = MagicMock()
    repo.iniciar_execucao.side_effect = ConnectionError("MySQL indisponivel")
    service = Ocorrencia73Service(repository=repo)
    interno = MagicMock()
    monkeypatch.setattr(service, "_executar_interno", interno)
    with pytest.raises(ConnectionError, match="MySQL"):
        service.executar(DATA)
    interno.assert_not_called()
