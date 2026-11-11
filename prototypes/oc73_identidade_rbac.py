"""Contrato offline de identidade corporativa e RBAC para a OC73.

Não implementa login, não conhece endpoints de IdP e não é importado pelo
runtime. Os campos representam claims mínimos que um provedor futuro deverá
comprovar por contrato verificável.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import FrozenSet


class Papel(str, Enum):
    APPROVER_TECHNICAL = "OC73_APPROVER_TECHNICAL"
    APPROVER_OPERATIONAL = "OC73_APPROVER_OPERATIONAL"
    EXECUTOR = "OC73_EXECUTOR"
    AUDITOR = "OC73_AUDITOR"
    PERMISSION_ADMIN = "OC73_PERMISSION_ADMIN"


class Acao(str, Enum):
    VIEW_REQUEST = "visualizar_solicitacao"
    APPROVE_TECHNICAL = "aprovar_tecnicamente"
    APPROVE_OPERATIONAL = "aprovar_operacionalmente"
    REVOKE_APPROVAL = "revogar_aprovacao"
    EXECUTE = "executar_homologacao"
    VIEW_EVIDENCE = "consultar_evidencias"
    ADMIN_PERMISSIONS = "administrar_permissoes"


PERMISSOES = {
    Papel.APPROVER_TECHNICAL: frozenset({
        Acao.VIEW_REQUEST,
        Acao.APPROVE_TECHNICAL,
        Acao.REVOKE_APPROVAL,
    }),
    Papel.APPROVER_OPERATIONAL: frozenset({
        Acao.VIEW_REQUEST,
        Acao.APPROVE_OPERATIONAL,
        Acao.REVOKE_APPROVAL,
    }),
    Papel.EXECUTOR: frozenset({Acao.VIEW_REQUEST, Acao.EXECUTE}),
    Papel.AUDITOR: frozenset({
        Acao.VIEW_REQUEST,
        Acao.VIEW_EVIDENCE,
    }),
    Papel.PERMISSION_ADMIN: frozenset({Acao.ADMIN_PERMISSIONS}),
}

MAX_AUTH_AGE = timedelta(minutes=15)


class IdentidadeErro(RuntimeError):
    """Erro fail-closed que não inclui claims ou tokens."""


@dataclass(frozen=True)
class IdentidadeCorporativa:
    issuer: str
    subject_id: str
    person_issuer: str
    person_id: str
    person_verified: bool
    person_binding_expires_at: datetime
    session_id: str
    authenticated_at: datetime
    expires_at: datetime
    roles: FrozenSet[Papel]
    mfa: bool

    def validar(
        self,
        agora: datetime,
        *,
        issuers_confiaveis: FrozenSet[str],
        person_issuers_confiaveis: FrozenSet[str],
        exigir_mfa: bool = True,
    ) -> None:
        for nome in (
            "issuer", "subject_id", "person_issuer", "person_id", "session_id"
        ):
            valor = getattr(self, nome)
            if not isinstance(valor, str) or not valor.strip() or len(valor) > 191:
                raise IdentidadeErro("Identidade corporativa incompleta.")
        if (
            self.authenticated_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.person_binding_expires_at.tzinfo is None
        ):
            raise IdentidadeErro("Identidade corporativa sem tempo verificável.")
        instante = _utc(agora)
        autenticada = _utc(self.authenticated_at)
        expira = _utc(self.expires_at)
        vinculo_expira = _utc(self.person_binding_expires_at)
        if (
            self.issuer not in issuers_confiaveis
            or self.person_issuer not in person_issuers_confiaveis
        ):
            raise IdentidadeErro("Namespace de identidade não autorizado.")
        if self.person_verified is not True or instante >= vinculo_expira:
            raise IdentidadeErro("Vínculo corporativo pessoa-conta não verificado.")
        if instante < autenticada or instante >= expira:
            raise IdentidadeErro("Sessão corporativa inválida ou expirada.")
        if instante - autenticada > MAX_AUTH_AGE:
            raise IdentidadeErro("Reautenticação reforçada é obrigatória.")
        if exigir_mfa and not self.mfa:
            raise IdentidadeErro("MFA é obrigatório para esta operação.")
        if not self.roles or not all(isinstance(role, Papel) for role in self.roles):
            raise IdentidadeErro("Papéis corporativos inválidos.")


@dataclass(frozen=True)
class EscopoAprovacao:
    tentativa_id: int
    referencia: str
    revisao_codigo: str

    def validar(self) -> None:
        if isinstance(self.tentativa_id, bool) or not isinstance(
            self.tentativa_id, int
        ) or self.tentativa_id <= 0:
            raise IdentidadeErro("Tentativa inválida.")
        if not self.referencia.startswith("OC73-") or len(self.referencia) != 25:
            raise IdentidadeErro("Referência inválida.")
        sufixo = self.referencia[5:]
        if any(caractere not in "0123456789ABCDEF" for caractere in sufixo):
            raise IdentidadeErro("Referência inválida.")
        revisao = self.revisao_codigo
        if not (7 <= len(revisao) <= 64) or any(
            caractere not in "0123456789abcdef" for caractere in revisao
        ):
            raise IdentidadeErro("Revisão inválida.")


def _utc(valor: datetime) -> datetime:
    if not isinstance(valor, datetime) or valor.tzinfo is None:
        raise IdentidadeErro("Timestamp verificável é obrigatório.")
    return valor.astimezone(timezone.utc)


def possui_permissao(identidade: IdentidadeCorporativa, acao: Acao) -> bool:
    return any(acao in PERMISSOES.get(papel, ()) for papel in identidade.roles)


def exigir_permissao(
    identidade: IdentidadeCorporativa,
    acao: Acao,
    agora: datetime,
    *,
    issuers_confiaveis: FrozenSet[str],
    person_issuers_confiaveis: FrozenSet[str],
) -> None:
    identidade.validar(
        agora,
        issuers_confiaveis=issuers_confiaveis,
        person_issuers_confiaveis=person_issuers_confiaveis,
    )
    if not possui_permissao(identidade, acao):
        raise IdentidadeErro("Operação não autorizada.")


def validar_dupla_aprovacao(
    *,
    tecnico: IdentidadeCorporativa,
    operacional: IdentidadeCorporativa,
    executor: IdentidadeCorporativa,
    escopo_tecnico: EscopoAprovacao,
    escopo_operacional: EscopoAprovacao,
    escopo_atual: EscopoAprovacao,
    agora: datetime,
    issuers_confiaveis: FrozenSet[str],
    person_issuers_confiaveis: FrozenSet[str],
) -> None:
    """Revalida identidade, RBAC, segregação e escopo no consumo."""
    for identidade, acao in (
        (tecnico, Acao.APPROVE_TECHNICAL),
        (operacional, Acao.APPROVE_OPERATIONAL),
        (executor, Acao.EXECUTE),
    ):
        exigir_permissao(
            identidade,
            acao,
            agora,
            issuers_confiaveis=issuers_confiaveis,
            person_issuers_confiaveis=person_issuers_confiaveis,
        )
    for escopo in (escopo_tecnico, escopo_operacional, escopo_atual):
        escopo.validar()
    if not (escopo_tecnico == escopo_operacional == escopo_atual):
        raise IdentidadeErro("Escopo aprovado diverge da tentativa atual.")
    if (tecnico.issuer, tecnico.subject_id) == (
        operacional.issuer, operacional.subject_id
    ):
        raise IdentidadeErro("Contas aprovadoras devem ser distintas.")
    if (tecnico.person_issuer, tecnico.person_id) == (
        operacional.person_issuer, operacional.person_id
    ):
        raise IdentidadeErro("Pessoas aprovadoras devem ser distintas.")
    pessoa_executor = (executor.person_issuer, executor.person_id)
    if pessoa_executor in {
        (tecnico.person_issuer, tecnico.person_id),
        (operacional.person_issuer, operacional.person_id),
    }:
        raise IdentidadeErro("Executor não pode aprovar a própria execução.")


def mensagem_autenticacao_sanitizada(_: BaseException) -> str:
    """Comportamento esperado para futura correção do login operacional."""
    return "Não foi possível autenticar. Verifique os dados e tente novamente."


def configuracao_cookie_segura_esperada() -> dict[str, object]:
    """Parâmetros esperados; não altera o SessionMiddleware atual."""
    return {
        "https_only": True,
        "same_site": "strict",
        "max_age": 15 * 60,
    }
