
from datetime import date
from unittest.mock import MagicMock

from pymysql.err import IntegrityError

import pytest

from modules.ocorrencia_73.repository import (
    Ocorrencia73Repository,
)


DATA = date(2026, 10, 12)


@pytest.fixture
def ambiente():
    """
    Banco completamente simulado.
    Nenhuma conexão real é realizada.
    """
    conexao = MagicMock()
    cursor = MagicMock()

    conexao.cursor.return_value.__enter__.return_value = cursor

    factory = MagicMock(return_value=conexao)

    repo = Ocorrencia73Repository(
        connection_factory=factory
    )

    return repo, conexao, cursor, factory


def configurar_reserva(
    cursor,
    *,
    tentativa_id=10,
    execucao_id=1,
    status="reservado",
):
    cursor.lastrowid = tentativa_id
    cursor.fetchone.return_value = {
        "id": tentativa_id,
        "execucao_id": execucao_id,
        "status": status,
    }


def reservar(repo, execucao_id=1):
    return repo.reservar_ctrc(
        execucao_id=execucao_id,
        serie="cwb",
        numero="123456",
        fluxo="CWB",
        data_inicial=DATA,
        data_final=DATA,
    )


def test_iniciar_execucao(ambiente):
    repo, conexao, cursor, factory = ambiente

    cursor.lastrowid = 42

    resultado = repo.iniciar_execucao(
        data_referencia=DATA,
        triggered_by="pytest",
        dry_run=True,
    )

    assert resultado == 42
    assert cursor.execute.call_count == 1
    conexao.commit.assert_called_once()
    conexao.close.assert_called_once()
    factory.assert_called_once()


def test_finalizar_execucao(ambiente):
    repo, conexao, cursor, _ = ambiente

    cursor.rowcount = 1

    repo.finalizar_execucao(
        execucao_id=42,
        status="concluido",
        total_relatorio=100,
        total_filtrado=5,
        total_lancado=0,
    )

    sql = cursor.execute.call_args.args[0]

    assert "UPDATE oc73_execucoes" in sql
    assert "finalizado_em" in sql
    conexao.commit.assert_called_once()


def test_finalizar_execucao_inexistente(ambiente):
    repo, conexao, cursor, _ = ambiente

    cursor.rowcount = 0

    with pytest.raises(RuntimeError, match="não encontrada"):
        repo.finalizar_execucao(
            execucao_id=999,
            status="erro",
        )

    conexao.rollback.assert_called_once()
    conexao.close.assert_called_once()


def test_buscar_tentativa(ambiente):
    repo, conexao, cursor, _ = ambiente

    cursor.fetchone.return_value = {
        "id": 10,
        "serie": "CWB",
        "numero": "123456",
        "status": "reservado",
    }

    resultado = repo.buscar_tentativa(
        serie="cwb",
        numero="123456",
    )

    assert resultado["id"] == 10

    parametros = cursor.execute.call_args.args[1]

    assert parametros == ("CWB", "123456")
    conexao.commit.assert_called_once()


def test_reserva_nova(ambiente):
    repo, conexao, cursor, _ = ambiente

    configurar_reserva(
        cursor,
        tentativa_id=10,
        execucao_id=1,
    )

    cursor.rowcount = 1

    resultado = reservar(repo)

    assert resultado["id"] == 10
    assert resultado["status"] == "reservado"
    assert resultado["reserva_criada"] is True
    assert resultado["reserva_existente"] is False
    assert resultado["autorizada_para_envio"] is False

    sql = cursor.execute.call_args_list[0].args[0]

    assert "INSERT INTO oc73_tentativas" in sql
    assert "INSERT IGNORE" not in sql

    conexao.commit.assert_called_once()


def test_reserva_existente_outra_execucao(ambiente):
    repo, conexao, cursor, _ = ambiente

    configurar_reserva(
        cursor,
        tentativa_id=10,
        execucao_id=99,
        status="incerto",
    )

    cursor.execute.side_effect = [
        IntegrityError(1062, "Duplicate entry"),
        None,
    ]

    resultado = reservar(repo, execucao_id=1)

    assert resultado["id"] == 10
    assert resultado["reserva_criada"] is False
    assert resultado["reserva_existente"] is True
    assert resultado["status"] == "incerto"
    assert resultado["autorizada_para_envio"] is False

    conexao.commit.assert_called_once()

def test_iniciar_envio_transicao_atomica(ambiente):
    repo, conexao, cursor, _ = ambiente

    cursor.rowcount = 1

    resultado = repo.iniciar_envio(
        tentativa_id=10,
        execucao_id=1,
    )

    assert resultado is True

    sql = cursor.execute.call_args.args[0]

    assert "status = 'reservado'" in sql
    assert "envio_iniciado_em IS NULL" in sql

    conexao.commit.assert_called_once()

def test_reserva_mesma_execucao_nao_autoriza_envio(
    ambiente,
):
    repo, _, cursor, _ = ambiente

    configurar_reserva(
        cursor,
        tentativa_id=10,
        execucao_id=1,
        status="reservado",
    )

    cursor.execute.side_effect = [
        IntegrityError(1062, "Duplicate entry"),
        None,
    ]

    resultado = reservar(repo, execucao_id=1)

    assert resultado["reserva_criada"] is False
    assert resultado["reserva_existente"] is True
    assert resultado["autorizada_para_envio"] is False

def test_iniciar_envio_duplicado_bloqueado(ambiente):
    repo, conexao, cursor, _ = ambiente

    cursor.rowcount = 0

    resultado = repo.iniciar_envio(
        tentativa_id=10,
        execucao_id=1,
    )

    assert resultado is False

    conexao.commit.assert_called_once()


def test_confirmar_e_marcar_incerto(ambiente):
    repo, conexao, cursor, _ = ambiente

    cursor.rowcount = 1

    repo.marcar_incerto(
        tentativa_id=10,
        mensagem_erro="Timeout após envio",
    )

    repo.confirmar_lancamento(
        tentativa_id=10,
        resposta_ssw="OC73 localizada no histórico",
    )

    assert cursor.execute.call_count == 2

    sql_incerto = cursor.execute.call_args_list[0].args[0]
    sql_confirmacao = cursor.execute.call_args_list[1].args[0]

    assert "status = 'incerto'" in sql_incerto
    assert "status = 'confirmado'" in sql_confirmacao

    assert conexao.commit.call_count == 2

def test_reserva_duplicada_nao_altera_registro(
    ambiente,
):
    repo, conexao, cursor, _ = ambiente

    configurar_reserva(
        cursor,
        tentativa_id=10,
        execucao_id=99,
        status="confirmado",
    )

    cursor.execute.side_effect = [
        IntegrityError(1062, "Duplicate entry"),
        None,
    ]

    resultado = reservar(repo, execucao_id=2)

    assert resultado["reserva_criada"] is False
    assert resultado["reserva_existente"] is True
    assert resultado["status"] == "confirmado"

    sql = cursor.execute.call_args_list[0].args[0]

    assert "ON DUPLICATE KEY UPDATE" not in sql
    assert "INSERT IGNORE" not in sql
    assert "INSERT INTO oc73_tentativas" in sql

    conexao.commit.assert_called_once()

def test_falha_banco_executa_rollback(ambiente):
    repo, conexao, cursor, _ = ambiente

    cursor.execute.side_effect = RuntimeError(
        "Conexão MySQL indisponível"
    )

    with pytest.raises(
        RuntimeError,
        match="MySQL indisponível",
    ):
        reservar(repo)

    conexao.rollback.assert_called_once()
    conexao.close.assert_called_once()
    conexao.commit.assert_not_called()


def test_duas_execucoes_disputam_mesmo_ctrc():
    """
    Simula duas execuções tentando reservar
    o mesmo CTRC.

    Não realiza concorrência real no MySQL.
    """

    def criar_ambiente(duplicado, execucao_id_banco):
        conexao = MagicMock()
        cursor = MagicMock()

        conexao.cursor.return_value.__enter__.return_value = (
            cursor
        )

        cursor.fetchone.return_value = {
            "id": 10,
            "execucao_id": execucao_id_banco,
            "status": "reservado",
        }

        if duplicado:
            cursor.execute.side_effect = [
                IntegrityError(1062, "Duplicate entry"),
                None,
            ]

        repo = Ocorrencia73Repository(
            connection_factory=lambda: conexao
        )

        return repo

    repo_a = criar_ambiente(
        duplicado=False,
        execucao_id_banco=1,
    )

    repo_b = criar_ambiente(
        duplicado=True,
        execucao_id_banco=1,
    )

    primeira = reservar(repo_a, execucao_id=1)
    segunda = reservar(repo_b, execucao_id=2)

    assert primeira["reserva_criada"] is True
    assert segunda["reserva_criada"] is False

    assert primeira["reserva_existente"] is False
    assert segunda["reserva_existente"] is True

    assert primeira["autorizada_para_envio"] is False
    assert segunda["autorizada_para_envio"] is False

def test_erro_integridade_nao_duplicado_interrompe(
    ambiente,
):
    repo, conexao, cursor, _ = ambiente

    cursor.execute.side_effect = IntegrityError(
        1452,
        "Cannot add or update a child row",
    )

    with pytest.raises(IntegrityError) as erro:
        reservar(repo)

    assert erro.value.args[0] == 1452

    conexao.rollback.assert_called_once()
    conexao.commit.assert_not_called()
    conexao.close.assert_called_once()
