import os
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger

from modules.ocorrencia_73.calendario import (
    AGENDAMENTOS_OC73,
    TIMEZONE,
)
from modules.ocorrencia_73.repository import Ocorrencia73Repository


STATUS_PERMITIDOS = frozenset({
    "executando",
    "concluido",
    "concluido_com_pendencias",
    "erro",
})
FLUXOS_PERMITIDOS = frozenset({"CWB", "BIG"})
PAGINA_TAMANHO_PADRAO = 25
PAGINA_TAMANHO_MAXIMO = 100
PAGINA_MAXIMA = 1000
DESLOCAMENTO_MAXIMO = 100_000
LIMITE_ALERTAS = 50

_SQL_PROIBIDO = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE|ALTER|CREATE|DROP|TRUNCATE|"
    r"GRANT|REVOKE|CALL|LOAD|LOCK|UNLOCK)\b",
    re.IGNORECASE,
)


def _env_bool(nome: str, padrao: bool) -> bool:
    valor = os.getenv(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes", "on"}


def usuarios_observabilidade_permitidos() -> frozenset[str]:
    valor = os.getenv("OC73_OBSERVABILIDADE_USUARIOS", "")
    return frozenset(
        item.strip().upper()
        for item in valor.split(",")
        if item.strip()
    )


def usuario_pode_observar(usuario: str | None) -> bool:
    usuario = str(usuario or "").strip().upper()
    permitidos = usuarios_observabilidade_permitidos()
    return bool(usuario and permitidos and usuario in permitidos)


def estado_seguranca_oc73() -> dict:
    from modules.ocorrencia_73 import config, scheduler, service

    scheduler_configurado = _env_bool("OCORRENCIA_73_ENABLED", False)
    scheduler_ativo = bool(
        scheduler_configurado and scheduler._scheduler.running
    )
    return {
        "scheduler_configurado": scheduler_configurado,
        "scheduler_habilitado": scheduler_ativo,
        "dry_run": bool(config.DRY_RUN),
        "gate_envio_habilitado": bool(service.OC73_ENVIO_HABILITADO),
    }


def proximas_execucoes_teoricas(
    agora: datetime | None = None,
) -> list[dict]:
    agora = agora or datetime.now(TIMEZONE)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=TIMEZONE)
    else:
        agora = agora.astimezone(TIMEZONE)

    scheduler_habilitado = estado_seguranca_oc73()[
        "scheduler_habilitado"
    ]
    resultado = []
    for agendamento in AGENDAMENTOS_OC73:
        trigger = CronTrigger(
            day_of_week=agendamento.dias_apscheduler,
            hour=agendamento.hora,
            minute=agendamento.minuto,
            timezone=TIMEZONE,
        )
        proxima = trigger.get_next_fire_time(None, agora)
        resultado.append({
            "fluxo": agendamento.fluxo,
            "proxima_execucao": proxima,
            "job_ativo": scheduler_habilitado,
            "rotulo": (
                "Job habilitado"
                if scheduler_habilitado
                else "Previsão teórica — scheduler desabilitado"
            ),
        })
    return resultado


def _validar_select(sql: str) -> None:
    normalizado = " ".join(str(sql).split())
    if not normalizado.upper().startswith("SELECT "):
        raise ValueError("Observabilidade aceita somente SELECT.")
    if ";" in normalizado or _SQL_PROIBIDO.search(normalizado):
        raise ValueError("Consulta não permitida na observabilidade.")


def _duracao_segundos(registro: dict, agora: datetime | None = None):
    inicio = registro.get("iniciado_em")
    if not isinstance(inicio, datetime):
        return None
    fim = registro.get("finalizado_em")
    if not isinstance(fim, datetime):
        fim = agora or datetime.now(TIMEZONE).replace(tzinfo=None)
    if inicio.tzinfo is not None and fim.tzinfo is None:
        fim = fim.replace(tzinfo=inicio.tzinfo)
    if fim.tzinfo is not None and inicio.tzinfo is None:
        fim = fim.replace(tzinfo=None)
    return max(0, int((fim - inicio).total_seconds()))


class Ocorrencia73ObservabilidadeRepository:
    """Consultas limitadas e somente leitura para o painel OC73."""

    def __init__(self, connection_factory=None):
        self.connection_factory = (
            connection_factory or Ocorrencia73Repository().connection_factory
        )

    @contextmanager
    def _conexao(self):
        conexao = self.connection_factory()
        try:
            yield conexao
        finally:
            conexao.close()

    @staticmethod
    def _executar(cursor, sql: str, parametros=()):
        _validar_select(sql)
        cursor.execute(sql, parametros)

    @staticmethod
    def _normalizar_paginacao(pagina: int, por_pagina: int):
        if (
            isinstance(pagina, bool)
            or not isinstance(pagina, int)
            or isinstance(por_pagina, bool)
            or not isinstance(por_pagina, int)
        ):
            raise ValueError("Paginação inválida.")
        if pagina < 1 or pagina > PAGINA_MAXIMA:
            raise ValueError(
                f"Página deve estar entre 1 e {PAGINA_MAXIMA}."
            )
        por_pagina = max(
            1,
            min(por_pagina, PAGINA_TAMANHO_MAXIMO),
        )
        if (pagina - 1) * por_pagina > DESLOCAMENTO_MAXIMO:
            raise ValueError("Deslocamento de paginação acima do limite.")
        return pagina, por_pagina

    @staticmethod
    def _validar_execucao_id(execucao_id: int) -> int:
        if (
            isinstance(execucao_id, bool)
            or not isinstance(execucao_id, int)
            or execucao_id <= 0
        ):
            raise ValueError("ID de execução inválido.")
        return execucao_id

    @staticmethod
    def _filtros_execucoes(
        fluxo: str | None,
        status: str | None,
        data_inicio: date | None,
        data_fim: date | None,
    ):
        clausulas = []
        parametros = []

        if fluxo:
            fluxo = fluxo.strip().upper()
            if fluxo not in FLUXOS_PERMITIDOS:
                raise ValueError("Fluxo inválido.")
            clausulas.append("triggered_by = %s")
            parametros.append(f"scheduler:{fluxo}")

        if status:
            status = status.strip().lower()
            if status not in STATUS_PERMITIDOS:
                raise ValueError("Status inválido.")
            clausulas.append("status = %s")
            parametros.append(status)

        if data_inicio:
            clausulas.append("data_referencia >= %s")
            parametros.append(data_inicio)
        if data_fim:
            clausulas.append("data_referencia <= %s")
            parametros.append(data_fim)
        if data_inicio and data_fim and data_inicio > data_fim:
            raise ValueError("Período inválido.")

        where = ""
        if clausulas:
            where = " WHERE " + " AND ".join(clausulas)
        return where, parametros

    def listar_execucoes(
        self,
        *,
        pagina: int = 1,
        por_pagina: int = PAGINA_TAMANHO_PADRAO,
        fluxo: str | None = None,
        status: str | None = None,
        data_inicio: date | None = None,
        data_fim: date | None = None,
    ) -> dict:
        pagina, por_pagina = self._normalizar_paginacao(
            pagina,
            por_pagina,
        )
        where, parametros = self._filtros_execucoes(
            fluxo,
            status,
            data_inicio,
            data_fim,
        )
        deslocamento = (pagina - 1) * por_pagina

        sql_total = "SELECT COUNT(*) AS total FROM oc73_execucoes" + where
        sql_dados = (
            "SELECT id, data_referencia, triggered_by, status, dry_run, "
            "iniciado_em, finalizado_em, total_relatorio, total_filtrado, "
            "total_lancado, total_erros "
            "FROM oc73_execucoes"
            + where
            + " ORDER BY id DESC LIMIT %s OFFSET %s"
        )

        with self._conexao() as conexao:
            with conexao.cursor() as cursor:
                self._executar(cursor, sql_total, tuple(parametros))
                total = int((cursor.fetchone() or {}).get("total", 0))
                self._executar(
                    cursor,
                    sql_dados,
                    tuple(parametros) + (por_pagina, deslocamento),
                )
                itens = list(cursor.fetchall() or [])

        for item in itens:
            item["duracao_segundos"] = _duracao_segundos(item)
        return {
            "itens": itens,
            "total": total,
            "pagina": pagina,
            "por_pagina": por_pagina,
            "total_paginas": max(1, (total + por_pagina - 1) // por_pagina),
        }

    def ultimas_execucoes_por_fluxo(self) -> dict[str, dict | None]:
        resultado = {}
        sql = (
            "SELECT id, data_referencia, triggered_by, status, dry_run, "
            "iniciado_em, finalizado_em, total_relatorio, total_filtrado, "
            "total_lancado, total_erros "
            "FROM oc73_execucoes "
            "WHERE triggered_by = %s ORDER BY id DESC LIMIT 1"
        )
        with self._conexao() as conexao:
            with conexao.cursor() as cursor:
                for fluxo in ("CWB", "BIG"):
                    self._executar(cursor, sql, (f"scheduler:{fluxo}",))
                    item = cursor.fetchone()
                    if item:
                        item["duracao_segundos"] = _duracao_segundos(item)
                    resultado[fluxo] = item
        return resultado

    def execucoes_anormais(
        self,
        minutos: int = 120,
    ) -> list[dict]:
        minutos = max(1, min(int(minutos), 10080))
        limite = datetime.now(TIMEZONE).replace(tzinfo=None) - timedelta(
            minutes=minutos
        )
        sql = (
            "SELECT id, data_referencia, triggered_by, status, iniciado_em "
            "FROM oc73_execucoes "
            "WHERE status = %s AND iniciado_em < %s "
            "ORDER BY iniciado_em ASC LIMIT %s"
        )
        with self._conexao() as conexao:
            with conexao.cursor() as cursor:
                self._executar(
                    cursor,
                    sql,
                    ("executando", limite, LIMITE_ALERTAS),
                )
                itens = list(cursor.fetchall() or [])
        for item in itens:
            item["duracao_segundos"] = _duracao_segundos(item)
        return itens

    def obter_execucao(self, execucao_id: int) -> dict | None:
        execucao_id = self._validar_execucao_id(execucao_id)
        sql = (
            "SELECT id, data_referencia, triggered_by, status, dry_run, "
            "iniciado_em, finalizado_em, total_relatorio, total_filtrado, "
            "total_lancado, total_erros "
            "FROM oc73_execucoes WHERE id = %s LIMIT 1"
        )
        with self._conexao() as conexao:
            with conexao.cursor() as cursor:
                self._executar(cursor, sql, (execucao_id,))
                item = cursor.fetchone()
        if item:
            item["duracao_segundos"] = _duracao_segundos(item)
        return item

    def listar_tentativas(
        self,
        execucao_id: int,
        *,
        pagina: int = 1,
        por_pagina: int = PAGINA_TAMANHO_PADRAO,
    ) -> dict:
        execucao_id = self._validar_execucao_id(execucao_id)
        pagina, por_pagina = self._normalizar_paginacao(
            pagina,
            por_pagina,
        )
        deslocamento = (pagina - 1) * por_pagina
        sql_total = (
            "SELECT COUNT(*) AS total FROM oc73_tentativas "
            "WHERE execucao_id = %s"
        )
        sql_dados = (
            "SELECT id, execucao_id, serie, numero, fluxo, status, referencia, "
            "reservado_em, envio_iniciado_em, confirmado_em "
            "FROM oc73_tentativas WHERE execucao_id = %s "
            "ORDER BY id DESC LIMIT %s OFFSET %s"
        )
        with self._conexao() as conexao:
            with conexao.cursor() as cursor:
                self._executar(cursor, sql_total, (execucao_id,))
                total = int((cursor.fetchone() or {}).get("total", 0))
                self._executar(
                    cursor,
                    sql_dados,
                    (execucao_id, por_pagina, deslocamento),
                )
                itens = list(cursor.fetchall() or [])
        return {
            "itens": itens,
            "total": total,
            "pagina": pagina,
            "por_pagina": por_pagina,
            "total_paginas": max(1, (total + por_pagina - 1) // por_pagina),
        }
