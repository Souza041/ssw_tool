import uuid
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse

from ssw.client import SSWClient


@dataclass(repr=False)
class RemoteAuthenticatedClient:
    """Referencia opaca; a sessao Chromium permanece exclusivamente no Windows."""

    dominio: str
    usuario: str
    unidade: str
    session_binding: str = field(repr=False)
    agent_id: str
    remote_authenticated: bool = field(default=True, init=False)

    def identity_payload(self) -> dict[str, str]:
        return {
            "dominio": self.dominio,
            "usuario": self.usuario,
            "unidade": self.unidade,
            "session_binding": self.session_binding,
        }

    def session_status(self) -> dict[str, str]:
        return {
            "state": "authenticated",
            "message": "Sessao SSW autenticada no agente Windows.",
            "operation": "",
            "observed_unit": self.unidade,
        }

    def close(self) -> None:
        try:
            from modules.local_agent.delegation import enqueue_ssw_task
            from modules.local_agent.ssw_tasks import TASK_SESSION_CLOSE

            enqueue_ssw_task(
                TASK_SESSION_CLOSE,
                self.identity_payload(),
                idempotency_key=None,
                concurrency_key=f"ssw.session:{self.session_binding}",
                priority=10,
                target_agent_id=self.agent_id,
            )
        except Exception:
            # Logout local nunca deve reter a sessao da interface por falha de rede/fila.
            pass


CLIENTS: dict[str, Any] = {}
SESSOES_CRIADAS_EM: dict[str, float] = {}
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8


def _fechar_client(client: Any | None) -> None:
    if client is None:
        return
    close = getattr(client, "close", None)
    if callable(close):
        close()


def criar_sessao(client: Any) -> str:
    session_id = str(uuid.uuid4())
    CLIENTS[session_id] = client
    SESSOES_CRIADAS_EM[session_id] = time.monotonic()
    return session_id


def obter_client(request: Request) -> Any | None:
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
        _fechar_client(CLIENTS.pop(session_id, None))
        SESSOES_CRIADAS_EM.pop(session_id, None)
        request.session.clear()
        return None
    return client


def remover_sessao(request: Request) -> None:
    session_id = request.session.get("ssw_session_id")

    if session_id:
        _fechar_client(CLIENTS.pop(session_id, None))
        SESSOES_CRIADAS_EM.pop(session_id, None)

    request.session.clear()


def exigir_client(request: Request) -> Any:
    client = obter_client(request)

    if not client:
        raise RuntimeError("Sessão SSW não encontrada. Faça login novamente.")

    return client
