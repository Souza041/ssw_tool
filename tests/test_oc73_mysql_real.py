
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
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest

from modules.ocorrencia_73.repository import (
    Ocorrencia73Repository,
)


pytestmark = pytest.mark.skipif(
    os.getenv("OC73_MYSQL_TEST") != "SIM",
    reason="Teste MySQL real exige OC73_MYSQL_TEST=SIM",
)


def novo_identificador():
    return uuid.uuid4().hex[:20].upper()


def criar_repository():
    """
    Cada operação utiliza uma conexão independente.
    Timeout de lock limitado à sessão atual.
    """
    def conectar():
        repo_base = Ocorrencia73Repository()
        conexao = repo_base._conectar()

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
