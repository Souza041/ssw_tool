"""Abre a OP455 na sessão humana; não gera relatório nem grava no banco."""
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
    parser.add_argument('--visual', action='store_true', help='Abre a opção pela interface para conferência manual.')
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
        client = session.client(usuario=user, unidade=unit)
        if args.visual:
            field = session.page.locator('input[name="f3"][maxlength="3"]')
            field.fill('455', timeout=10000)
            field.press('Enter', timeout=10000)
            print('OP455 solicitada pela interface. Confira a janela; feche o navegador para encerrar.', flush=True)
            import time
            deadline = time.monotonic() + 30
            confirmed = False
            while session.context is not None and session.context.pages and time.monotonic() < deadline:
                pages = session.context.pages
                if any(p.url.startswith('https://tms.ssw.inf.br/') and p.locator('form[action="/bin/ssw0230"]').count() for p in pages):
                    confirmed = True
                    print('Formulário OP455 localizado. Nenhum relatório solicitado.', flush=True)
                    break
                pages[0].wait_for_timeout(500)
            if not confirmed:
                print("Formulário não confirmado automaticamente. Confira a janela.", flush=True)
            while session.context is not None and session.context.pages:
                session.context.pages[0].wait_for_timeout(500)
            return 0 if confirmed else 2
        from ssw.utils import dummy
        client.get('/bin/ssw0082', {'quadro_menu01':unit.upper(),'dummy':dummy()})
        response = client.open_option('455')
        from html.parser import HTMLParser
        class Form(HTMLParser):
            found = False
            def handle_starttag(self, tag, attrs):
                if tag == 'form' and dict(attrs).get('action') == '/bin/ssw0230':
                    self.found = True
        form = Form(); form.feed(response.text)
        if not form.found:
            print(f'OP455 não confirmada: formulário esperado ausente (HTTP {response.status_code}).'); return 2
        print('OP455: formulário confirmado na sessão autenticada. Nenhum relatório gerado.')
        return 0
    except Exception as exc:
        print('Homologação interrompida: '+type(exc).__name__); return 2
    finally:
        session.close()

if __name__ == '__main__':
    raise SystemExit(main())
