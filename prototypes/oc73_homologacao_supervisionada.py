"""Protótipo offline de autorização e instrumentação da homologação OC73.

Este módulo não é importado pelo runtime, não lê ambiente, não abre rede e não
acessa banco. Ele demonstra propriedades locais; não autoriza homologação real.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


REFERENCIA_RE = re.compile(r"OC73-[0-9A-F]{20}\Z")
REVISAO_RE = re.compile(r"[0-9a-f]{7,64}\Z")
AUTORIZACAO_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
VALIDADE_MAXIMA = timedelta(minutes=15)
VERSAO_TOKEN = 1
CAMPOS_AUTORIZACAO = frozenset({
    "autorizacao_id",
    "serie",
    "numero",
    "seq_ctrc",
    "familia",
    "local",
    "referencia",
    "revisao_codigo",
    "emitida_em",
    "expira_em",
    "aceite_tecnico",
    "aceite_operacional",
})


class AutorizacaoErro(RuntimeError):
    """Erro seguro, sem conteúdo assinado ou segredo."""


class AutorizacaoInvalida(AutorizacaoErro):
    pass


class AutorizacaoExpirada(AutorizacaoErro):
    pass


class AutorizacaoDivergente(AutorizacaoErro):
    pass


class AutorizacaoReutilizada(AutorizacaoErro):
    pass


class AutorizacaoRevogada(AutorizacaoErro):
    pass


class FalhaConsumoAutorizacao(AutorizacaoErro):
    pass


def _utc(valor: datetime) -> datetime:
    if not isinstance(valor, datetime) or valor.tzinfo is None:
        raise AutorizacaoInvalida("Timestamp UTC com timezone é obrigatório.")
    return valor.astimezone(timezone.utc)


def _iso_utc(valor: datetime) -> str:
    return _utc(valor).isoformat().replace("+00:00", "Z")


def _parse_utc(valor: Any) -> datetime:
    try:
        texto = str(valor)
        if texto.endswith("Z"):
            texto = texto[:-1] + "+00:00"
        return _utc(datetime.fromisoformat(texto))
    except (TypeError, ValueError) as exc:
        raise AutorizacaoInvalida("Timestamp da autorização inválido.") from exc


def _normalizar_identificador(valor: Any, nome: str) -> str:
    texto = str(valor or "").strip().upper()
    if not texto or len(texto) > 80:
        raise AutorizacaoInvalida(f"Identificador {nome} inválido.")
    return texto


def _normalizar_revisao(valor: Any) -> str:
    revisao = str(valor or "").strip().lower()
    if REVISAO_RE.fullmatch(revisao) is None:
        raise AutorizacaoInvalida("Revisão de código inválida.")
    return revisao


def _normalizar_referencia(valor: Any) -> str:
    referencia = str(valor or "").strip()
    if REFERENCIA_RE.fullmatch(referencia) is None:
        raise AutorizacaoInvalida("Referência OC73 inválida.")
    return referencia


def _validar_chave(chave: bytes) -> bytes:
    if not isinstance(chave, bytes) or len(chave) < 32:
        raise AutorizacaoInvalida(
            "Chave de assinatura do protótipo deve ter ao menos 32 bytes."
        )
    return chave


@dataclass(frozen=True)
class ConteudoAutorizacao:
    autorizacao_id: str
    serie: str
    numero: str
    seq_ctrc: str
    familia: str
    local: str
    referencia: str
    revisao_codigo: str
    emitida_em: str
    expira_em: str
    aceite_tecnico: str
    aceite_operacional: str

    @classmethod
    def criar(
        cls,
        *,
        autorizacao_id: str,
        serie: str,
        numero: str,
        seq_ctrc: str,
        familia: str,
        local: str,
        referencia: str,
        revisao_codigo: str,
        emitida_em: datetime,
        expira_em: datetime,
        aceite_tecnico: str,
        aceite_operacional: str,
    ) -> "ConteudoAutorizacao":
        autorizacao_id = str(autorizacao_id or "").strip().lower()
        if AUTORIZACAO_ID_RE.fullmatch(autorizacao_id) is None:
            raise AutorizacaoInvalida("ID de autorização inválido.")

        tecnico = _normalizar_identificador(
            aceite_tecnico, "aceite_tecnico"
        )
        operacional = _normalizar_identificador(
            aceite_operacional, "aceite_operacional"
        )
        if hmac.compare_digest(tecnico, operacional):
            raise AutorizacaoInvalida(
                "Aceites técnico e operacional devem ser distintos."
            )

        inicio = _utc(emitida_em)
        fim = _utc(expira_em)
        if fim <= inicio or fim - inicio > VALIDADE_MAXIMA:
            raise AutorizacaoInvalida(
                "Validade deve ser positiva e de no máximo 15 minutos."
            )

        return cls(
            autorizacao_id=autorizacao_id,
            serie=_normalizar_identificador(serie, "serie"),
            numero=_normalizar_identificador(numero, "numero"),
            seq_ctrc=_normalizar_identificador(seq_ctrc, "seq_ctrc"),
            familia=_normalizar_identificador(familia, "familia"),
            local=_normalizar_identificador(local, "local"),
            referencia=_normalizar_referencia(referencia),
            revisao_codigo=_normalizar_revisao(revisao_codigo),
            emitida_em=_iso_utc(inicio),
            expira_em=_iso_utc(fim),
            aceite_tecnico=tecnico,
            aceite_operacional=operacional,
        )


@dataclass(frozen=True)
class ContextoTentativa:
    serie: str
    numero: str
    seq_ctrc: str
    familia: str
    local: str
    referencia: str
    revisao_codigo: str
    status_persistido: str

    def canonico(self) -> dict[str, str]:
        return {
            "serie": _normalizar_identificador(self.serie, "serie"),
            "numero": _normalizar_identificador(self.numero, "numero"),
            "seq_ctrc": _normalizar_identificador(self.seq_ctrc, "seq_ctrc"),
            "familia": _normalizar_identificador(self.familia, "familia"),
            "local": _normalizar_identificador(self.local, "local"),
            "referencia": _normalizar_referencia(self.referencia),
            "revisao_codigo": _normalizar_revisao(self.revisao_codigo),
            "status_persistido": str(self.status_persistido or "")
            .strip()
            .lower(),
        }


def _json_canonico(valor: Any) -> bytes:
    return json.dumps(
        valor,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def emitir_token(conteudo: ConteudoAutorizacao, chave: bytes) -> dict[str, Any]:
    """Emite token assinado; não persiste nem registra a chave."""
    chave = _validar_chave(chave)
    payload = asdict(conteudo)
    assinatura = hmac.new(
        chave,
        _json_canonico(payload),
        hashlib.sha256,
    ).hexdigest()
    return {
        "versao": VERSAO_TOKEN,
        "payload": payload,
        "assinatura": assinatura,
    }


def _validar_token(token: Any, chave: bytes) -> dict[str, str]:
    chave = _validar_chave(chave)
    if not isinstance(token, dict) or token.get("versao") != VERSAO_TOKEN:
        raise AutorizacaoInvalida("Token de autorização inválido.")
    payload = token.get("payload")
    assinatura = token.get("assinatura")
    if not isinstance(payload, dict) or not isinstance(assinatura, str):
        raise AutorizacaoInvalida("Token de autorização inválido.")
    if set(payload) != CAMPOS_AUTORIZACAO:
        raise AutorizacaoInvalida("Campos da autorização inválidos.")
    esperada = hmac.new(
        chave,
        _json_canonico(payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(assinatura, esperada):
        raise AutorizacaoInvalida("Assinatura da autorização inválida.")
    try:
        conteudo = ConteudoAutorizacao.criar(
            autorizacao_id=payload["autorizacao_id"],
            serie=payload["serie"],
            numero=payload["numero"],
            seq_ctrc=payload["seq_ctrc"],
            familia=payload["familia"],
            local=payload["local"],
            referencia=payload["referencia"],
            revisao_codigo=payload["revisao_codigo"],
            emitida_em=_parse_utc(payload["emitida_em"]),
            expira_em=_parse_utc(payload["expira_em"]),
            aceite_tecnico=payload["aceite_tecnico"],
            aceite_operacional=payload["aceite_operacional"],
        )
    except KeyError as exc:
        raise AutorizacaoInvalida("Token de autorização incompleto.") from exc
    return asdict(conteudo)


class ArmazenamentoUsoUnicoLocal:
    """Marcadores atômicos para um único filesystem local compartilhado.

    O_EXCL impede dois processos cooperantes de consumir o mesmo ID. Não há
    garantia equivalente para hosts distintos ou filesystems de rede.
    """

    def __init__(
        self,
        diretorio: Path,
        *,
        apos_reivindicar: Callable[[], None] | None = None,
    ) -> None:
        self.diretorio = Path(diretorio)
        self.apos_reivindicar = apos_reivindicar
        self.diretorio.mkdir(parents=True, exist_ok=True)

    def _caminho(self, autorizacao_id: str, sufixo: str) -> Path:
        if AUTORIZACAO_ID_RE.fullmatch(autorizacao_id) is None:
            raise AutorizacaoInvalida("ID de autorização inválido.")
        return self.diretorio / f"{autorizacao_id}.{sufixo}"

    @staticmethod
    def _criar_marcador(caminho: Path, registro: dict[str, Any]) -> None:
        descritor = os.open(
            caminho,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descritor, "wb") as arquivo:
            conteudo = _json_canonico(registro)
            arquivo.write(conteudo)
            arquivo.flush()
            os.fsync(arquivo.fileno())

    def revogar(self, autorizacao_id: str, *, agora: datetime) -> bool:
        caminho = self._caminho(autorizacao_id, "revogada")
        try:
            self._criar_marcador(
                caminho,
                {
                    "autorizacao_id": autorizacao_id,
                    "revogada_em": _iso_utc(agora),
                },
            )
            return True
        except FileExistsError:
            return False

    def consumir(
        self,
        autorizacao_id: str,
        *,
        agora: datetime,
        fingerprint_conteudo: str,
    ) -> None:
        revogada = self._caminho(autorizacao_id, "revogada")
        usada = self._caminho(autorizacao_id, "usada")
        if revogada.exists():
            raise AutorizacaoRevogada("Autorização revogada.")
        try:
            self._criar_marcador(
                usada,
                {
                    "autorizacao_id": autorizacao_id,
                    "consumida_em": _iso_utc(agora),
                    "fingerprint": fingerprint_conteudo,
                },
            )
        except FileExistsError as exc:
            raise AutorizacaoReutilizada(
                "Autorização já consumida."
            ) from exc
        except Exception as exc:
            # Se O_EXCL já criou o arquivo, ele permanece como fail-closed.
            raise FalhaConsumoAutorizacao(
                "Falha no consumo da autorização; autorização bloqueada."
            ) from exc

        try:
            if self.apos_reivindicar is not None:
                self.apos_reivindicar()
            if revogada.exists():
                raise AutorizacaoRevogada("Autorização revogada.")
        except AutorizacaoErro:
            raise
        except Exception as exc:
            raise FalhaConsumoAutorizacao(
                "Falha no consumo da autorização; autorização bloqueada."
            ) from exc


def validar_e_consumir(
    *,
    token: dict[str, Any],
    chave: bytes,
    contexto: ContextoTentativa,
    armazenamento: ArmazenamentoUsoUnicoLocal,
    agora: datetime,
) -> str:
    """Valida conteúdo/estado e queima a autorização antes de qualquer POST."""
    payload = _validar_token(token, chave)
    instante = _utc(agora)
    emitida = _parse_utc(payload["emitida_em"])
    expira = _parse_utc(payload["expira_em"])
    if instante < emitida or instante >= expira:
        raise AutorizacaoExpirada("Autorização fora da validade.")

    atual = contexto.canonico()
    if atual["status_persistido"] != "reservado":
        raise AutorizacaoDivergente(
            "Estado persistido não autoriza tentativa supervisionada."
        )
    for campo in (
        "serie",
        "numero",
        "seq_ctrc",
        "familia",
        "local",
        "referencia",
        "revisao_codigo",
    ):
        if not hmac.compare_digest(payload[campo], atual[campo]):
            raise AutorizacaoDivergente(
                "Conteúdo da tentativa diverge da autorização."
            )

    fingerprint = hashlib.sha256(_json_canonico(payload)).hexdigest()
    armazenamento.consumir(
        payload["autorizacao_id"],
        agora=instante,
        fingerprint_conteudo=fingerprint,
    )
    return payload["autorizacao_id"]


@dataclass
class ContadoresPostOffline:
    callback_solicitado: int = 0
    tentativa_transporte: int = 0
    observada_transporte_falso: int = 0
    resposta_recebida: int = 0
    confirmada_por_historico: int = 0
    posts_consultivos: int = 0


class SondaPostOffline:
    """Mede camadas distintas sem registrar payload ou identificador bruto."""

    def __init__(self) -> None:
        self.contadores = ContadoresPostOffline()
        self.eventos_saneados: list[dict[str, str]] = []

    def solicitar_callback(self, callback: Callable[[], Any]) -> Any:
        self.contadores.callback_solicitado += 1
        resultado = callback()
        self.contadores.resposta_recebida += 1
        return resultado

    def registrar_confirmacao_historico(self, confirmou: bool) -> None:
        if confirmou:
            self.contadores.confirmada_por_historico += 1

    @staticmethod
    def _fingerprint(valor: Any) -> str:
        return hashlib.sha256(str(valor or "").encode("utf-8")).hexdigest()[:12]

    def registrar_tentativa(self) -> None:
        self.contadores.tentativa_transporte += 1

    def registrar_observada(self, payload: dict[str, Any]) -> None:
        self.contadores.observada_transporte_falso += 1
        observacao = str(payload.get("f6") or "")
        referencia = REFERENCIA_RE.search(observacao)
        self.eventos_saneados.append({
            "operacao": "lancamento_oc73",
            "seq_ctrc_fingerprint": self._fingerprint(payload.get("seq_ctrc")),
            "referencia_fingerprint": self._fingerprint(
                referencia.group(0) if referencia else ""
            ),
        })

    def registrar_consultivo(self, operacao: str) -> None:
        self.contadores.posts_consultivos += 1
        self.eventos_saneados.append({"operacao": operacao})


class TransporteSSWFalsoInstrumentado:
    """Transporte falso que classifica a operação por endpoint e payload."""

    def __init__(
        self,
        sonda: SondaPostOffline,
        *,
        modo_lancamento: str = "sucesso",
        historico: str = "<historico></historico>",
    ) -> None:
        if modo_lancamento not in {
            "sucesso",
            "ambiguo",
            "timeout_apos_aceite",
            "falha_antes_aceite",
        }:
            raise ValueError("Modo de transporte falso inválido.")
        self.sonda = sonda
        self.modo_lancamento = modo_lancamento
        self.historico = historico

    def get(self, path: str, **kwargs):
        return SimpleNamespace(text="73 - ENTREGA SERA REALIZADA AMANHA")

    def post(self, path: str, data: dict[str, Any] | None = None, **kwargs):
        payload = data or {}
        lancamento = (
            path == "/bin/ssw0122"
            and payload.get("act") == "II3"
            and str(payload.get("f3")) == "73"
            and bool(payload.get("seq_ctrc"))
        )
        if lancamento:
            self.sonda.registrar_tentativa()
            if self.modo_lancamento == "falha_antes_aceite":
                raise ConnectionError("falha simulada antes do aceite")
            self.sonda.registrar_observada(payload)
            if self.modo_lancamento == "timeout_apos_aceite":
                raise TimeoutError("timeout simulado após aceite")
            if self.modo_lancamento == "ambiguo":
                return SimpleNamespace(text="HTTP 200 - operação processada")
            return SimpleNamespace(text="REQUISICAO RECEBIDA")

        operacao = "post_consultivo"
        if path == "/bin/ssw0053" and payload.get("act") == "O":
            operacao = "consulta_historico_op101"
            resposta = self.historico
        elif path == "/bin/ssw0053":
            operacao = "consulta_ctrc_op101"
            resposta = "<consulta></consulta>"
        elif path == "/bin/ssw0230":
            operacao = "solicitacao_relatorio_op455"
            resposta = "<relatorio></relatorio>"
        else:
            resposta = "<navegacao></navegacao>"
        self.sonda.registrar_consultivo(operacao)
        return SimpleNamespace(text=resposta)
