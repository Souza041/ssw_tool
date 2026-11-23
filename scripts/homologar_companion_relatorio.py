"""Gera e valida um relatório OP455 sem gravar no banco."""
import argparse
from pathlib import Path
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright
from modules.local_agent.companion_runtime import InteractiveSSWSession
from modules.local_agent.ssw_companion import CompanionContract, configure_sanitized_log
from modules.local_agent.ssw_companion_proxy import load_companion_proxy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--dias', type=int, choices=range(1,31), default=1)
    args = parser.parse_args()
    values = dotenv_values(args.env_file, encoding='utf-8-sig', interpolate=False)
    domain, user, unit = (str(values.get('NEXUS_SSW_EXPECTED_'+k) or '').strip() for k in ('DOMAIN','USER','UNIT'))
    if not all((domain,user,unit)):
        parser.error('Configure os três identificadores NEXUS_SSW_EXPECTED_* no arquivo local.')
    logger = configure_sanitized_log(Path.home() / 'AppData/Local/Nexus/logs/ssw-companion.log')
    session = InteractiveSSWSession(contract=CompanionContract.from_file(args.contract), base_url='https://tms.ssw.inf.br', proxy=load_companion_proxy(args.env_file), playwright_factory=sync_playwright, logger=logger)
    try:
        print('Realize login e CAPTCHA manualmente no navegador.')
        session.authenticate(usuario=user, unidade=unit, dominio=domain, timeout_seconds=900, cancel=lambda: None)
        from scripts.coletar_companion_op455 import coletar
        result = coletar(session, user, unit, args.dias)
        import json
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print('Homologação interrompida: '+type(exc).__name__); return 2
    finally:
        session.close()

if __name__ == '__main__':
    raise SystemExit(main())
