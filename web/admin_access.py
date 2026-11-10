"""Autorização local para visualizações administrativas. Nunca confiar em flags de cookie."""
import os
import unicodedata
from fastapi import Request
from web.session_store import obter_client

# Nomes de usuário SSW não são segredos; CPFs e senhas não devem ser usados na allowlist.
# Em produção configure NEXUS_ADMIN_USERS=dhionata,outro_usuario no ambiente do serviço.
DEFAULT_ADMINS = "dhionata"

def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().casefold()

def is_admin(request: Request) -> bool:
    client = obter_client(request)  # consulta a sessão ativa, não o cookie de role
    if client is None:
        return False
    allowlist = {_normalize(u) for u in os.getenv("NEXUS_ADMIN_USERS", DEFAULT_ADMINS).split(",") if u.strip()}
    return _normalize(str(getattr(client, "usuario", ""))) in allowlist
