"""Informações públicas dos perfis. Nunca enviar Settings/senhas ao template."""
from .config import Settings, PROFILES
from .presentation import ACTIVE



def account_cards(repo):
    cards=[]
    for code in PROFILES:
        try:
            settings=Settings.from_env(code)
            configured=bool(settings.user and settings.password)
            latest=repo.latest_run(settings.user,settings.carrier) if configured else None
            cards.append(dict(code=code, carrier=settings.carrier, configured=configured,
                              send_enabled=settings.send_enabled, latest=latest))
        except (ValueError, TypeError):
            cards.append(dict(code=code,carrier='ROBR_'+code,configured=False,send_enabled=False,latest=None))
    return cards


def filtered_history(repo, operation):
    if not operation:
        return repo.history()
    settings=Settings.from_env(operation)
    return repo.history(settings.user,settings.carrier) if settings.user else []


def live_active(view, cards):
    return bool(view and view['active']) or any(c['latest'] and c['latest']['status'] in ACTIVE for c in cards)
