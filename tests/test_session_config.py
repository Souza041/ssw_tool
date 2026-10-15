import importlib
import inspect
import logging
import secrets
import sys

import pytest
from starlette.middleware.sessions import SessionMiddleware

from web.session_config import (
    SECRET_ENV_NAME,
    SessionConfigurationError,
    carregar_configuracao_sessao,
    validar_session_secret_key,
)


def chave_valida():
    return secrets.token_urlsafe(48)


def test_chave_ausente_falha_fechada():
    with pytest.raises(SessionConfigurationError) as erro:
        carregar_configuracao_sessao({})
    assert str(erro.value) == "Configuração segura de sessão indisponível."


@pytest.mark.parametrize(
    "valor",
    [
        "",
        "   ",
        "troque-essa-chave-depois",
        "change-me" * 20,
        "A" * 128,
        "abc123" * 20,
        "0123456789abcdef" * 8,
        " segredo-com-espacos-nas-bordas-que-nao-pode-ser-usado ",
    ],
)
def test_chaves_fracas_ou_previsiveis_sao_recusadas(valor):
    with pytest.raises(SessionConfigurationError):
        validar_session_secret_key(valor)


def test_chave_gerada_por_token_urlsafe_48_e_aceita():
    segredo = chave_valida()
    assert validar_session_secret_key(segredo) == segredo
    configuracao = carregar_configuracao_sessao({
        SECRET_ENV_NAME: segredo,
        "SSW_ENVIRONMENT": "production",
    })
    assert configuracao.secret_key == segredo
    assert configuracao.environment == "production"
    assert configuracao.source == "mapeamento explícito"
    assert segredo not in repr(configuracao)


def test_erro_e_logs_nao_expoem_chave_rejeitada(caplog):
    segredo_fraco = "password-" + ("A" * 80)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SessionConfigurationError) as erro:
            carregar_configuracao_sessao({SECRET_ENV_NAME: segredo_fraco})

    assert segredo_fraco not in str(erro.value)
    assert segredo_fraco not in repr(erro.value)
    assert segredo_fraco not in caplog.text


def test_ambiente_do_supervisor_tem_precedencia_sobre_dotenv(
    monkeypatch,
    tmp_path,
):
    chave_supervisor = chave_valida()
    chave_dotenv = chave_valida()
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"{SECRET_ENV_NAME}={chave_dotenv}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(SECRET_ENV_NAME, chave_supervisor)
    monkeypatch.setenv("SSW_ENVIRONMENT", "production")

    configuracao = carregar_configuracao_sessao(env_file=env_file)

    assert configuracao.secret_key == chave_supervisor
    assert configuracao.source == "ambiente do processo"


def test_dotenv_da_raiz_e_carregado_por_caminho_explicito(
    monkeypatch,
    tmp_path,
):
    segredo = chave_valida()
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"{SECRET_ENV_NAME}={segredo}\nSSW_ENVIRONMENT=production\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(SECRET_ENV_NAME, raising=False)
    monkeypatch.delenv("SSW_ENVIRONMENT", raising=False)

    configuracao = carregar_configuracao_sessao(env_file=env_file)

    assert configuracao.secret_key == segredo
    assert configuracao.source == ".env da raiz do projeto"


def test_session_middleware_continua_assinando_cookies_compativeis():
    async def aplicacao(scope, receive, send):
        return None

    segredo = chave_valida()
    middleware = SessionMiddleware(
        aplicacao,
        secret_key=segredo,
        max_age=60,
    )
    payload = b'{"ssw_session_id":"sessao-valida"}'
    cookie_assinado = middleware.signer.sign(payload)

    assert middleware.signer.unsign(cookie_assinado) == payload
    assert middleware.max_age == 60


def test_web_main_instala_middleware_com_configuracao_validada(
    monkeypatch,
    caplog,
):
    segredo = chave_valida()
    monkeypatch.setenv(SECRET_ENV_NAME, segredo)
    monkeypatch.setenv("SSW_ENVIRONMENT", "test")
    sys.modules.pop("web.main", None)

    with caplog.at_level(logging.INFO):
        modulo = importlib.import_module("web.main")

    middleware = next(
        item
        for item in modulo.app.user_middleware
        if item.cls is SessionMiddleware
    )
    assert middleware.kwargs["secret_key"] == segredo
    assert segredo not in caplog.text
    fonte_main = inspect.getsource(modulo)
    assert fonte_main.index(
        "session_security = carregar_configuracao_sessao()"
    ) < fonte_main.index("from web.routes import router")
