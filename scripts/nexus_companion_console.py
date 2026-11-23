"""Console local de homologação; mantém a sessão humana entre comandos."""
import argparse
import importlib
import json
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright
from modules.local_agent.companion_runtime import InteractiveSSWSession
from modules.local_agent.ssw_companion import CompanionContract, configure_sanitized_log
from modules.local_agent.ssw_companion_proxy import load_companion_proxy

ORIGIN = 'https://tms.ssw.inf.br'


def official(page):
    url = urlsplit(page.url)
    return url.scheme == 'https' and url.netloc == 'tms.ssw.inf.br'


def console(session, domain, user, unit, dias, read=input, out=print):
    attempted = False
    out('Comandos: status | op455 | coletar | baixar SEQUENCIA | desbloquear | sair')
    while True:
        try:
            command = read('nexus> ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if command == 'sair':
            return
        if command not in ('status', 'op455', 'coletar', 'desbloquear') and not command.startswith('baixar '):
            out('Comandos: status | op455 | coletar | baixar SEQUENCIA | desbloquear | sair')
            continue
        try:
            session.client(usuario=user, unidade=unit)
            if command == 'status':
                out('Sessão validada. Janelas oficiais: ' + str(sum(official(p) for p in session.context.pages)))
            elif command.startswith('baixar '):
                importlib.invalidate_caches()
                importlib.reload(importlib.import_module('ssw.authenticated_report_client'))
                resume = importlib.reload(importlib.import_module('scripts.retomar_companion_op455'))
                result = resume.baixar(session, user, unit, command.split()[1:])
                out(json.dumps(result, ensure_ascii=False))
            elif command == 'desbloquear':
                if not attempted:
                    out('Nenhuma tentativa bloqueada.')
                elif read('Conferiu OP156 e não existe relatório novo ou pendente? Digite CONFERIDO: ').strip() == 'CONFERIDO':
                    attempted = False
                    out('Nova tentativa autorizada. Sessão preservada.')
                else:
                    out('Bloqueio mantido.')
            elif command == 'op455':
                field = session.page.locator('input[name="f3"][maxlength="3"]')
                field.fill('455', timeout=10000)
                field.press('Enter', timeout=10000)
                out('OP455 solicitada. Confira no navegador; nenhuma geração solicitada.')
            elif attempted:
                out('Nova coleta bloqueada: tentativa anterior pode ter solicitado relatório. Confira a OP156 manualmente. Não reinicie para repetir sem conferir.')
            else:
                importlib.invalidate_caches()
                importlib.reload(importlib.import_module('ssw.authenticated_report_client'))
                module = importlib.import_module('scripts.coletar_companion_op455')
                module = importlib.reload(module)
                attempted = True
                result = module.coletar(session, user, unit, dias)
                out(json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            # Não imprimir payloads, HTML, URLs, cookies ou mensagens externas.
            out('Falha: ' + type(exc).__name__)
            for frame in traceback.extract_tb(exc.__traceback__):
                out(f'  {Path(frame.filename).name}:{frame.lineno} em {frame.name}')
            out('Navegador não será fechado pelo console. Confira a sessão antes de continuar.')
        except KeyboardInterrupt:
            out('Comando interrompido. Não repita geração sem conferir a fila.')


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
    session = InteractiveSSWSession(contract=CompanionContract.from_file(args.contract), base_url=ORIGIN, proxy=load_companion_proxy(args.env_file), playwright_factory=sync_playwright, logger=logger)
    try:
        print('Realize login e CAPTCHA manualmente no navegador.')
        session.authenticate(usuario=user, unidade=unit, dominio=domain, timeout_seconds=900, cancel=lambda: None)
        console(session, domain, user, unit, args.dias)
    finally:
        session.close()


if __name__ == '__main__':
    main()
