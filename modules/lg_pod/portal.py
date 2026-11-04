"""Cliente JDA usando a sessão do Chromium e controles observados nos HARs.
Não armazena cookies, senha, HTML ou imagens em disco.
"""
import re
from urllib.parse import urlencode, urlsplit, parse_qs
from .config import today
from .parser import parse_html, choose_invoice, mapping_success, approval_success, eligibility


class PortalError(RuntimeError):
    pass


def encode_password(password, script):
    """Mesmo encipher de i2tm.js; parâmetros lidos do JS da sessão atual."""
    def constant(name):
        match = re.search(r'\b' + name + r'\s*=\s*[\'"]([^\'"]+)[\'"]', script)
        if not match:
            raise PortalError('Parâmetros de autenticação não reconhecidos no portal.')
        return match.group(1)
    modulus = int(constant('basicEncryptionPublicKey'))
    exponent = int(constant('basicEncryptionGCD'))
    separator = constant('basicEncryptionSeparator')
    if not (1 < modulus < 2**26 and 0 < exponent < 100000):
        raise PortalError('Parâmetros de autenticação fora do formato observado.')
    raw = password.encode('utf-16-le')
    units = [int.from_bytes(raw[i:i+2], 'little') for i in range(0, len(raw), 2)]
    return ''.join(str(pow(unit, exponent, modulus)) + separator for unit in units), '*' * len(units)


class Portal:
    def __init__(self, settings):
        self.settings = settings
        self.manager = self.browser = self.context = None
        self.page = None
        self.start = self.end = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        try:
            self.manager = sync_playwright().start()
            proxy = None
            if self.settings.proxy_server:
                proxy = dict(server=self.settings.proxy_server)
                if self.settings.proxy_user:
                    proxy.update(username=self.settings.proxy_user, password=self.settings.proxy_password)
            self.browser = self.manager.chromium.launch(headless=True, proxy=proxy)
            self.context = self.browser.new_context(locale='pt-BR', timezone_id='America/Sao_Paulo')
            self.page = self.context.new_page()
            self.page.set_default_timeout(self.settings.timeout_ms)
            self.page.set_default_navigation_timeout(self.settings.timeout_ms)
            # Diálogos não capturados exigem análise: nunca confirmar ação desconhecida.
            self.page.on('dialog', lambda dialog: dialog.dismiss())
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise PortalError('Não foi possível iniciar o Chromium. Verifique a instalação do Playwright.') from None

    def __exit__(self, *args):
        for obj in [self.context, self.browser]:
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        if self.manager:
            self.manager.stop()

    def login(self):
        base = self.settings.base_url
        self.context.request.get(base + '/tm/admin/LoginView.jsp', timeout=self.settings.timeout_ms)
        js = self.context.request.get(base + '/tm/framework/includes/i2tmgenerated.js', timeout=self.settings.timeout_ms)
        if not js.ok:
            raise PortalError('Não foi possível obter os parâmetros de login LG.')
        encrypted, disguised = encode_password(self.settings.password, js.text())
        response = self.context.request.post(base + '/tm/admin/LoginViewController.jsp', form={
            'ControllerAction': 'Login', 'newPassword': '', 'loginPassword': encrypted,
            'loginUser': self.settings.user, 'dspLoginPassword': disguised,
        }, headers={'Origin': base, 'Referer': base + '/tm/admin/LoginView.jsp'},
            timeout=self.settings.timeout_ms)
        if not response.ok or urlsplit(response.url).path != '/tm/entry/main.gsm':
            raise PortalError('O portal não confirmou o login. Verifique usuário e senha LG.')
        self.page.goto(base + '/base/start.x2ps?' + urlencode({
            'SERVICE_NAME': 'tms_load', 'START_WORKFLOW': 'tiWPodMap', 'userID': self.settings.user,
        }), wait_until='networkidle')
        self.page.locator('#SearchLeft_button').wait_for(state='visible')

    def ready(self):
        self.page.wait_for_load_state('networkidle')
        self.page.wait_for_function("""() => {
            if (!window.PGL || !PGL.JOL) return false;
            const element = document.getElementById('MPpb');
            const table = element && PGL.JOL.getTypeFromElement(element, 'ui:table');
            return !!(table && table.dataTable && !table.requesting);
        }""")
        if self.page.locator('#SearchLeft_button').count() != 1:
            raise PortalError('Tela de mapeamento indisponível ou sessão expirada.')

    def action(self, action):
        def expected(response):
            url = urlsplit(response.url)
            return (url.path == '/base/view.x2ps' and response.request.method == 'POST'
                    and parse_qs(url.query).get('action') == [action])
        with self.page.expect_response(expected) as pending:
            self.page.locator('#' + action).click()
        response = pending.value
        if not response.ok:
            raise PortalError('O portal retornou falha HTTP na ação ' + action + '.')
        content = response.text()
        self.ready()
        return parse_html(content)

    def search(self, start, end):
        self.start, self.end = start, end
        self.page.locator('#FROM_CREATION_DATE').fill(start.strftime('%d/%m/%Y'))
        self.page.locator('#TO_CREATION_DATE').fill(end.strftime('%d/%m/%Y'))
        self.page.locator('#POD_CARRIER_CODE').select_option(self.settings.carrier)
        self.action('SearchLeft_button')
        return self.load_pods()

    def load_pods(self):
        for _ in range(self.settings.max_records // 50 + 3):
            doc = parse_html(self.page.content())
            try:
                total = int(doc.fields['RECORD_COUNT'])
            except (KeyError, ValueError):
                raise PortalError('A contagem dos comprovantes não pôde ser lida.') from None
            if total < 0 or total > self.settings.max_records:
                raise PortalError('Contagem de comprovantes inválida ou acima de LG_POD_MAX_REGISTROS.')
            unique = {row.pod_id: row for row in doc.pods}
            if len(unique) != len(doc.pods) or any(not key.isdigit() for key in unique):
                raise PortalError('A lista contém IDs de comprovantes ausentes ou duplicados.')
            if len(unique) == total:
                return list(unique.values())
            if len(unique) > total:
                raise PortalError('Contagem de comprovantes inconsistente.')
            before = len(unique)
            # O JDA carrega páginas adicionais ao rolar. Método presente no JS capturado.
            with self.page.expect_response(lambda r: '/base/view.x2ps' in r.url
                    and parse_qs(urlsplit(r.url).query).get('LAF_FILTER_ID') == ['MPpb']):
                self.page.evaluate("""() => {
                    const t = PGL.JOL.getTypeFromElement(document.getElementById('MPpb'), 'ui:table');
                    if (!t || t.requesting) throw new Error('Tabela indisponível');
                    t.getNextRows();
                }""")
            self.page.wait_for_function("""() => {
                const t = PGL.JOL.getTypeFromElement(document.getElementById('MPpb'), 'ui:table');
                return t && !t.requesting;
            }""")
            self.ready()
            if len(parse_html(self.page.content()).pods) <= before:
                raise PortalError('A paginação não avançou; nenhum item foi enviado.')
        raise PortalError('Limite de paginação atingido.')

    def refresh_pod(self, pod_id):
        rows = self.search(self.start, self.end)
        return next((row for row in rows if row.pod_id == pod_id), None)

    def find_invoice(self, nf):
        self.page.locator('#FROM_NF_ISSUE_DATE').fill(self.settings.invoice_start.strftime('%d/%m/%Y'))
        self.page.locator('#TO_NF_ISSUE_DATE').fill(today().strftime('%d/%m/%Y'))
        for selector in ['#GERP_ORDER_NO', '#NF_SERIE', '#LD_LEG_ID0', '#PROTOCOL_NUMBER']:
            self.page.locator(selector).fill('')
        self.page.locator('#NF_NO').fill(nf)
        self.action('SearchRight_button')
        doc = parse_html(self.page.content())
        return choose_invoice(doc.invoices, nf, self.settings.carrier,
                              int(doc.fields.get('MPTable_RECORD_COUNT', '-1')))

    def select_only(self, name, value):
        boxes = self.page.locator('input[name="' + name + '"]')
        for index in range(boxes.count()):
            box = boxes.nth(index)
            desired = box.get_attribute('value') == value
            if box.is_checked() != desired:
                box.set_checked(desired)
        selected = self.page.locator('input[name="' + name + '"]:checked')
        if selected.count() != 1 or selected.get_attribute('value') != value:
            raise PortalError('Não foi possível selecionar exclusivamente o registro esperado.')

    def map(self, pod, invoice):
        # A busca da NF redesenha também a lista esquerda; carregar novamente
        # antes de selecionar um POD que pode estar na terceira página ou além.
        rows = self.load_pods()
        current = next((r for r in rows if r.pod_id == pod.pod_id), None)
        if (not current or current.filename != pod.filename
                or eligibility(current, self.settings.carrier)[0] != invoice.nf):
            raise PortalError('O comprovante mudou desde a busca da NF; mapeamento interrompido.')
        doc = parse_html(self.page.content())
        selected = choose_invoice(doc.invoices, invoice.nf, self.settings.carrier,
                                  int(doc.fields.get('MPTable_RECORD_COUNT', '-1')))
        if selected.key != invoice.key:
            raise PortalError('A identidade da nota mudou desde a consulta.')
        self.select_only('SELECTED_KEY_LEFT', pod.pod_id)
        self.select_only('SELECTED_KEY_CD', invoice.key)
        doc = self.action('Map')
        if not mapping_success(doc.message, invoice.nf, invoice.values['LD_NF_SERIE']):
            raise PortalError('O portal não confirmou o mapeamento da NF e série esperadas.')
        return doc.message

    def approve(self, pod):
        doc = parse_html(self.page.content())
        current = next((r for r in doc.pods if r.pod_id == pod.pod_id), None)
        if (not current or current.filename != pod.filename or current.status != 'MAPPED'
                or current.values.get('POD_STATUS') != '2'
                or current.values.get('P_CARR_CD') != self.settings.carrier
                or current.values.get('NOTAS_MAPEADAS') != pod.values.get('NOTAS_MAPEADAS')):
            raise PortalError('O vínculo mapeado mudou antes do envio para aprovação.')
        self.select_only('SELECTED_KEY_LEFT', pod.pod_id)
        doc = self.action('btnApprove')
        if not approval_success(doc.message):
            raise PortalError('O portal não confirmou o envio para aprovação.')
        return doc.message
