import uuid
import time

from fastapi import Request
from fastapi.responses import RedirectResponse

from ssw.client import SSWClient


CLIENTS: dict[str, SSWClient] = {}
SESSOES_CRIADAS_EM: dict[str, float] = {}
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8


def criar_sessao(client: SSWClient) -> str:
    session_id = str(uuid.uuid4())
    CLIENTS[session_id] = client
    SESSOES_CRIADAS_EM[session_id] = time.monotonic()
    return session_id


def obter_client(request: Request) -> SSWClient | None:
    session_id = request.session.get("ssw_session_id")

    if not session_id:
        return None

    client = CLIENTS.get(session_id)
    criada_em = SESSOES_CRIADAS_EM.get(session_id)
    expirada = (
        criada_em is None
        or time.monotonic() - criada_em > SESSION_MAX_AGE_SECONDS
    )
    if client is None or expirada:
        CLIENTS.pop(session_id, None)
        SESSOES_CRIADAS_EM.pop(session_id, None)
        request.session.clear()
        return None
    return client


def remover_sessao(request: Request) -> None:
    session_id = request.session.get("ssw_session_id")

    if session_id:
        CLIENTS.pop(session_id, None)
        SESSOES_CRIADAS_EM.pop(session_id, None)

    request.session.clear()


def exigir_client(request: Request) -> SSWClient:
    client = obter_client(request)

    if not client:
        raise RuntimeError("Sessão SSW não encontrada. Faça login novamente.")

    return client
