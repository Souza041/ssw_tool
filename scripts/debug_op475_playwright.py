from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import parse_qs

from playwright.sync_api import (
    BrowserContext,
    Page,
    Request,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from ssw.client import SSWClient

from ssw.utils import dummy

from datetime import datetime


# ============================================================
# DADOS FIXOS DO NOSSO TESTE
# ============================================================

UNIDADE = "APU"
CNPJ = "05117268000806"

SERIE = "1"
NF = "4212126"
VALOR = "1164,43"

# Data de emissão = hoje.
# Vamos deixar o próprio SSW/tela trabalhar com ela inicialmente.

SSW_PATH = "/bin/ssw0094"

DEBUG_DIR = Path("debug_op475")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# HELPERS DE LOG
# ============================================================

def log(titulo: str, valor=None) -> None:
    print()
    print("=" * 90)
    print(titulo)

    if valor is not None:
        if isinstance(valor, (dict, list)):
            print(
                json.dumps(
                    valor,
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )
        else:
            print(valor)

    print("=" * 90)


def resumo_post_data(post_data: str | None) -> dict:
    if not post_data:
        return {}

    try:
        parsed = parse_qs(
            post_data,
            keep_blank_values=True,
        )

        return {
            chave: valores[-1] if valores else ""
            for chave, valores in parsed.items()
        }

    except Exception:
        return {
            "_raw": post_data,
        }


# ============================================================
# NETWORK LOGGER
# ============================================================

def instalar_network_logger(page: Page, nome: str) -> None:

    contador = {
        "request": 0,
        "response": 0,
    }

    def on_request(request: Request) -> None:
        if "sistema.ssw.inf.br" not in request.url:
            return

        contador["request"] += 1

        print()
        print(
            f"[PW REQUEST #{contador['request']}:{nome}] "
            f"{request.method} {request.url}"
        )

        if request.post_data:
            dados = resumo_post_data(
                request.post_data
            )

            if dados:
                print(
                    json.dumps(
                        dados,
                        indent=2,
                        ensure_ascii=False,
                    )
                )

    def on_response(response: Response) -> None:
        if "sistema.ssw.inf.br" not in response.url:
            return

        contador["response"] += 1

        try:
            body = response.text()
        except Exception as exc:
            body = (
                "<não foi possível ler response: "
                f"{exc}>"
            )

        print()
        print(
            f"[PW RESPONSE #{contador['response']}:{nome}] "
            f"{response.status} {response.url}"
        )

        print(body[:3000])

    def on_console(msg) -> None:
        print(
            f"[PW CONSOLE:{nome}] "
            f"{msg.type}: {msg.text}"
        )

    def on_page_error(exc) -> None:
        print(
            f"[PW PAGEERROR:{nome}] "
            f"{exc}"
        )

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("console", on_console)
    page.on("pageerror", on_page_error)


# ============================================================
# COOKIES REQUESTS -> PLAYWRIGHT
# ============================================================

def copiar_cookies_para_playwright(
    client: SSWClient,
    context: BrowserContext,
) -> None:

    cookies_pw = []

    for cookie in client.session.cookies:
        item = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain
            or "sistema.ssw.inf.br",
            "path": cookie.path or "/",
        }

        cookies_pw.append(item)

    log(
        "[PW] Cookies que serão enviados ao Chromium",
        [
            {
                "name": item["name"],
                "domain": item["domain"],
                "path": item["path"],
                # propositalmente não mostramos o valor/token
            }
            for item in cookies_pw
        ],
    )

    context.add_cookies(cookies_pw)


# ============================================================
# ESTADO DA TELA
# ============================================================

def capturar_estado(page: Page, titulo: str) -> dict:

    estado = page.evaluate(
        """
        () => {
            const inputs = {};

            document
                .querySelectorAll(
                    'input, select, textarea'
                )
                .forEach((el) => {

                    const chave =
                        el.name ||
                        el.id ||
                        '(sem nome)';

                    inputs[chave] = {
                        tag: el.tagName,
                        type: el.type || '',
                        value: el.value || '',
                    };
                });

            const globals = {};

            const nomes = [
                'agora',
                'filial_sigla',
                'cod_fil_pgto',
                'seq_desp_nota',
                'seq_desp_parcela',
                'codigo',
                'flag_morto',
                'nro_lancto'
            ];

            nomes.forEach((nome) => {
                try {
                    globals[nome] =
                        window[nome] ?? null;
                } catch (_) {
                    globals[nome] = null;
                }
            });

            return {
                url: location.href,

                title: document.title,

                inputs,

                globals,

                localStorage: {
                    ...localStorage
                },

                sessionStorage: {
                    ...sessionStorage
                },

                bodyOnload:
                    document.body
                    ? document.body.getAttribute('onload')
                    : null
            };
        }
        """
    )

    log(
        f"[PW STATE] {titulo}",
        estado,
    )

    arquivo = DEBUG_DIR / (
        titulo
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        + ".json"
    )

    arquivo.write_text(
        json.dumps(
            estado,
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    return estado


# ============================================================
# LOCALIZADORES AUXILIARES
# ============================================================

def preencher(
    page: Page,
    selector: str,
    valor: str,
    nome: str,
) -> None:

    locator = page.locator(selector).first

    locator.wait_for(
        state="visible",
        timeout=10000,
    )

    print(
        f"[PW] Preenchendo {nome}: {valor}"
    )

    locator.click()

    # Preenche como usuário real:
    # não usamos evaluate() para alterar value.
    locator.fill("")

    locator.press_sequentially(
        valor,
        delay=100,
    )

    # Simula saída do campo.
    locator.press("Tab")

    page.wait_for_timeout(500)


def valor_campo(
    page: Page,
    nome: str,
) -> str:

    locator = page.locator(
        f'[name="{nome}"]'
    )

    if locator.count() == 0:
        return ""

    try:
        return locator.first.input_value()
    except Exception:
        return ""


# ============================================================
# DESCOBRIR QUAL PAGE É A OP475
# ============================================================

def pagina_ssw0094(
    context: BrowserContext,
) -> Page | None:

    candidatas = []

    for page in context.pages:
        if "ssw0094" in page.url:
            candidatas.append(page)

    if not candidatas:
        return None

    return candidatas[-1]


# ============================================================
# ABRIR OP475
# ============================================================

def abrir_op475(
    client: SSWClient,
    context: BrowserContext,
) -> Page:

    #
    # Usamos HTTP SOMENTE para entrar na opção,
    # exatamente como o Nexus já faz.
    #
    # Depois o navegador assume o fluxo.
    #

    print(
        f"[PW] Abrindo OP475 pela unidade base "
        f"{client.unidade}..."
    )

    client.open_option(
        "475",
        client.unidade,
    )

    #
    # O requests já recebeu o HTML, mas o Chromium
    # precisa abrir a página para executar JS.
    #
    url = (
        f"{client.base_url}"
        f"{SSW_PATH}"
    )

    page = context.new_page()

    instalar_network_logger(
        page,
        "OP475",
    )

    print(
        f"[PW] Navegando para {url}"
    )

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=30000,
    )

    page.wait_for_timeout(1500)

    return page


# ============================================================
# PROSSEGUIR DA TELA INICIAL
# ============================================================

def prosseguir_tela_inicial(
    page: Page,
    context: BrowserContext,
    client: SSWClient,
) -> Page:

    log(
        "[PW] INÍCIO DA TELA INICIAL OP475"
    )

    capturar_estado(
        page,
        "01_tela_inicial",
    )

    #
    # 1. UNIDADE
    #
    preencher(
        page,
        '[name="f3"]',
        UNIDADE.lower(),
        "UNIDADE",
    )

    page.wait_for_timeout(1000)

    unidade_nome = valor_campo(
        page,
        "unidade3",
    )

    print(
        "[PW] Nome retornado para unidade:",
        unidade_nome,
    )

    #
    # 2. CNPJ
    #
    preencher(
        page,
        '[name="chave_nfe"]',
        CNPJ,
        "CNPJ",
    )

    page.wait_for_timeout(1500)

    evento = valor_campo(
        page,
        "f5",
    )

    descricao_evento = valor_campo(
        page,
        "evento",
    )

    print(
        f"[PW] Evento após CNPJ: "
        f"codigo={evento!r} "
        f"descricao={descricao_evento!r}"
    )

    #
    # Fallback 5501.
    #
    if not evento.strip():

        print(
            "[PW] Evento não veio automaticamente. "
            "Aplicando fallback 5501."
        )

        preencher(
            page,
            '[name="f5"]',
            "5501",
            "EVENTO",
        )

        page.wait_for_timeout(1000)

        evento = valor_campo(
            page,
            "f5",
        )

        descricao_evento = valor_campo(
            page,
            "evento",
        )

        print(
            f"[PW] Evento fallback: "
            f"{evento!r} "
            f"{descricao_evento!r}"
        )

    capturar_estado(
        page,
        "02_antes_prosseguir",
    )

    #
    # PROSSEGUIR
    #
    # Pelo SSW legado o botão pode ser input/button,
    # onclick JS ou até elemento customizado.
    #
    botao = page.locator('a[id="6"]')

    botao.wait_for(
        state="visible",
        timeout=10000,
    )

    print(
        "[PW] Botão Prosseguir encontrado | "
        "id=6 | onclick=ajaxEnvia('INC', 1)"
    )

    paginas_antes = set(context.pages)

    botao.click()

    #
    # SSW pode navegar na mesma aba OU criar outra.
    #
    page.wait_for_timeout(2000)

    paginas_depois = set(context.pages)
    novas_paginas = list(
        paginas_depois - paginas_antes
    )

    if novas_paginas:
        nova = novas_paginas[-1]

        instalar_network_logger(
            nova,
            "OP475-NOVA-ABA",
        )

        try:
            nova.wait_for_load_state(
                "domcontentloaded",
                timeout=15000,
            )
        except PlaywrightTimeoutError:
            pass

        page = nova

        print(
            "[PW] SSW abriu nova aba:",
            page.url,
        )

    else:
        print(
            "[PW] SSW continuou na mesma aba:",
            page.url,
        )

    page.wait_for_timeout(2000)

    capturar_estado(
        page,
        "03_depois_prosseguir",
    )

    debug_funcoes = page.evaluate(
        """
        () => {
            const nomes = [
                'criaTemp',
                'gonav',
                'setChave',
                'dadosfor',
                'adiciona_evento'
            ];

            const resultado = {};

            for (const nome of nomes) {
                try {
                    resultado[nome] =
                        typeof window[nome] === 'function'
                        ? window[nome].toString()
                        : String(window[nome]);
                } catch (e) {
                    resultado[nome] =
                        '<erro: ' + e.message + '>';
                }
            }

            return resultado;
        }
        """
    )

    log(
        "[PW DEBUG] FUNÇÕES JS IMPORTANTES",
        debug_funcoes,
    )

    cookies_browser = context.cookies()

    print()
    print("=" * 90)
    print("[PW DEBUG] COOKIES DO CHROMIUM APÓS ACT=INC")

    for cookie in cookies_browser:
        print(
            f"{cookie['name']}="
            f"{cookie['value']} | "
            f"domain={cookie['domain']} | "
            f"path={cookie['path']}"
        )

    print("=" * 90)

    estado_browser = page.evaluate(
        """
        () => ({
            document_cookie: document.cookie,
            window_name: window.name,
            has_opener: !!window.opener,
            opener_url: (() => {
                try {
                    return window.opener
                        ? window.opener.location.href
                        : null;
                } catch (_) {
                    return '<cross-origin>';
                }
            })()
        })
        """
    )

    print(
        "[PW DEBUG] ESTADO EXTRA DO BROWSER =",
        estado_browser,
    )

    return page

def copiar_cookies_do_playwright_para_requests(
    context: BrowserContext,
    client: SSWClient,
) -> None:
    cookies = context.cookies()

    for cookie in cookies:
        client.session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie["domain"],
            path=cookie["path"],
        )

    print(
        "[PW -> REQUESTS] Cookies sincronizados:",
        client.session.cookies.get_dict(),
    )

def diagnosticar_eventos_nota(page: Page) -> None:
    dados = page.evaluate(
        """
        () => {
            const nomes = [
                'f4',
                'f5',
                'f7',
                'f14',
                'f15',
                'f16',
                'f17'
            ];

            const resultado = {};

            nomes.forEach((nome) => {
                const el = document.querySelector(
                    `[name="${nome}"]`
                );

                if (!el) {
                    resultado[nome] = null;
                    return;
                }

                resultado[nome] = {
                    id: el.id || '',
                    name: el.name || '',
                    value: el.value || '',
                    onblur: el.getAttribute('onblur'),
                    onchange: el.getAttribute('onchange'),
                    onkeyup: el.getAttribute('onkeyup'),
                    onkeydown: el.getAttribute('onkeydown'),
                    onkeypress: el.getAttribute('onkeypress'),
                    onclick: el.getAttribute('onclick'),
                    onfocus: el.getAttribute('onfocus')
                };
            });

            return resultado;
        }
        """
    )

    log(
        "[PW DEBUG] EVENTOS DOS CAMPOS DA NOTA",
        dados,
    )


# ============================================================
# PREENCHER NOTA
# ============================================================

def preencher_nota(page: Page) -> None:
    from datetime import datetime

    log(
        "[PW] INICIANDO PREENCHIMENTO COMPLETO DA OP475"
    )

    hoje = datetime.now().strftime("%d%m%y")

    capturar_estado(
        page,
        "04_antes_nota",
    )

    # ========================================================
    # DADOS FISCAIS
    # ========================================================

    preencher(
        page,
        '[name="f4"]',
        SERIE,
        "SÉRIE",
    )

    preencher(
        page,
        '[name="f5"]',
        NF,
        "NOTA FISCAL",
    )

    preencher(
        page,
        '[name="f15"]',
        VALOR,
        "VALOR DOCUMENTO",
    )

    preencher(
        page,
        '[name="f16"]',
        hoje,
        "DATA EMISSÃO",
    )

    page.wait_for_timeout(800)

    data_entrada = valor_campo(
        page,
        "f17",
    )

    print(
        "[PW] Data entrada após emissão:",
        repr(data_entrada),
    )

    capturar_estado(
        page,
        "05_dados_fiscais",
    )

    # ========================================================
    # DADOS DO PAGAMENTO
    # ========================================================

    # Valores fixos apenas para ESTE debug.
    # Depois ligamos na planilha.
    VENCIMENTO_DEBUG = "310826"
    HISTORICO_DEBUG = "3737/2026/AVARIA - SUCATA"

    preencher(
        page,
        '[name="data_vcto"]',
        VENCIMENTO_DEBUG,
        "DATA VENCIMENTO",
    )

    page.wait_for_timeout(800)

    data_pagamento = valor_campo(
        page,
        "data_pgto",
    )

    competencia = valor_campo(
        page,
        "mes_competencia",
    )

    print(
        "[PW] Após vencimento | "
        f"data_pgto={data_pagamento!r} | "
        f"competencia={competencia!r}"
    )

    # Se o SSW não preencher automaticamente a data de pagamento,
    # fazemos o mesmo valor do vencimento neste debug.
    if not data_pagamento.strip():
        preencher(
            page,
            '[name="data_pgto"]',
            VENCIMENTO_DEBUG,
            "DATA PAGAMENTO",
        )

    # Valor da parcela
    preencher(
        page,
        '[name="vlr_parcela"]',
        VALOR,
        "VALOR PARCELA",
    )

    # Histórico
    preencher(
        page,
        '[name="historico"]',
        HISTORICO_DEBUG,
        "HISTÓRICO",
    )

    page.wait_for_timeout(1000)

    capturar_estado(
        page,
        "06_antes_gravar",
    )

    # ========================================================
    # LOCALIZAR BOTÃO "GRAVAR LANÇAMENTO"
    # ========================================================

    print()
    print(
        "[PW] Procurando botão Gravar lançamento..."
    )

    candidatos = page.locator(
        'a, input[type="button"], input[type="submit"], button'
    )

    encontrado = None

    for i in range(candidatos.count()):
        el = candidatos.nth(i)

        try:
            texto = (el.inner_text() or "").strip()
        except Exception:
            texto = ""

        try:
            value = el.get_attribute("value") or ""
        except Exception:
            value = ""

        try:
            onclick = el.get_attribute("onclick") or ""
        except Exception:
            onclick = ""

        combinado = (
            f"{texto} {value} {onclick}"
        ).lower()

        if (
            "gravar" in combinado
            or "inc2" in combinado
        ):
            print(
                "[PW] Candidato encontrado | "
                f"texto={texto!r} | "
                f"value={value!r} | "
                f"onclick={onclick!r}"
            )

            encontrado = el
            break

    if encontrado is None:
        raise RuntimeError(
            "Não encontrei o botão/link de Gravar lançamento."
        )

    # ========================================================
    # CLIQUE CONTROLADO
    # ========================================================

    print()
    print(
        "[PW] Clicando em Gravar lançamento."
    )
    print(
        "[PW] Esperamos DUPLICIDADE porque estamos usando série 1."
    )

    encontrado.click()

    page.wait_for_timeout(3000)

    capturar_estado(
        page,
        "07_depois_gravar",
    )

    # ========================================================
    # CAPTURA DA MENSAGEM / CARD
    # ========================================================

    texto_pagina = page.locator("body").inner_text()

    print()
    print("=" * 90)
    print("[PW] TEXTO DA PÁGINA APÓS GRAVAR")
    print(texto_pagina[:5000])
    print("=" * 90)

    texto_normalizado = (
        texto_pagina
        .lower()
        .replace("á", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("à", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )

    duplicado = (
        "ja existe um lancamento" in texto_normalizado
        and
        "com mesmo documento" in texto_normalizado
    )

    if duplicado:
        print()
        print(
            "############################################################"
        )
        print(
            "# DUPLICIDADE DETECTADA COM SUCESSO                        #"
        )
        print(
            "# O DEBUG VAI PARAR AQUI.                                 #"
        )
        print(
            "############################################################"
        )
        return

    print()
    print(
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    )
    print(
        "! ATENÇÃO: NÃO ENCONTREI O CARD DE DUPLICIDADE            !"
    )
    print(
        "! NÃO CONTINUE NENHUMA AÇÃO MANUAL NESTE DEBUG            !"
    )
    print(
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    )

def testar_consistencia_via_requests(
    page: Page,
    client: SSWClient,
) -> None:
    contexto = page.evaluate(
        """
        () => {
            const valor = (name) => {
                const el = document.querySelector(
                    `[name="${name}"]`
                );

                return el ? el.value : '';
            };

            return {
                codigo: valor('codigo'),
                cod_fil_pgto: valor('cod_fil_pgto'),
                agora: valor('agora'),
                filial_sigla: valor('filial_sigla'),
                flag_morto: valor('flag_morto')
            };
        }
        """
    )

    payload = {
        "consiste_nota": "S",
        "f4": "1",
        "f5": "4212126",
        "f7": "97",
        "f12": "",
        "f14": "1949",
        "f15": "1.164,43",
        "f16": datetime.now().strftime("%d%m%y"),
        "f17": datetime.now().strftime("%d%m%y"),
        "f18": "",
        "f19": "",
        "f20": "",
        "f21": "",
        "f22": "",
        "f23": "",
        "f24": "",
        "f25": "",
        "f26": "",
        "f27": "undefined",
        "f28": "undefined",
        "f29": "undefined",
        "cod_cfop_saida": "",
        "cfop_entrada": "1949",
        "cgc_forn": "05117268000806",
        "itens": "N",
        "chave_nfe_display": "",
        "produtos": "",
        "codigo": contexto["codigo"],
        "cod_fil_pgto": contexto["cod_fil_pgto"],
        "seq_desp_nota": "",
        "nro_lancto": "",
        "agora": contexto["agora"],
        "filial_sigla": contexto["filial_sigla"],
        "base_calc_inss": "",
        "flag_morto": contexto["flag_morto"],
        "orig_dest": "",
        "p_act": "undefined",
        "dummy": dummy(),
    }

    client.session.headers["Referer"] = (
        f"{client.base_url}/bin/ssw0094"
    )

    response = client.post(
        "/bin/ssw0094",
        payload,
        retries=1,
    )

    print()
    print("=" * 90)
    print("[PW -> REQUESTS] TESTE CONSISTE_NOTA")
    print(response.text)
    print("=" * 90)

def snapshot_cookies_requests(
    client: SSWClient,
    titulo: str,
) -> dict[str, str]:
    cookies = client.session.cookies.get_dict()

    print()
    print("=" * 90)
    print(f"[COOKIE DEBUG REQUESTS] {titulo}")

    for nome, valor in cookies.items():
        print(f"{nome}={valor}")

    print("=" * 90)

    return dict(cookies)


def snapshot_cookies_playwright(
    context: BrowserContext,
    titulo: str,
) -> dict[str, str]:
    cookies = {
        cookie["name"]: cookie["value"]
        for cookie in context.cookies()
    }

    print()
    print("=" * 90)
    print(f"[COOKIE DEBUG PLAYWRIGHT] {titulo}")

    for nome, valor in cookies.items():
        print(f"{nome}={valor}")

    print("=" * 90)

    return cookies


def comparar_cookies(
    antes: dict[str, str],
    depois: dict[str, str],
    titulo: str,
) -> None:
    print()
    print("=" * 90)
    print(f"[COOKIE DIFF] {titulo}")

    nomes = sorted(
        set(antes) | set(depois)
    )

    mudou = False

    for nome in nomes:
        valor_antes = antes.get(nome)
        valor_depois = depois.get(nome)

        if valor_antes != valor_depois:
            mudou = True

            print(
                f"{nome}: "
                f"{valor_antes!r} "
                f"→ "
                f"{valor_depois!r}"
            )

    if not mudou:
        print("Nenhum cookie mudou.")

    print("=" * 90)


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    log(
        "[DEBUG OP475] INICIANDO LABORATÓRIO PLAYWRIGHT"
    )

    #
    # Reutilizamos exatamente a autenticação atual do Nexus.
    #
    client = SSWClient()

    print("[PW] Efetuando login SSW...")

    client.login()
    client.open_menu()

    cookies_requests_inicio = snapshot_cookies_requests(
        client,
        "APÓS LOGIN",
    )

    print(
        "[PW] Login concluído. "
        f"Unidade base={client.unidade}"
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=False,

            # Deixa propositalmente lento para conseguirmos
            # enxergar o SSW trabalhando.
            slow_mo=150,
        )

        context = browser.new_context(
            viewport={
                "width": 1440,
                "height": 1000,
            },

            ignore_https_errors=True,
        )

        #
        # Copia a sessão autenticada do requests.
        #
        copiar_cookies_para_playwright(
            client,
            context,
        )

        # ========================================================
        # SNAPSHOT DO CHROMIUM ANTES DA OP475
        # ========================================================

        cookies_browser_inicio = snapshot_cookies_playwright(
            context,
            "ANTES DE ABRIR OP475",
        )

        comparar_cookies(
            cookies_requests_inicio,
            cookies_browser_inicio,
            "REQUESTS LOGIN x CHROMIUM INICIAL",
        )

        #
        # Toda nova aba será anunciada.
        #
        def nova_aba(page: Page) -> None:
            print(
                "[PW] NOVA ABA DETECTADA:",
                page.url,
            )

        context.on(
            "page",
            nova_aba,
        )

        page = abrir_op475(
            client,
            context,
        )

        page = prosseguir_tela_inicial(
            page,
            context,
            client,
        )

        cookies_browser_inc = snapshot_cookies_playwright(
            context,
            "APÓS ACT=INC",
        )

        comparar_cookies(
            cookies_browser_inicio,
            cookies_browser_inc,
            "CHROMIUM INICIAL x APÓS ACT=INC",
        )

        cookies_requests_antes_sync = snapshot_cookies_requests(
            client,
            "ANTES DA SINCRONIZAÇÃO",
        )

        comparar_cookies(
            cookies_requests_antes_sync,
            cookies_browser_inc,
            "REQUESTS x CHROMIUM APÓS ACT=INC",
        )

        # Só agora sincroniza.
        copiar_cookies_do_playwright_para_requests(
            context,
            client,
        )

        testar_consistencia_via_requests(
            page,
            client,
        )

        print()
        input(
            "Teste finalizado. Pressione ENTER para encerrar..."
        )

        browser.close()
        return


if __name__ == "__main__":
    main()