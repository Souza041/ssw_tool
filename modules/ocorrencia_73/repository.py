
import os
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pymysql
from pymysql.cursors import DictCursor

from pymysql.err import IntegrityError


TIMEZONE = ZoneInfo("America/Sao_Paulo")


class Ocorrencia73Repository:
    def __init__(self, connection_factory=None):
        self.connection_factory = (
            connection_factory or self._conectar
        )

    def _conectar(self):
        campos = {
            "host": os.getenv("AUTOMATION_DB_HOST"),
            "database": os.getenv("AUTOMATION_DB_NAME"),
            "user": os.getenv("AUTOMATION_DB_USER"),
            "password": os.getenv("AUTOMATION_DB_PASSWORD"),
        }

        faltando = [
            nome for nome, valor in campos.items()
            if not valor
        ]

        if faltando:
            raise RuntimeError(
                "Configuração MySQL incompleta: "
                + ", ".join(faltando)
            )

        return pymysql.connect(
            host=campos["host"],
            port=int(os.getenv("AUTOMATION_DB_PORT", "3306")),
            user=campos["user"],
            password=campos["password"],
            database=campos["database"],
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=10,
            read_timeout=15,
            write_timeout=15,
        )

    @contextmanager
    def transacao(self):
        conexao = self.connection_factory()
        try:
            yield conexao
            conexao.commit()
        except Exception:
            conexao.rollback()
            raise
        finally:
            conexao.close()

    def agora(self):
        return datetime.now(TIMEZONE).replace(tzinfo=None)

    def iniciar_execucao(
        self,
        data_referencia: date,
        triggered_by: str = "manual",
        dry_run: bool = True,
    ) -> int:
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO oc73_execucoes (
                        data_referencia,
                        triggered_by,
                        status,
                        dry_run,
                        iniciado_em
                    )
                    VALUES (%s, %s, 'executando', %s, %s)
                    """,
                    (
                        data_referencia,
                        triggered_by,
                        int(dry_run),
                        self.agora(),
                    ),
                )
                return cursor.lastrowid

    def finalizar_execucao(
        self,
        execucao_id: int,
        status: str,
        total_relatorio: int = 0,
        total_filtrado: int = 0,
        total_lancado: int = 0,
        total_erros: int = 0,
        mensagem_erro: str | None = None,
    ):
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE oc73_execucoes
                    SET status = %s,
                        total_relatorio = %s,
                        total_filtrado = %s,
                        total_lancado = %s,
                        total_erros = %s,
                        mensagem_erro = %s,
                        finalizado_em = %s
                    WHERE id = %s
                    """,
                    (
                        status,
                        total_relatorio,
                        total_filtrado,
                        total_lancado,
                        total_erros,
                        mensagem_erro,
                        self.agora(),
                        execucao_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"Execução {execucao_id} não encontrada."
                    )

    def buscar_tentativa(
        self,
        serie: str,
        numero: str,
    ) -> dict | None:
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM oc73_tentativas
                    WHERE serie = %s
                      AND numero = %s
                      AND codigo_ocorrencia = 73
                    LIMIT 1
                    """,
                    (serie.upper().strip(), str(numero).strip()),
                )
                return cursor.fetchone()

    def reservar_ctrc(
        self,
        execucao_id: int,
        serie: str,
        numero: str,
        fluxo: str,
        data_inicial: date,
        data_final: date,
    ) -> dict:
        serie = serie.upper().strip()
        numero = str(numero).strip()
        fluxo = fluxo.upper().strip()

        if not serie or not numero:
            raise ValueError("Série e número são obrigatórios.")

        if fluxo not in {"CWB", "BIG"}:
            raise ValueError("Fluxo inválido.")

        if data_inicial > data_final:
            raise ValueError("Janela de pesquisa inválida.")

        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                # A chave UNIQUE (serie, numero, 73)
                # impede inserções simultâneas duplicadas.
                #
                # INSERT IGNORE retorna rowcount=1
                # somente quando uma nova linha Ã© criada.
                # NÃ£o altera a reserva existente.
                try:
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
                            status,
                            reservado_em
                        )
                        VALUES (
                            %s, %s, %s, 73, %s,
                            %s, %s, 'reservado', %s
                        )
                        """,
                        (
                            execucao_id,
                            serie,
                            numero,
                            fluxo,
                            data_inicial,
                            data_final,
                            self.agora(),
                        ),
                    )
                    reserva_criada = True

                except IntegrityError as exc:
                    if exc.args[0] != 1062:
                        raise

                    # A duplicidade é esperada quando o CTRC
                    # já possui uma reserva persistente.
                    reserva_criada = False

                cursor.execute(
                    """
                    SELECT *
                    FROM oc73_tentativas
                    WHERE serie = %s
                    AND numero = %s
                    AND codigo_ocorrencia = 73
                    FOR UPDATE
                    """,
                    (serie, numero),
                )

                tentativa = cursor.fetchone()

                if tentativa is None:
                    raise RuntimeError(
                        "Não foi possível recuperar a reserva."
                    )

                return {
                    "id": tentativa["id"],
                    "status": tentativa["status"],
                    "execucao_id": tentativa["execucao_id"],
                    "reserva_criada": reserva_criada,
                    "reserva_existente": not reserva_criada,
                    "autorizada_para_envio": False,
                }


    def iniciar_envio(
        self,
        tentativa_id: int,
        execucao_id: int,
    ) -> bool:
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE oc73_tentativas
                    SET status = 'envio_iniciado',
                        envio_iniciado_em = %s
                    WHERE id = %s
                      AND execucao_id = %s
                      AND status = 'reservado'
                      AND envio_iniciado_em IS NULL
                    """,
                    (
                        self.agora(),
                        tentativa_id,
                        execucao_id,
                    ),
                )
                return cursor.rowcount == 1

    def confirmar_lancamento(
        self,
        tentativa_id: int,
        resposta_ssw: str | None = None,
    ):
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE oc73_tentativas
                    SET status = 'confirmado',
                        resposta_ssw = %s,
                        confirmado_em = %s
                    WHERE id = %s
                      AND status IN (
                          'envio_iniciado', 'incerto'
                      )
                    """,
                    (
                        resposta_ssw,
                        self.agora(),
                        tentativa_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "Tentativa não está em estado "
                        "confirmável."
                    )

    def marcar_incerto(
        self,
        tentativa_id: int,
        mensagem_erro: str,
    ):
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE oc73_tentativas
                    SET status = 'incerto',
                        mensagem_erro = %s
                    WHERE id = %s
                      AND status = 'envio_iniciado'
                    """,
                    (
                        mensagem_erro,
                        tentativa_id,
                    ),
                )

                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "Tentativa não está em envio_iniciado."
                    )
