"""Protótipo offline da autorização transacional da homologação OC73.

Não é integração de runtime: não importa serviços OC73, web, rede ou driver SQL.
O armazenamento falso modela uma transação em um único snapshot protegido por
lock. Ele serve para testar invariantes, não para provar durabilidade MySQL.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, MutableMapping


REFERENCIA_RE = re.compile(r"OC73-[0-9A-F]{20}\Z")
REVISAO_RE = re.compile(r"[0-9a-f]{7,64}\Z")
VALIDADE_MAXIMA = timedelta(minutes=15)
PAPEL_TECNICO = "oc73_aprovador_tecnico"
PAPEL_OPERACIONAL = "oc73_aprovador_operacional"


class AutorizacaoTransacionalErro(RuntimeError):
    """Erro sanitizado do protótipo."""


class AutorizacaoNegada(AutorizacaoTransacionalErro):
    pass


class ConflitoPersistente(AutorizacaoTransacionalErro):
    pass


class FalhaTransacional(AutorizacaoTransacionalErro):
    pass


class CommitAmbiguo(FalhaTransacional):
    pass


class ProcessoInterrompido(FalhaTransacional):
    pass


def _utc(valor: datetime) -> datetime:
    if not isinstance(valor, datetime) or valor.tzinfo is None:
        raise AutorizacaoNegada("Timestamp UTC com timezone é obrigatório.")
    return valor.astimezone(timezone.utc)


def _texto(valor: Any, nome: str, limite: int = 80) -> str:
    resultado = str(valor or "").strip()
    if not resultado or len(resultado) > limite:
        raise AutorizacaoNegada(f"Identificador {nome} inválido.")
    return resultado


@dataclass(frozen=True)
class IdentidadeAutenticada:
    issuer: str
    subject_id: str
    person_issuer: str
    person_id: str
    person_verified: bool
    username: str
    authenticated: bool
    roles: frozenset[str]
    provider: str = "provedor-corporativo-pendente"

    def validar(self, papel: str) -> None:
        _texto(self.issuer, "issuer")
        _texto(self.subject_id, "subject_id")
        _texto(self.person_issuer, "person_issuer")
        _texto(self.person_id, "person_id")
        _texto(self.username, "username")
        _texto(self.provider, "provider")
        if not self.authenticated:
            raise AutorizacaoNegada("Identidade não autenticada.")
        if self.person_verified is not True:
            raise AutorizacaoNegada("Vínculo pessoa-conta não verificado.")
        if papel not in self.roles:
            raise AutorizacaoNegada("Papel de aprovação insuficiente.")


@dataclass(frozen=True)
class TentativaHomologacao:
    tentativa_id: int
    serie: str
    numero: str
    seq_ctrc: str
    familia: str
    local: str
    referencia: str
    revisao_codigo: str
    status: str = "reservado"

    def validar(self) -> None:
        if isinstance(self.tentativa_id, bool) or self.tentativa_id <= 0:
            raise AutorizacaoNegada("ID de tentativa inválido.")
        for nome in ("serie", "numero", "seq_ctrc", "familia", "local"):
            _texto(getattr(self, nome), nome)
        if REFERENCIA_RE.fullmatch(self.referencia) is None:
            raise AutorizacaoNegada("Referência OC73 inválida.")
        if REVISAO_RE.fullmatch(self.revisao_codigo) is None:
            raise AutorizacaoNegada("Revisão de código inválida.")


@dataclass(frozen=True)
class AutorizacaoHomologacao:
    autorizacao_id: str
    tentativa_id: int
    serie: str
    numero: str
    seq_ctrc: str
    familia: str
    local: str
    referencia: str
    revisao_codigo: str
    emitida_em: datetime
    expira_em: datetime
    aprovador_tecnico: IdentidadeAutenticada
    aprovador_operacional: IdentidadeAutenticada
    status: str = "pendente"

    def validar_estrutura(self) -> None:
        _texto(self.autorizacao_id, "autorizacao_id")
        inicio = _utc(self.emitida_em)
        fim = _utc(self.expira_em)
        if fim <= inicio or fim - inicio > VALIDADE_MAXIMA:
            raise AutorizacaoNegada(
                "Validade deve ser positiva e de no máximo 15 minutos."
            )


@dataclass(frozen=True)
class PermissaoCallback:
    autorizacao_id: str
    tentativa_id: int
    referencia: str
    commit_seq: int


@dataclass(frozen=True)
class ResultadoHomologacao:
    callback_invocado: bool
    resposta: Any = None


class ArmazenamentoTransacionalFalso:
    """Snapshot único, útil também com ``Manager.dict`` e ``Manager.RLock``.

    A troca do snapshot sob o lock representa o ponto de commit do modelo. Um
    processo real, filesystem ou banco não herda garantias deste fake.
    """

    def __init__(
        self,
        state: MutableMapping[str, Any] | None = None,
        lock: Any | None = None,
    ) -> None:
        self._state = state if state is not None else {}
        self._lock = lock if lock is not None else RLock()
        with self._lock:
            if "snapshot" not in self._state:
                self._state["snapshot"] = {
                    "tentativas": {},
                    "autorizacoes": {},
                    "auditoria": [],
                    "commit_seq": 0,
                }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state["snapshot"])

    def cadastrar_tentativa(self, tentativa: TentativaHomologacao) -> None:
        tentativa.validar()
        with self._lock:
            estado = copy.deepcopy(self._state["snapshot"])
            chave_ctrc = (tentativa.serie.upper(), tentativa.numero)
            if tentativa.tentativa_id in estado["tentativas"]:
                raise ConflitoPersistente("Tentativa já cadastrada.")
            if any(
                (item["serie"].upper(), item["numero"]) == chave_ctrc
                for item in estado["tentativas"].values()
            ):
                raise ConflitoPersistente("CTRC já reservado.")
            estado["tentativas"][tentativa.tentativa_id] = asdict(tentativa)
            estado["commit_seq"] += 1
            self._state["snapshot"] = estado

    def cadastrar_autorizacao(
        self, autorizacao: AutorizacaoHomologacao
    ) -> None:
        autorizacao.validar_estrutura()
        with self._lock:
            estado = copy.deepcopy(self._state["snapshot"])
            if autorizacao.tentativa_id not in estado["tentativas"]:
                raise ConflitoPersistente("Tentativa não cadastrada.")
            if autorizacao.autorizacao_id in estado["autorizacoes"]:
                raise ConflitoPersistente("Autorização já cadastrada.")
            if any(
                item["tentativa_id"] == autorizacao.tentativa_id
                for item in estado["autorizacoes"].values()
            ):
                raise ConflitoPersistente(
                    "A tentativa já possui autorização persistida."
                )
            estado["autorizacoes"][autorizacao.autorizacao_id] = asdict(
                autorizacao
            )
            estado["commit_seq"] += 1
            self._state["snapshot"] = estado

    def revogar(self, autorizacao_id: str, agora: datetime) -> None:
        with self._lock:
            estado = copy.deepcopy(self._state["snapshot"])
            registro = estado["autorizacoes"].get(autorizacao_id)
            if registro is None or registro["status"] != "pendente":
                raise AutorizacaoNegada("Autorização não pode ser revogada.")
            registro["status"] = "revogada"
            registro["revogada_em"] = _utc(agora)
            estado["auditoria"].append({
                "evento": "autorizacao_revogada",
                "autorizacao_id": autorizacao_id,
            })
            estado["commit_seq"] += 1
            self._state["snapshot"] = estado

    @staticmethod
    def _validar_vinculo(
        autorizacao: dict[str, Any], tentativa: dict[str, Any], agora: datetime
    ) -> None:
        if autorizacao["status"] != "pendente":
            raise AutorizacaoNegada("Autorização não está pendente.")
        if tentativa["status"] != "reservado":
            raise AutorizacaoNegada("Tentativa não está reservada.")
        instante = _utc(agora)
        if instante < _utc(autorizacao["emitida_em"]) or instante >= _utc(
            autorizacao["expira_em"]
        ):
            raise AutorizacaoNegada("Autorização expirada ou ainda inválida.")
        for campo in (
            "tentativa_id", "serie", "numero", "seq_ctrc", "familia",
            "local", "referencia", "revisao_codigo",
        ):
            if autorizacao[campo] != tentativa[campo]:
                raise AutorizacaoNegada(
                    "Autorização diverge da tentativa persistida."
                )

        tecnico = IdentidadeAutenticada(**autorizacao["aprovador_tecnico"])
        operacional = IdentidadeAutenticada(
            **autorizacao["aprovador_operacional"]
        )
        tecnico.validar(PAPEL_TECNICO)
        operacional.validar(PAPEL_OPERACIONAL)
        if (tecnico.issuer, tecnico.subject_id) == (
            operacional.issuer, operacional.subject_id
        ):
            raise AutorizacaoNegada("Aprovadores devem ter contas distintas.")
        if (tecnico.person_issuer, tecnico.person_id) == (
            operacional.person_issuer, operacional.person_id
        ):
            raise AutorizacaoNegada("Aprovadores devem ser pessoas distintas.")

    def consumir_e_iniciar_envio(
        self,
        autorizacao_id: str,
        tentativa_id: int,
        agora: datetime,
        *,
        falha: str | None = None,
    ) -> PermissaoCallback:
        """Consome autorização e muda para envio_iniciado no mesmo commit.

        Não existe retry interno. Deadlock, timeout, perda de conexão e commit
        ambíguo são devolvidos ao chamador sem permissão para callback.
        """
        with self._lock:
            estado = copy.deepcopy(self._state["snapshot"])
            tentativa = estado["tentativas"].get(tentativa_id)
            autorizacao = estado["autorizacoes"].get(autorizacao_id)
            if tentativa is None or autorizacao is None:
                raise AutorizacaoNegada("Tentativa ou autorização inexistente.")
            if autorizacao["tentativa_id"] != tentativa_id:
                raise AutorizacaoNegada("Autorização não pertence à tentativa.")
            self._validar_vinculo(autorizacao, tentativa, agora)

            if falha == "auditoria":
                raise FalhaTransacional("Falha ao registrar auditoria.")
            if falha in {
                "antes_commit", "deadlock", "timeout_trava", "conexao_perdida"
            }:
                raise FalhaTransacional("Transação não confirmada.")

            autorizacao["status"] = "consumida"
            autorizacao["consumida_em"] = _utc(agora)
            if falha == "entre_updates":
                raise FalhaTransacional("Transação interrompida entre updates.")
            tentativa["status"] = "envio_iniciado"
            tentativa["envio_iniciado_em"] = _utc(agora)
            estado["auditoria"].append({
                "evento": "autorizacao_consumida_envio_iniciado",
                "autorizacao_id": autorizacao_id,
                "tentativa_id": tentativa_id,
            })
            estado["commit_seq"] += 1

            if falha == "commit_ambiguo_nao_confirmado":
                raise CommitAmbiguo(
                    "Resultado do commit desconhecido; callback proibido."
                )
            if falha == "commit_ambiguo_confirmado":
                self._state["snapshot"] = estado
                raise CommitAmbiguo(
                    "Resultado do commit desconhecido; callback proibido."
                )

            self._state["snapshot"] = estado
            permissao = PermissaoCallback(
                autorizacao_id=autorizacao_id,
                tentativa_id=tentativa_id,
                referencia=tentativa["referencia"],
                commit_seq=estado["commit_seq"],
            )
            if falha == "apos_commit_antes_callback":
                raise ProcessoInterrompido(
                    "Processo interrompido após commit; callback não executado."
                )
            return permissao

    def marcar_incerto(self, tentativa_id: int, agora: datetime) -> None:
        """Fecha uma falha do callback sem jamais reabrir a tentativa."""
        with self._lock:
            estado = copy.deepcopy(self._state["snapshot"])
            tentativa = estado["tentativas"].get(tentativa_id)
            if tentativa is None or tentativa["status"] != "envio_iniciado":
                raise FalhaTransacional("Tentativa não pode ser marcada incerta.")
            tentativa["status"] = "incerto"
            tentativa["incerto_em"] = _utc(agora)
            estado["auditoria"].append({
                "evento": "resultado_externo_incerto",
                "tentativa_id": tentativa_id,
            })
            estado["commit_seq"] += 1
            self._state["snapshot"] = estado


def executar_homologacao(
    armazenamento: ArmazenamentoTransacionalFalso,
    *,
    autorizacao_id: str,
    tentativa_id: int,
    agora: datetime,
    callback: Callable[[PermissaoCallback], Any],
    falha: str | None = None,
) -> ResultadoHomologacao:
    permissao = armazenamento.consumir_e_iniciar_envio(
        autorizacao_id, tentativa_id, agora, falha=falha
    )
    try:
        resposta = callback(permissao)
    except Exception:
        armazenamento.marcar_incerto(tentativa_id, agora)
        raise
    return ResultadoHomologacao(callback_invocado=True, resposta=resposta)


@dataclass(frozen=True)
class ConfiguracaoMySQLLaboratorio:
    host: str
    porta: int
    schema: str
    usuario: str
    server_uuid_esperado: str
    flag_explicita: bool


def validar_configuracao_mysql_laboratorio(
    config: ConfiguracaoMySQLLaboratorio,
) -> None:
    """Valida a allowlist literal antes de qualquer futura abertura de conexão."""
    if not config.flag_explicita:
        raise AutorizacaoNegada("Laboratório não autorizado explicitamente.")
    if (
        config.host != "127.0.0.1"
        or config.porta != 3308
        or config.schema != "oc73_autorizacao_transacional_test"
        or config.usuario != "oc73_tester"
        or not str(config.server_uuid_esperado or "").strip()
    ):
        raise AutorizacaoNegada("Identidade do laboratório fora da allowlist.")
    if config.schema.casefold() == "automationx":
        raise AutorizacaoNegada("Schema operacional é proibido.")
