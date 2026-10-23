
import os
import secrets
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

    def reservar_execucao_agendada(
        self,
        *,
        data_referencia: date,
        fluxo: str,
        dry_run: bool,
    ) -> dict:
        """Reserva uma execução por fluxo/data entre processos.

        GET_LOCK serializa a verificação e a inserção no servidor MySQL.
        O registro em oc73_execucoes mantém a decisão após a liberação
        da trava e também bloqueia uma repetição depois de reinício.
        """
        if not isinstance(data_referencia, date):
            raise ValueError("Data de referência inválida.")

        fluxo = str(fluxo or "").strip().upper()
        if fluxo not in {"CWB", "BIG"}:
            raise ValueError("Fluxo agendado inválido.")

        triggered_by = f"scheduler:{fluxo}"
        nome_trava = (
            f"ssw_tool:oc73:{fluxo}:"
            f"{data_referencia.isoformat()}"
        )
        conexao = self.connection_factory()
        trava_adquirida = False

        try:
            with conexao.cursor() as cursor:
                cursor.execute(
                    "SELECT GET_LOCK(%s, 0) AS adquirida",
                    (nome_trava,),
                )
                linha = cursor.fetchone() or {}
                trava_adquirida = linha.get("adquirida") == 1

                if not trava_adquirida:
                    return {
                        "reservada": False,
                        "motivo": "trava_ocupada",
                        "execucao_id": None,
                        "triggered_by": triggered_by,
                    }

                cursor.execute(
                    """
                    SELECT id, status
                    FROM oc73_execucoes
                    WHERE data_referencia = %s
                      AND triggered_by = %s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (data_referencia, triggered_by),
                )
                existente = cursor.fetchone()

                if existente is not None:
                    conexao.rollback()
                    return {
                        "reservada": False,
                        "motivo": "execucao_ja_registrada",
                        "execucao_id": existente["id"],
                        "status": existente["status"],
                        "triggered_by": triggered_by,
                    }

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
                execucao_id = cursor.lastrowid
                conexao.commit()

                return {
                    "reservada": True,
                    "motivo": "reservada",
                    "execucao_id": execucao_id,
                    "triggered_by": triggered_by,
                }
        except Exception:
            conexao.rollback()
            raise
        finally:
            if trava_adquirida:
                try:
                    with conexao.cursor() as cursor:
                        cursor.execute(
                            "SELECT RELEASE_LOCK(%s)",
                            (nome_trava,),
                        )
                except Exception:
                    # O fechamento da conexão também libera GET_LOCK.
                    pass
            conexao.close()

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

        referencia_nova = "OC73-" + secrets.token_hex(10).upper()

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
                            reservado_em,
                            referencia
                        )
                        VALUES (
                            %s, %s, %s, 73, %s,
                            %s, %s, 'reservado', %s, %s
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
                            referencia_nova,
                        ),
                    )
                    reserva_criada = True

                except IntegrityError as exc:
                    codigo = exc.args[0] if exc.args else None
                    mensagem = str(exc)

                    if codigo != 1062:
                        raise

                    if "uq_oc73_referencia" in mensagem:
                        raise RuntimeError(
                            "Colisão de referência global OC73; "
                            "reserva abortada para proteção contra duplicidade."
                        ) from exc

                    if "uq_oc73_ctrc" not in mensagem:
                        raise RuntimeError(
                            "Violação UNIQUE não reconhecida no OC73; "
                            "reserva abortada."
                        ) from exc

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
                    "referencia": tentativa["referencia"],
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
                      AND referencia IS NOT NULL
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

    @contextmanager
    def trava_rechecagem_big(self, execucao_id):
        """Serializa execução original e rechecagens do mesmo pai no MySQL.

        A conexão dedicada fica aberta durante a seção crítica; GET_LOCK é
        liberado automaticamente se o processo cair. O callable permite
        conferir a posse imediatamente antes de um envio.
        """
        nome = f"ssw_tool:oc73:big:exec:{int(execucao_id)}"
        conexao = self.connection_factory()
        adquirida = False
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, 0) AS ok", (nome,))
                adquirida = (cursor.fetchone() or {}).get("ok") == 1

            def possui_trava():
                if not adquirida:
                    return False
                with conexao.cursor() as cursor:
                    cursor.execute(
                        "SELECT IS_USED_LOCK(%s) = CONNECTION_ID() AS ok", (nome,),
                    )
                    return (cursor.fetchone() or {}).get("ok") == 1

            yield possui_trava
        finally:
            if adquirida:
                try:
                    with conexao.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", (nome,))
                except Exception:
                    pass
            conexao.close()

    def registrar_rechecagem_big(
        self, *, execucao_id, data_referencia, item, limite_envios,
        proxima_consulta_em, limite_em,
    ):
        import json
        if int(limite_envios) <= 0:
            raise ValueError("Limite de envios deve ser positivo.")
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                # Repetir a captura não reabre estados terminais nem amplia prazo.
                cursor.execute(
                    """INSERT INTO oc73_rechecagens_big (
                        execucao_id, data_referencia, serie, numero, item_json,
                        limite_envios, proxima_consulta_em, limite_em,
                        criado_em, atualizado_em
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)""",
                    (execucao_id, data_referencia, item["serie"], item["numero"],
                     json.dumps(item, ensure_ascii=False), limite_envios,
                     proxima_consulta_em, limite_em, self.agora(), self.agora()),
                )
                return cursor.lastrowid

    def listar_rechecagens_big(self, agora):
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """SELECT * FROM oc73_rechecagens_big
                    WHERE status = 'aguardando' AND proxima_consulta_em <= %s
                    ORDER BY proxima_consulta_em, id LIMIT 100""", (agora,),
                )
                return cursor.fetchall()

    def buscar_rechecagem_big(self, identificador):
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM oc73_rechecagens_big WHERE id = %s", (identificador,),
                )
                return cursor.fetchone()

    def atualizar_rechecagem_big(
        self, identificador, *, status, codigo=None, detalhe="", proxima=None,
        tentativa_id=None, consultou=False,
    ):
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """UPDATE oc73_rechecagens_big SET status=%s, ultimo_codigo=%s,
                    detalhe=%s, proxima_consulta_em=COALESCE(%s,proxima_consulta_em),
                    tentativa_id=COALESCE(%s,tentativa_id),
                    consultas=consultas+%s,
                    ultima_consulta_em=IF(%s, %s, ultima_consulta_em), atualizado_em=%s
                    WHERE id=%s AND status='aguardando'""",
                    (status, codigo, detalhe[:2000], proxima, tentativa_id,
                     int(consultou), int(consultou), self.agora(), self.agora(), identificador),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Pendência não está aguardando; atualização abortada.")

    def contar_tentativas_rechecagem_big(self, execucao_id):
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """SELECT COUNT(*) AS total FROM oc73_tentativas
                    WHERE execucao_id=%s OR execucao_id IN (
                        SELECT execucao_envio_id FROM oc73_rechecagens_big
                        WHERE execucao_id=%s AND execucao_envio_id IS NOT NULL
                    )""", (execucao_id, execucao_id),
                )
                return int(cursor.fetchone()["total"])

    def iniciar_envio_rechecagem_big(self, pendencia):
        # Execução filha e vínculo são persistidos na MESMA transação antes do envio.
        with self.transacao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """SELECT execucao_envio_id FROM oc73_rechecagens_big
                    WHERE id=%s AND status='aguardando' FOR UPDATE""", (pendencia["id"],),
                )
                registro = cursor.fetchone()
                if registro is None:
                    raise RuntimeError("Pendência não está aguardando.")
                if registro["execucao_envio_id"] is not None:
                    return registro["execucao_envio_id"]
                cursor.execute(
                    """INSERT INTO oc73_execucoes (
                        data_referencia, triggered_by, status, dry_run, iniciado_em
                    ) VALUES (%s,'rechecagem:BIG','executando',0,%s)""",
                    (pendencia["data_referencia"], self.agora()),
                )
                identificador = cursor.lastrowid
                cursor.execute(
                    "UPDATE oc73_rechecagens_big SET execucao_envio_id=%s WHERE id=%s",
                    (identificador, pendencia["id"]),
                )
                return identificador
