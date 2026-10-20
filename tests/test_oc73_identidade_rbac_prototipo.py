"""Testes offline do contrato de identidade/RBAC; nenhum IdP é acessado."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.middleware.sessions import SessionMiddleware

from prototypes.oc73_identidade_rbac import (
    Acao,
    EscopoAprovacao,
    IdentidadeCorporativa,
    IdentidadeErro,
    PERMISSOES,
    Papel,
    configuracao_cookie_segura_esperada,
    exigir_permissao,
    mensagem_autenticacao_sanitizada,
    possui_permissao,
    validar_dupla_aprovacao,
)


AGORA = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)
REFERENCIA = "OC73-A1B2C3D4E5F60718293A"
ISSUER = "https://idp.corporativo.invalid"
PERSON_ISSUER = "urn:diretorio-corporativo:person"
ISSUERS_CONFIAVEIS = frozenset({ISSUER})
PERSON_ISSUERS_CONFIAVEIS = frozenset({PERSON_ISSUER})


def identidade(nome, papel, **mudancas):
    dados = {
        "issuer": ISSUER,
        "subject_id": f"subject:{nome}",
        "person_issuer": PERSON_ISSUER,
        "person_id": f"person:{nome}",
        "person_verified": True,
        "person_binding_expires_at": AGORA + timedelta(hours=1),
        "session_id": f"session:{nome}",
        "authenticated_at": AGORA - timedelta(minutes=1),
        "expires_at": AGORA + timedelta(minutes=4),
        "roles": frozenset({papel}),
        "mfa": True,
    }
    dados.update(mudancas)
    return IdentidadeCorporativa(**dados)


def escopo(**mudancas):
    dados = {
        "tentativa_id": 701,
        "referencia": REFERENCIA,
        "revisao_codigo": "abcdef1234567890",
    }
    dados.update(mudancas)
    return EscopoAprovacao(**dados)


def dupla(**mudancas):
    dados = {
        "tecnico": identidade("tecnico", Papel.APPROVER_TECHNICAL),
        "operacional": identidade(
            "operacional", Papel.APPROVER_OPERATIONAL
        ),
        "executor": identidade("executor", Papel.EXECUTOR),
        "escopo_tecnico": escopo(),
        "escopo_operacional": escopo(),
        "escopo_atual": escopo(),
        "agora": AGORA,
        "issuers_confiaveis": ISSUERS_CONFIAVEIS,
        "person_issuers_confiaveis": PERSON_ISSUERS_CONFIAVEIS,
    }
    dados.update(mudancas)
    return validar_dupla_aprovacao(**dados)


def test_matriz_rbac_aplica_menor_privilegio():
    assert PERMISSOES[Papel.APPROVER_TECHNICAL] == {
        Acao.VIEW_REQUEST,
        Acao.APPROVE_TECHNICAL,
        Acao.REVOKE_APPROVAL,
    }
    assert Acao.EXECUTE not in PERMISSOES[Papel.APPROVER_TECHNICAL]
    assert Acao.APPROVE_TECHNICAL not in PERMISSOES[Papel.EXECUTOR]
    assert PERMISSOES[Papel.PERMISSION_ADMIN] == {Acao.ADMIN_PERMISSIONS}


def test_fluxo_valido_exige_tres_pessoas_e_papeis_corretos():
    assert dupla() is None


def test_username_ou_email_nao_substitui_subject_e_person_id():
    usuario = identidade(
        "usuario@example.test",
        Papel.APPROVER_TECHNICAL,
        subject_id="",
        person_id="",
    )
    with pytest.raises(IdentidadeErro, match="incompleta"):
        usuario.validar(
            AGORA,
            issuers_confiaveis=ISSUERS_CONFIAVEIS,
            person_issuers_confiaveis=PERSON_ISSUERS_CONFIAVEIS,
        )


def test_duas_contas_da_mesma_pessoa_sao_rejeitadas():
    tecnico = identidade(
        "conta-a", Papel.APPROVER_TECHNICAL, person_id="person:1"
    )
    operacional = identidade(
        "conta-b", Papel.APPROVER_OPERATIONAL, person_id="person:1"
    )
    with pytest.raises(IdentidadeErro, match="Pessoas"):
        dupla(tecnico=tecnico, operacional=operacional)


def test_mesma_conta_e_rejeitada_mesmo_com_dois_papeis():
    ambos = identidade(
        "ambos",
        Papel.APPROVER_TECHNICAL,
        roles=frozenset({
            Papel.APPROVER_TECHNICAL,
            Papel.APPROVER_OPERATIONAL,
        }),
    )
    with pytest.raises(IdentidadeErro, match="Contas"):
        dupla(tecnico=ambos, operacional=ambos)


def test_mesmo_subject_em_emissores_diferentes_nao_e_mesma_conta():
    tecnico = identidade("a", Papel.APPROVER_TECHNICAL, subject_id="subject:1")
    outro_issuer = "https://segundo-idp.invalid"
    operacional = identidade(
        "b",
        Papel.APPROVER_OPERATIONAL,
        issuer=outro_issuer,
        subject_id="subject:1",
    )
    dupla(
        tecnico=tecnico,
        operacional=operacional,
        issuers_confiaveis=frozenset({ISSUER, outro_issuer}),
    )


@pytest.mark.parametrize(
    "mudancas",
    [
        {"person_verified": False},
        {"person_issuer": ""},
        {"person_binding_expires_at": AGORA},
    ],
)
def test_person_id_sem_comprovacao_valida_e_rejeitado(mudancas):
    tecnico = identidade("tecnico", Papel.APPROVER_TECHNICAL, **mudancas)
    with pytest.raises(IdentidadeErro):
        dupla(tecnico=tecnico)


def test_executor_nao_pode_ser_um_dos_aprovadores():
    tecnico = identidade("tecnico", Papel.APPROVER_TECHNICAL)
    executor = identidade(
        "executor", Papel.EXECUTOR, person_id=tecnico.person_id
    )
    with pytest.raises(IdentidadeErro, match="própria execução"):
        dupla(tecnico=tecnico, executor=executor)


def test_papel_errado_falha_server_side():
    tecnico = identidade("tecnico", Papel.AUDITOR)
    with pytest.raises(IdentidadeErro, match="não autorizada"):
        dupla(tecnico=tecnico)


@pytest.mark.parametrize(
    "mudancas,mensagem",
    [
        ({"mfa": False}, "MFA"),
        ({"expires_at": AGORA}, "expirada"),
        (
            {"authenticated_at": AGORA - timedelta(minutes=16)},
            "Reautenticação",
        ),
    ],
)
def test_mfa_expiracao_e_frescor_sao_revalidados(mudancas, mensagem):
    tecnico = identidade("tecnico", Papel.APPROVER_TECHNICAL, **mudancas)
    with pytest.raises(IdentidadeErro, match=mensagem):
        dupla(tecnico=tecnico)


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("tentativa_id", 702),
        ("referencia", "OC73-FFFFFFFFFFFFFFFFFFFF"),
        ("revisao_codigo", "1234567abcdef"),
    ],
)
def test_escopo_divergente_e_rejeitado(campo, valor):
    with pytest.raises(IdentidadeErro, match="diverge"):
        dupla(escopo_atual=escopo(**{campo: valor}))


def test_auditor_pode_consultar_evidencia_mas_nao_aprovar():
    auditor = identidade("auditor", Papel.AUDITOR)
    exigir_permissao(
        auditor,
        Acao.VIEW_EVIDENCE,
        AGORA,
        issuers_confiaveis=ISSUERS_CONFIAVEIS,
        person_issuers_confiaveis=PERSON_ISSUERS_CONFIAVEIS,
    )
    assert possui_permissao(auditor, Acao.APPROVE_TECHNICAL) is False


def test_mensagem_de_erro_esperada_nao_vaza_excecao():
    segredo = "senha-ultrassecreta"
    mensagem = mensagem_autenticacao_sanitizada(RuntimeError(segredo))
    assert segredo not in mensagem
    assert "RuntimeError" not in mensagem


def test_runtime_atual_nao_explicita_cookie_secure():
    fonte = Path("web/main.py").read_text(encoding="utf-8")
    trecho = fonte.split("app.add_middleware(", 1)[1].split(")", 1)[0]
    assert "SessionMiddleware" in trecho
    assert "https_only" not in trecho
    assert "same_site" not in trecho


def test_starlette_atual_tem_httponly_lax_sem_secure_por_padrao():
    middleware = SessionMiddleware(lambda scope, receive, send: None, "x" * 48)
    assert "httponly" in middleware.security_flags
    assert "samesite=lax" in middleware.security_flags
    assert "secure" not in middleware.security_flags


def test_configuracao_segura_proposta_inclui_secure_e_strict():
    config = configuracao_cookie_segura_esperada()
    middleware = SessionMiddleware(
        lambda scope, receive, send: None,
        "x" * 48,
        **config,
    )
    assert "httponly" in middleware.security_flags
    assert "samesite=strict" in middleware.security_flags
    assert "secure" in middleware.security_flags


def test_login_atual_propaga_str_exc_e_nao_tem_rate_limit_ou_csrf():
    fonte = Path("web/routes.py").read_text(encoding="utf-8")
    inicio = fonte.index("def login_run(")
    fim = fonte.index("\n\n@router.get(\"/logout\")", inicio)
    trecho = fonte[inicio:fim]
    assert '"error": str(exc)' in trecho
    assert "rate_limit" not in trecho.casefold()
    assert "csrf" not in trecho.casefold()


def test_login_substitui_sessao_e_logout_invalida_armazenamento_local():
    fonte = Path("web/routes.py").read_text(encoding="utf-8")
    assert fonte.index("remover_sessao(request)") < fonte.index(
        "session_id = criar_sessao(client)"
    )
    assert 'def logout(request: Request):\n    remover_sessao(request)' in fonte
