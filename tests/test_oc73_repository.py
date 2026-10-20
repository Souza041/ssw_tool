
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
        "referencia": "REFERENCIA_TESTE",
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


def test_reserva_execucao_agendada_distribuida(ambiente):
    repo, conexao, cursor, factory = ambiente
    cursor.fetchone.side_effect = [
        {"adquirida": 1},
        None,
    ]
    cursor.lastrowid = 77

    resultado = repo.reservar_execucao_agendada(
        data_referencia=DATA,
        fluxo="CWB",
        dry_run=True,
    )

    assert resultado == {
        "reservada": True,
        "motivo": "reservada",
        "execucao_id": 77,
        "triggered_by": "scheduler:CWB",
    }
    sqls = [chamada.args[0] for chamada in cursor.execute.call_args_list]
    assert "GET_LOCK" in sqls[0]
    assert "FROM oc73_execucoes" in sqls[1]
    assert "INSERT INTO oc73_execucoes" in sqls[2]
    assert "RELEASE_LOCK" in sqls[3]
    conexao.commit.assert_called_once()
    conexao.close.assert_called_once()
    factory.assert_called_once()


def test_reserva_agendada_existente_bloqueia_repeticao(ambiente):
    repo, conexao, cursor, _ = ambiente
    cursor.fetchone.side_effect = [
        {"adquirida": 1},
        {"id": 55, "status": "concluido"},
    ]

    resultado = repo.reservar_execucao_agendada(
        data_referencia=DATA,
        fluxo="BIG",
        dry_run=False,
    )

    assert resultado["reservada"] is False
    assert resultado["motivo"] == "execucao_ja_registrada"
    assert resultado["execucao_id"] == 55
    conexao.rollback.assert_called_once()
    conexao.commit.assert_not_called()
    conexao.close.assert_called_once()


def test_execucao_em_andamento_bloqueia_reinicio(ambiente):
    repo, conexao, cursor, _ = ambiente
    cursor.fetchone.side_effect = [
        {"adquirida": 1},
        {"id": 56, "status": "executando"},
    ]

    resultado = repo.reservar_execucao_agendada(
        data_referencia=DATA,
        fluxo="CWB",
        dry_run=True,
    )

    assert resultado["reservada"] is False
    assert resultado["status"] == "executando"
    assert resultado["execucao_id"] == 56
    conexao.commit.assert_not_called()


@pytest.mark.parametrize("resposta_lock", [{"adquirida": 0}, {"adquirida": None}, {}])
def test_reserva_agendada_com_trava_ocupada_falha_fechada(ambiente, resposta_lock):
    repo, conexao, cursor, _ = ambiente
    cursor.fetchone.return_value = resposta_lock

    resultado = repo.reservar_execucao_agendada(
        data_referencia=DATA,
        fluxo="CWB",
        dry_run=False,
    )

    assert resultado["reservada"] is False
    assert resultado["motivo"] == "trava_ocupada"
    assert cursor.execute.call_count == 1
    conexao.commit.assert_not_called()
    conexao.close.assert_called_once()


def test_excecao_durante_reserva_libera_trava_e_conexao(ambiente):
    repo, conexao, cursor, _ = ambiente
    cursor.fetchone.return_value = {"adquirida": 1}
    cursor.execute.side_effect = [
        None,
        RuntimeError("falha durante SELECT"),
        None,
    ]

    with pytest.raises(RuntimeError, match="falha durante SELECT"):
        repo.reservar_execucao_agendada(
            data_referencia=DATA,
            fluxo="CWB",
            dry_run=True,
        )

    sqls = [chamada.args[0] for chamada in cursor.execute.call_args_list]
    assert "GET_LOCK" in sqls[0]
    assert "FROM oc73_execucoes" in sqls[1]
    assert "RELEASE_LOCK" in sqls[2]
    conexao.rollback.assert_called_once()
    conexao.close.assert_called_once()


def test_perda_de_conexao_no_commit_falha_fechada(ambiente):
    repo, conexao, cursor, _ = ambiente
    cursor.fetchone.side_effect = [
        {"adquirida": 1},
        None,
    ]
    cursor.lastrowid = 90
    conexao.commit.side_effect = ConnectionError("conexão perdida")

    with pytest.raises(ConnectionError, match="conexão perdida"):
        repo.reservar_execucao_agendada(
            data_referencia=DATA,
            fluxo="CWB",
            dry_run=False,
        )

    conexao.rollback.assert_called_once()
    conexao.close.assert_called_once()


def test_falha_no_release_fecha_conexao_sem_perder_reserva(ambiente):
    repo, conexao, cursor, _ = ambiente
    cursor.fetchone.side_effect = [
        {"adquirida": 1},
        None,
    ]
    cursor.lastrowid = 91
    cursor.execute.side_effect = [
        None,
        None,
        None,
        RuntimeError("RELEASE_LOCK indisponível"),
    ]

    resultado = repo.reservar_execucao_agendada(
        data_referencia=DATA,
        fluxo="BIG",
        dry_run=True,
    )

    assert resultado["reservada"] is True
    assert resultado["execucao_id"] == 91
    conexao.commit.assert_called_once()
    conexao.close.assert_called_once()


def test_commit_ocorre_antes_do_release_lock(ambiente):
    repo, conexao, cursor, _ = ambiente
    eventos = []
    respostas = iter([{"adquirida": 1}, None])

    def executar(sql, parametros=None):
        if "GET_LOCK" in sql:
            eventos.append("get_lock")
        elif "INSERT INTO oc73_execucoes" in sql:
            eventos.append("insert")
        elif "RELEASE_LOCK" in sql:
            eventos.append("release_lock")

    cursor.execute.side_effect = executar
    cursor.fetchone.side_effect = lambda: next(respostas)
    cursor.lastrowid = 92
    conexao.commit.side_effect = lambda: eventos.append("commit")

    repo.reservar_execucao_agendada(
        data_referencia=DATA,
        fluxo="CWB",
        dry_run=True,
    )

    assert eventos == [
        "get_lock",
        "insert",
        "commit",
        "release_lock",
    ]


def test_cwb_e_big_usam_nomes_de_trava_independentes():
    nomes = []

    def executar_fluxo(fluxo):
        conexao = MagicMock()
        cursor = MagicMock()
        conexao.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = {"adquirida": 0}
        repo = Ocorrencia73Repository(
            connection_factory=lambda: conexao
        )
        repo.reservar_execucao_agendada(
            data_referencia=DATA,
            fluxo=fluxo,
            dry_run=True,
        )
        nomes.append(cursor.execute.call_args.args[1][0])

    executar_fluxo("CWB")
    executar_fluxo("BIG")

    assert nomes == [
        "ssw_tool:oc73:CWB:2026-10-12",
        "ssw_tool:oc73:BIG:2026-10-12",
    ]


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
        IntegrityError(
            1062,
            "Duplicate entry 'CWB-123456-73' for key 'uq_oc73_ctrc'"
        ),
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
        IntegrityError(
            1062,
            "Duplicate entry 'CWB-123456-73' for key 'uq_oc73_ctrc'"
        ),
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
        IntegrityError(
            1062,
            "Duplicate entry 'CWB-123456-73' for key 'uq_oc73_ctrc'"
        ),
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
            "referencia": "OC73-A1B2C3D4E5F60718293A",
            "execucao_id": execucao_id_banco,
            "status": "reservado",
        }

        if duplicado:
            cursor.execute.side_effect = [
                IntegrityError(
                    1062,
                    "Duplicate entry 'CWB-123456-73' for key 'uq_oc73_ctrc'"
                ),
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

def test_colisao_referencia_global_aborta_reserva(ambiente):
    repo, conexao, cursor, _ = ambiente

    cursor.execute.side_effect = IntegrityError(
        1062,
        "Duplicate entry 'OC73-ABC' "
        "for key 'uq_oc73_referencia'",
    )

    with pytest.raises(RuntimeError, match="Colisão de referência"):
        reservar(repo)

    conexao.rollback.assert_called_once()
    conexao.commit.assert_not_called()
    conexao.close.assert_called_once()
