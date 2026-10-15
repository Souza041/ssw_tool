
"""
Testes reais e isolados do banco MySQL da OC73.

NÃO acessa o SSW.
NÃO executa lançamentos.
NÃO altera o scheduler.

Executar somente com:
    OC73_MYSQL_TEST=SIM

Todos os CTRCs usados são fictícios.
"""

import os
import multiprocessing
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier

import pytest

from modules.ocorrencia_73.repository import (
    Ocorrencia73Repository,
)

from modules.ocorrencia_73.reconciliacao import ReconciliadorOC73
from modules.ocorrencia_73.gate import PortaoOC73

pytestmark = pytest.mark.skipif(
    os.getenv("OC73_MYSQL_TEST") != "SIM",
    reason="Teste MySQL real exige OC73_MYSQL_TEST=SIM",
)


def validar_destino_laboratorio(conexao):
    """Bloqueia qualquer banco que não seja o laboratório local OC73."""
    esperado = {
        "host": "127.0.0.1",
        "port": "3308",
        "database": "oc73_concorrencia_test",
        "user": "oc73_tester",
    }
    recebido = {
        "host": os.getenv("AUTOMATION_DB_HOST", ""),
        "port": os.getenv("AUTOMATION_DB_PORT", ""),
        "database": os.getenv("AUTOMATION_DB_NAME", ""),
        "user": os.getenv("AUTOMATION_DB_USER", ""),
    }
    if recebido != esperado:
        raise RuntimeError("OC73: destino MySQL fora do laboratório autorizado")

    with conexao.cursor() as cursor:
        cursor.execute("SELECT @@port AS porta, @@datadir AS diretorio, DATABASE() AS banco, CURRENT_USER() AS usuario")
        info = cursor.fetchone()
    diretorio = str(info["diretorio"]).replace("\\", "/").lower()
    if not (
        int(info["porta"]) == 3308
        and info["banco"] == "oc73_concorrencia_test"
        and info["usuario"] == "oc73_tester@127.0.0.1"
        and "/oc73-lab/data" in diretorio
    ):
        raise RuntimeError("OC73: identidade real do servidor não corresponde ao laboratório")


def novo_identificador():
    return uuid.uuid4().hex[:20].upper()


def criar_repository():
    """
    Cada operação utiliza uma conexão independente.
    Timeout de lock limitado à sessão atual.
    """
    def conectar():
        repo_base = Ocorrencia73Repository()
        # A validação acontece antes de qualquer escrita, inclusive SET SESSION.
        if any((
            os.getenv("AUTOMATION_DB_HOST") != "127.0.0.1",
            os.getenv("AUTOMATION_DB_PORT") != "3308",
            os.getenv("AUTOMATION_DB_NAME") != "oc73_concorrencia_test",
            os.getenv("AUTOMATION_DB_USER") != "oc73_tester",
        )):
            raise RuntimeError("OC73: configuração de laboratório obrigatória")
        conexao = repo_base._conectar()
        try:
            validar_destino_laboratorio(conexao)
        except Exception:
            conexao.close()
            raise

        try:
            with conexao.cursor() as cursor:
                cursor.execute(
                    "SET SESSION innodb_lock_wait_timeout = 5"
                )
            conexao.commit()
        except Exception:
            conexao.close()
            raise

        return conexao

    return Ocorrencia73Repository(
        connection_factory=conectar
    )


def _data_scheduler_unica():
    deslocamento = int(uuid.uuid4().hex[:6], 16) % 3000
    return date(2090, 1, 1) + timedelta(days=deslocamento)


def _reservar_scheduler_em_processo(
    data_iso,
    fluxo,
    sinal_inicio,
    resultados,
):
    try:
        if not sinal_inicio.wait(timeout=15):
            raise TimeoutError("barreira multiprocesso não liberada")
        resultado = criar_repository().reservar_execucao_agendada(
            data_referencia=date.fromisoformat(data_iso),
            fluxo=fluxo,
            dry_run=True,
        )
        resultados.put(("ok", resultado))
    except BaseException as exc:
        resultados.put(("erro", f"{type(exc).__name__}: {exc}"))


def _reservar_scheduler_e_aguardar_queda(
    data_iso,
    fluxo,
    canal,
):
    try:
        resultado = criar_repository().reservar_execucao_agendada(
            data_referencia=date.fromisoformat(data_iso),
            fluxo=fluxo,
            dry_run=True,
        )
        canal.send(("ok", resultado))
        canal.close()
        multiprocessing.Event().wait(60)
    except BaseException as exc:
        canal.send(("erro", f"{type(exc).__name__}: {exc}"))
        canal.close()


def _executar_reservas_em_processos(data_referencia, fluxos):
    contexto = multiprocessing.get_context("spawn")
    sinal_inicio = contexto.Event()
    resultados = contexto.Queue()
    processos = [
        contexto.Process(
            target=_reservar_scheduler_em_processo,
            args=(
                data_referencia.isoformat(),
                fluxo,
                sinal_inicio,
                resultados,
            ),
        )
        for fluxo in fluxos
    ]

    try:
        for processo in processos:
            processo.start()
        sinal_inicio.set()

        recebidos = [resultados.get(timeout=30) for _ in processos]
        for processo in processos:
            processo.join(timeout=30)
            assert processo.exitcode == 0
    finally:
        for processo in processos:
            if processo.is_alive():
                processo.terminate()
                processo.join(timeout=15)

    erros = [detalhe for estado, detalhe in recebidos if estado == "erro"]
    assert erros == []
    return [detalhe for _, detalhe in recebidos]


def remover_reservas_scheduler(repo, data_referencia, execucoes):
    ids = sorted({
        int(execucao_id)
        for execucao_id in execucoes
        if execucao_id is not None
    })
    if not ids:
        return

    with repo.transacao() as conexao:
        validar_destino_laboratorio(conexao)
        with conexao.cursor() as cursor:
            placeholders = ", ".join(["%s"] * len(ids))
            cursor.execute(
                f"""
                DELETE FROM oc73_execucoes
                WHERE id IN ({placeholders})
                  AND data_referencia = %s
                  AND triggered_by IN ('scheduler:CWB', 'scheduler:BIG')
                  AND dry_run = 1
                """,
                tuple(ids) + (data_referencia,),
            )


class _CursorComFalha:
    def __init__(self, cursor, trecho_sql):
        self._cursor = cursor
        self._trecho_sql = trecho_sql

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._cursor.close()

    def execute(self, sql, parametros=None):
        if self._trecho_sql in sql:
            raise RuntimeError(f"falha injetada em {self._trecho_sql}")
        return self._cursor.execute(sql, parametros)

    def fetchone(self):
        return self._cursor.fetchone()

    @property
    def lastrowid(self):
        return self._cursor.lastrowid


class _ConexaoComFalhaSQL:
    def __init__(self, conexao, trecho_sql):
        self._conexao = conexao
        self._trecho_sql = trecho_sql

    def cursor(self):
        return _CursorComFalha(
            self._conexao.cursor(),
            self._trecho_sql,
        )

    def commit(self):
        return self._conexao.commit()

    def rollback(self):
        return self._conexao.rollback()

    def close(self):
        return self._conexao.close()


class _ConexaoPerdidaNoCommit(_ConexaoComFalhaSQL):
    def __init__(self, conexao):
        super().__init__(conexao, trecho_sql="")

    def cursor(self):
        return self._conexao.cursor()

    def commit(self):
        self._conexao.close()
        raise ConnectionError("conexão perdida antes do commit")


def remover_registros_teste(
    repo,
    execucoes,
    serie,
    numero,
):
    """
    Remove apenas registros vinculados aos IDs
    criados por este teste.

    Não utiliza DELETE sem restrição.
    """
    if not execucoes:
        return

    with repo.transacao() as conexao:
        with conexao.cursor() as cursor:
            placeholders = ", ".join(
                ["%s"] * len(execucoes)
            )

            cursor.execute(
                f"""
                DELETE FROM oc73_tentativas
                WHERE execucao_id IN ({placeholders})
                  AND serie = %s
                  AND numero = %s
                  AND codigo_ocorrencia = 73
                """,
                tuple(execucoes) + (serie, numero),
            )

            cursor.execute(
                f"""
                DELETE FROM oc73_execucoes
                WHERE id IN ({placeholders})
                  AND triggered_by = %s
                """,
                tuple(execucoes) + ("pytest_mysql",),
            )


def test_concorrencia_real_reserva_unica():
    """
    Duas conexões reais tentam reservar o mesmo CTRC.

    Exatamente uma deve criar a reserva.
    A outra deve encontrar a reserva existente.
    """
    repo = criar_repository()

    serie = "TST"
    numero = novo_identificador()
    data = date(2026, 10, 12)
    execucoes = []

    try:
        for _ in range(2):
            execucao_id = repo.iniciar_execucao(
                data_referencia=data,
                triggered_by="pytest_mysql",
                dry_run=True,
            )
            execucoes.append(execucao_id)

        barreira = Barrier(2, timeout=10)

        def tentar_reservar(execucao_id):
            repo_thread = criar_repository()

            barreira.wait()

            return repo_thread.reservar_ctrc(
                execucao_id=execucao_id,
                serie=serie,
                numero=numero,
                fluxo="CWB",
                data_inicial=data,
                data_final=data,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [
                pool.submit(tentar_reservar, execucoes[0]),
                pool.submit(tentar_reservar, execucoes[1]),
            ]

            resultados = [
                futuro.result(timeout=20)
                for futuro in futuros
            ]

        criadas = [
            item for item in resultados
            if item["reserva_criada"]
        ]

        existentes = [
            item for item in resultados
            if item["reserva_existente"]
        ]

        assert len(criadas) == 1
        assert len(existentes) == 1

        assert (
            criadas[0]["id"]
            == existentes[0]["id"]
        )

        assert all(
            not item["autorizada_para_envio"]
            for item in resultados
        )

        with repo.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM oc73_tentativas
                    WHERE serie = %s
                      AND numero = %s
                      AND codigo_ocorrencia = 73
                    """,
                    (serie, numero),
                )

                total = cursor.fetchone()["total"]

        assert total == 1

        print(
            "\n[OK] Concorrência real: "
            "1 reserva criada, 1 duplicidade bloqueada."
        )

    finally:
        remover_registros_teste(
            repo,
            execucoes,
            serie,
            numero,
        )


def test_rollback_real_nao_persiste_tentativa():
    """
    Uma inserção seguida de exceção deve sofrer
    rollback e não permanecer no banco.
    """
    repo = criar_repository()

    serie = "TST"
    numero = novo_identificador()
    data = date(2026, 10, 12)
    execucoes = []

    try:
        execucao_id = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(execucao_id)

        with pytest.raises(
            RuntimeError,
            match="rollback intencional",
        ):
            with repo.transacao() as conexao:
                with conexao.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO oc73_tentativas (
                            execucao_id,
                            serie,
                            numero,
                            codigo_ocorrencia,
                            fluxo,
                            data_inicial_pesquisa,
                            data_final_pesquisa,
                            status
                        )
                        VALUES (
                            %s, %s, %s, 73,
                            'CWB', %s, %s, 'reservado'
                        )
                        """,
                        (
                            execucao_id,
                            serie,
                            numero,
                            data,
                            data,
                        ),
                    )

                    raise RuntimeError(
                        "rollback intencional"
                    )

        tentativa = repo.buscar_tentativa(
            serie=serie,
            numero=numero,
        )

        assert tentativa is None

        print(
            "\n[OK] Rollback real: "
            "tentativa não persistiu."
        )

    finally:
        remover_registros_teste(
            repo,
            execucoes,
            serie,
            numero,
        )

def test_concorrencia_real_inicio_envio_unico():
    repo = criar_repository()

    serie = "TST"
    numero = novo_identificador()
    data = date(2026, 10, 12)
    execucoes = []

    try:
        execucao_id = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(execucao_id)

        reserva = repo.reservar_ctrc(
            execucao_id=execucao_id,
            serie=serie,
            numero=numero,
            fluxo="CWB",
            data_inicial=data,
            data_final=data,
        )

        assert reserva["reserva_criada"] is True
        tentativa_id = reserva["id"]

        barreira = Barrier(2, timeout=10)

        def iniciar():
            repo_thread = criar_repository()
            barreira.wait()

            return repo_thread.iniciar_envio(
                tentativa_id=tentativa_id,
                execucao_id=execucao_id,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [
                pool.submit(iniciar)
                for _ in range(2)
            ]

            resultados = [
                futuro.result(timeout=20)
                for futuro in futuros
            ]

        assert resultados.count(True) == 1
        assert resultados.count(False) == 1

        tentativa = repo.buscar_tentativa(
            serie=serie,
            numero=numero,
        )

        assert tentativa["status"] == "envio_iniciado"
        assert tentativa["envio_iniciado_em"] is not None

        print(
            "\n[OK] Concorrência real: "
            "somente uma transição para envio_iniciado."
        )

    finally:
        remover_registros_teste(
            repo,
            execucoes,
            serie,
            numero,
        )

def test_estado_incerto_nao_permite_reenvio():
    repo = criar_repository()
    serie = "TST"
    numero = novo_identificador()
    data = date(2026, 10, 12)
    execucoes = []

    try:
        execucao_id = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(execucao_id)

        reserva = repo.reservar_ctrc(
            execucao_id=execucao_id,
            serie=serie,
            numero=numero,
            fluxo="CWB",
            data_inicial=data,
            data_final=data,
        )

        tentativa_id = reserva["id"]

        assert repo.iniciar_envio(
            tentativa_id,
            execucao_id,
        ) is True

        repo.marcar_incerto(
            tentativa_id,
            "Timeout simulado após início do envio",
        )

        tentativa = repo.buscar_tentativa(serie, numero)

        assert tentativa["status"] == "incerto"

        # Não pode autorizar novo envio.
        assert repo.iniciar_envio(
            tentativa_id,
            execucao_id,
        ) is False

        # Nova execução também não pode reservar o CTRC.
        segunda_execucao = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(segunda_execucao)

        segunda_reserva = repo.reservar_ctrc(
            execucao_id=segunda_execucao,
            serie=serie,
            numero=numero,
            fluxo="CWB",
            data_inicial=data,
            data_final=data,
        )

        assert segunda_reserva["reserva_existente"] is True
        assert segunda_reserva["autorizada_para_envio"] is False

    finally:
        remover_registros_teste(
            repo, execucoes, serie, numero
        )


def test_confirmacao_de_incerto_sem_reenvio():
    repo = criar_repository()
    serie = "TST"
    numero = novo_identificador()
    data = date(2026, 10, 12)
    execucoes = []

    try:
        execucao_id = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(execucao_id)

        reserva = repo.reservar_ctrc(
            execucao_id=execucao_id,
            serie=serie,
            numero=numero,
            fluxo="BIG",
            data_inicial=data,
            data_final=data,
        )

        tentativa_id = reserva["id"]

        assert repo.iniciar_envio(
            tentativa_id,
            execucao_id,
        ) is True

        repo.marcar_incerto(
            tentativa_id,
            "Resposta do SSW não confirmada",
        )

        # Simula confirmação obtida posteriormente
        # pela reconciliação do histórico OP101.
        repo.confirmar_lancamento(
            tentativa_id,
            resposta_ssw="Confirmado por reconciliação simulada",
        )

        tentativa = repo.buscar_tentativa(serie, numero)

        assert tentativa["status"] == "confirmado"
        assert tentativa["confirmado_em"] is not None

        assert repo.iniciar_envio(
            tentativa_id,
            execucao_id,
        ) is False

    finally:
        remover_registros_teste(
            repo, execucoes, serie, numero
        )

def test_queda_apos_inicio_envio_nao_reenvia():
    repo = criar_repository()
    serie = "TST"
    numero = novo_identificador()
    data = date(2026, 10, 12)
    execucoes = []

    try:
        primeira_execucao = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(primeira_execucao)

        reserva = repo.reservar_ctrc(
            execucao_id=primeira_execucao,
            serie=serie,
            numero=numero,
            fluxo="CWB",
            data_inicial=data,
            data_final=data,
        )

        tentativa_id = reserva["id"]

        assert repo.iniciar_envio(
            tentativa_id, primeira_execucao
        ) is True

        # Simula o encerramento do processo:
        # não chama confirmar_lancamento nem marcar_incerto.

        segunda_execucao = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(segunda_execucao)

        nova_reserva = repo.reservar_ctrc(
            execucao_id=segunda_execucao,
            serie=serie,
            numero=numero,
            fluxo="CWB",
            data_inicial=data,
            data_final=data,
        )

        assert nova_reserva["reserva_existente"] is True
        assert nova_reserva["status"] == "envio_iniciado"
        assert nova_reserva["autorizada_para_envio"] is False

        assert repo.iniciar_envio(
            tentativa_id, segunda_execucao
        ) is False

        tentativa = repo.buscar_tentativa(serie, numero)

        assert tentativa["status"] == "envio_iniciado"
        assert tentativa["envio_iniciado_em"] is not None

    finally:
        remover_registros_teste(
            repo, execucoes, serie, numero
        )

@pytest.mark.parametrize(
    "cenario,estado_inicial,estado_esperado",
    [
        ("encontrada", "envio_iniciado", "confirmado"),
        ("ausente", "incerto", "incerto"),
        ("timeout", "envio_iniciado", "envio_iniciado"),
    ],
)
def test_reconciliacao_mysql_real(
    cenario,
    estado_inicial,
    estado_esperado,
):
    repo = criar_repository()
    serie = "TST"
    numero = novo_identificador()
    data = date(2026, 10, 12)
    execucoes = []
    consultas = []

    try:
        execucao_id = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(execucao_id)

        reserva = repo.reservar_ctrc(
            execucao_id=execucao_id,
            serie=serie,
            numero=numero,
            fluxo="CWB",
            data_inicial=data,
            data_final=data,
        )

        assert reserva["reserva_criada"] is True
        tentativa_id = reserva["id"]

        assert repo.iniciar_envio(
            tentativa_id,
            execucao_id,
        ) is True

        if estado_inicial == "incerto":
            repo.marcar_incerto(
                tentativa_id,
                "Timeout simulado",
            )

        def consultar_historico(**kwargs):
            consultas.append(kwargs)

            if cenario == "timeout":
                raise TimeoutError(
                    "Consulta OP101 indisponivel"
                )

            if cenario == "ausente":
                return [{"codigo": "63"}]

            referencia = reserva["referencia"]
            return [
                {"codigo": "63"},
                {"codigo": "73", "complemento": "OC73 ANTIGA SEM REFERENCIA"},
                {"codigo": "73", "complemento": f"BOT OC73 | REF:{referencia}"},
            ]

        reconciliador = ReconciliadorOC73(
            repository=repo,
            consultar_historico=consultar_historico,
        )

        tentativa_antes = repo.buscar_tentativa(
            serie, numero
        )

        resultado = reconciliador.reconciliar(
            tentativa_antes
        )

        tentativa_depois = repo.buscar_tentativa(
            serie, numero
        )

        assert len(consultas) == 1
        assert consultas[0]["serie"] == serie
        assert consultas[0]["numero"] == numero
        assert consultas[0]["data_inicial"] == data
        assert consultas[0]["data_final"] == data

        assert resultado.status == estado_esperado
        assert resultado.permite_reenvio is False
        assert tentativa_depois["status"] == estado_esperado

        if cenario == "encontrada":
            assert resultado.confirmou is True
            assert tentativa_depois["confirmado_em"] is not None
            assert tentativa_depois["resposta_ssw"] is not None
        else:
            assert resultado.confirmou is False
            assert tentativa_depois["confirmado_em"] is None

        # Nenhum estado pode reiniciar o envio.
        assert repo.iniciar_envio(
            tentativa_id,
            execucao_id,
        ) is False

        print(
            f"\n[OK] Reconciliacao {cenario}: "
            f"{estado_esperado}, sem reenvio."
        )

    finally:
        remover_registros_teste(
            repo,
            execucoes,
            serie,
            numero,
        )

def test_reconciliacao_concorrente_mysql_real():
    repo = criar_repository()
    serie = "TST"
    numero = novo_identificador()
    data = date(2026, 10, 12)
    execucoes = []
    barreira = Barrier(2, timeout=10)

    try:
        execucao_id = repo.iniciar_execucao(
            data_referencia=data,
            triggered_by="pytest_mysql",
            dry_run=True,
        )
        execucoes.append(execucao_id)

        reserva = repo.reservar_ctrc(
            execucao_id=execucao_id,
            serie=serie,
            numero=numero,
            fluxo="CWB",
            data_inicial=data,
            data_final=data,
        )

        tentativa_id = reserva["id"]

        assert repo.iniciar_envio(
            tentativa_id,
            execucao_id,
        ) is True

        # Ambas as threads recebem o mesmo estado inicial.
        tentativa_inicial = repo.buscar_tentativa(
            serie, numero
        )

        def consultar_historico(**kwargs):
            # Força as duas consultas a chegarem
            # ao mesmo ponto antes da confirmação.
            barreira.wait()

            referencia = reserva["referencia"]
            return [
                {"codigo": "73", "complemento": "OC73 ANTIGA"},
                {"codigo": "73", "complemento": f"BOT OC73 | REF:{referencia}"},
            ]

        def reconciliar():
            repo_thread = criar_repository()

            reconciliador = ReconciliadorOC73(
                repository=repo_thread,
                consultar_historico=consultar_historico,
            )

            return reconciliador.reconciliar(
                dict(tentativa_inicial)
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [
                pool.submit(reconciliar)
                for _ in range(2)
            ]

            resultados = [
                futuro.result(timeout=20)
                for futuro in futuros
            ]

        assert all(
            resultado.status == "confirmado"
            for resultado in resultados
        )

        assert all(
            resultado.confirmou is True
            for resultado in resultados
        )

        assert all(
            resultado.permite_reenvio is False
            for resultado in resultados
        )

        tentativa_final = repo.buscar_tentativa(
            serie, numero
        )

        assert tentativa_final["status"] == "confirmado"
        assert tentativa_final["confirmado_em"] is not None

        print(
            "\n[OK] Duas reconciliacoes concorrentes: "
            "estado confirmado, sem reenvio."
        )

    finally:
        remover_registros_teste(
            repo,
            execucoes,
            serie,
            numero,
        )


def test_scheduler_dois_processos_mesmo_fluxo_data():
    data_referencia = _data_scheduler_unica()
    repo = criar_repository()
    resultados = []

    try:
        resultados = _executar_reservas_em_processos(
            data_referencia,
            ("CWB", "CWB"),
        )

        assert sum(item["reservada"] for item in resultados) == 1
        assert {
            item["motivo"] for item in resultados
        } <= {"reservada", "trava_ocupada", "execucao_ja_registrada"}

        with repo.transacao() as conexao:
            validar_destino_laboratorio(conexao)
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM oc73_execucoes
                    WHERE data_referencia = %s
                      AND triggered_by = 'scheduler:CWB'
                    """,
                    (data_referencia,),
                )
                assert cursor.fetchone()["total"] == 1
    finally:
        remover_reservas_scheduler(
            repo,
            data_referencia,
            [item.get("execucao_id") for item in resultados],
        )


def test_scheduler_dois_processos_fluxos_independentes():
    data_referencia = _data_scheduler_unica()
    repo = criar_repository()
    resultados = []

    try:
        resultados = _executar_reservas_em_processos(
            data_referencia,
            ("CWB", "BIG"),
        )

        assert all(item["reservada"] for item in resultados)
        assert {
            item["triggered_by"] for item in resultados
        } == {"scheduler:CWB", "scheduler:BIG"}
        assert len({item["execucao_id"] for item in resultados}) == 2
    finally:
        remover_reservas_scheduler(
            repo,
            data_referencia,
            [item.get("execucao_id") for item in resultados],
        )


def test_scheduler_queda_apos_reserva_e_reinicio_nao_repetem():
    data_referencia = _data_scheduler_unica()
    contexto = multiprocessing.get_context("spawn")
    receptor, emissor = contexto.Pipe(duplex=False)
    processo = contexto.Process(
        target=_reservar_scheduler_e_aguardar_queda,
        args=(data_referencia.isoformat(), "CWB", emissor),
    )
    repo = criar_repository()
    primeira = None
    repetida = None

    try:
        processo.start()
        assert receptor.poll(30), "processo não confirmou a reserva"
        estado, primeira = receptor.recv()
        assert estado == "ok"
        assert primeira["reservada"] is True

        processo.terminate()
        processo.join(timeout=15)
        assert not processo.is_alive()

        repetida = criar_repository().reservar_execucao_agendada(
            data_referencia=data_referencia,
            fluxo="CWB",
            dry_run=True,
        )
        assert repetida["reservada"] is False
        assert repetida["motivo"] == "execucao_ja_registrada"
        assert repetida["status"] == "executando"
        assert repetida["execucao_id"] == primeira["execucao_id"]
    finally:
        if processo.is_alive():
            processo.terminate()
            processo.join(timeout=15)
        remover_reservas_scheduler(
            repo,
            data_referencia,
            [
                primeira.get("execucao_id") if primeira else None,
                repetida.get("execucao_id") if repetida else None,
            ],
        )


def test_scheduler_excecao_transacional_libera_trava_real():
    data_referencia = _data_scheduler_unica()
    repo_base = criar_repository()
    conexao_real = repo_base.connection_factory()
    repo_falho = Ocorrencia73Repository(
        connection_factory=lambda: _ConexaoComFalhaSQL(
            conexao_real,
            "FROM oc73_execucoes",
        )
    )
    reserva_posterior = None

    try:
        with pytest.raises(RuntimeError, match="falha injetada"):
            repo_falho.reservar_execucao_agendada(
                data_referencia=data_referencia,
                fluxo="CWB",
                dry_run=True,
            )

        reserva_posterior = criar_repository().reservar_execucao_agendada(
            data_referencia=data_referencia,
            fluxo="CWB",
            dry_run=True,
        )
        assert reserva_posterior["reservada"] is True
    finally:
        remover_reservas_scheduler(
            repo_base,
            data_referencia,
            [
                reserva_posterior.get("execucao_id")
                if reserva_posterior else None
            ],
        )


def test_scheduler_perda_conexao_falha_sem_reserva_fantasma():
    data_referencia = _data_scheduler_unica()
    repo_base = criar_repository()
    conexao_real = repo_base.connection_factory()
    repo_falho = Ocorrencia73Repository(
        connection_factory=lambda: _ConexaoPerdidaNoCommit(conexao_real)
    )
    reserva_posterior = None

    try:
        with pytest.raises(Exception):
            repo_falho.reservar_execucao_agendada(
                data_referencia=data_referencia,
                fluxo="BIG",
                dry_run=True,
            )

        reserva_posterior = criar_repository().reservar_execucao_agendada(
            data_referencia=data_referencia,
            fluxo="BIG",
            dry_run=True,
        )
        assert reserva_posterior["reservada"] is True
    finally:
        remover_reservas_scheduler(
            repo_base,
            data_referencia,
            [
                reserva_posterior.get("execucao_id")
                if reserva_posterior else None
            ],
        )


def test_scheduler_falha_release_lock_fecha_sessao_real():
    data_referencia = _data_scheduler_unica()
    repo_base = criar_repository()
    conexao_real = repo_base.connection_factory()
    repo_falho = Ocorrencia73Repository(
        connection_factory=lambda: _ConexaoComFalhaSQL(
            conexao_real,
            "RELEASE_LOCK",
        )
    )
    primeira = None
    repetida = None

    try:
        primeira = repo_falho.reservar_execucao_agendada(
            data_referencia=data_referencia,
            fluxo="BIG",
            dry_run=True,
        )
        assert primeira["reservada"] is True

        repetida = criar_repository().reservar_execucao_agendada(
            data_referencia=data_referencia,
            fluxo="BIG",
            dry_run=True,
        )
        assert repetida["reservada"] is False
        assert repetida["motivo"] == "execucao_ja_registrada"
        assert repetida["execucao_id"] == primeira["execucao_id"]
    finally:
        remover_reservas_scheduler(
            repo_base,
            data_referencia,
            [
                primeira.get("execucao_id") if primeira else None,
                repetida.get("execucao_id") if repetida else None,
            ],
        )
