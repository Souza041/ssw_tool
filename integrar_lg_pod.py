"""Integração idempotente no projeto enviado, preservando conteúdo existente.
Executar na raiz: python integrar_lg_pod.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def replacements():
    main = ROOT / 'web/main.py'
    base = ROOT / 'web/templates/base.html'
    changes = []
    text = main.read_text(encoding='utf-8')
    anchor = '    from modules.documentos.web import router as documentos_router'
    include = '    app.include_router(documentos_router)'
    if 'from modules.lg_pod.routes import router as lg_pod_router' not in text:
        if text.count(anchor) != 1:
            raise RuntimeError('Âncora de import não encontrada de forma única em web/main.py.')
        text = text.replace(anchor, anchor + '\n    from modules.lg_pod.routes import router as lg_pod_router')
    if 'app.include_router(lg_pod_router)' not in text:
        if text.count(include) != 1:
            raise RuntimeError('Âncora de router não encontrada de forma única em web/main.py.')
        text = text.replace(include, include + '\n    app.include_router(lg_pod_router)')
    compile(text, str(main), 'exec')
    changes.append((main, text))
    text = base.read_text(encoding='utf-8')
    anchor = '            <a href="/documentos">Documentos GCE</a>'
    if 'href="/lg-pod"' not in text:
        if text.count(anchor) != 1:
            raise RuntimeError('Âncora do menu não encontrada de forma única em base.html.')
        text = text.replace(anchor, anchor + '\n            <a href="/lg-pod">LG - Mapeamento POD</a>')
    changes.append((base, text))
    return changes


def main():
    changes = replacements()  # Validar os dois arquivos antes de escrever.
    for path, text in changes:
        if path.read_text(encoding='utf-8') == text:
            continue
        backup = path.with_name(path.name + '.antes_lg_pod')
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        path.write_text(text, encoding='utf-8')
    print('LG POD integrado ao menu e às rotas. Reinicie a aplicação após instalar as tabelas.')


if __name__ == '__main__':
    main()
