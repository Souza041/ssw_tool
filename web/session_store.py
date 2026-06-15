import uuid

from fastapi import Request
from fastapi.responses import RedirectResponse

from ssw.client import SSWClient


CLIENTS: dict[str, SSWClient] = {}


def criar_sessao(client: SSWClient) -> str:
    session_id = str(uuid.uuid4())
    CLIENTS[session_id] = client
    return session_id


def obter_client(request: Request) -> SSWClient | None:
    session_id = request.session.get("ssw_session_id")

    if not session_id:
        return None

    return CLIENTS.get(session_id)


def remover_sessao(request: Request) -> None:
    session_id = request.session.get("ssw_session_id")

    if session_id:
        CLIENTS.pop(session_id, None)

    request.session.clear()


def exigir_client(request: Request) -> SSWClient:
    client = obter_client(request)

    if not client:
        raise RuntimeError("Sessão SSW não encontrada. Faça login novamente.")

    return client