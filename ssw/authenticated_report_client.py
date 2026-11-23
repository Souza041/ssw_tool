"""Transporte autenticado restrito a relatórios OP455/OP156; não efetua login."""
import requests
import re
from urllib.parse import urlsplit

class AuthenticatedReportClient:
    def __init__(self, session, user, unit):
        self.session = session
        self.usuario = user
        self.unidade = unit.upper()
        self.base_url = 'https://tms.ssw.inf.br'
        self.generated = False
        self.job = None
        self.form_page = None

    def close(self):
        pass

    def post(self, path, data=None, **kwargs):
        return self._request('POST', path, data)

    def get(self, path, params=None, **kwargs):
        return self._request('GET', path, params)

    def _request(self, method, path, values):
        self.session.client(usuario=self.usuario, unidade=self.unidade)
        values = dict(values or {})
        act = str(values.get('act', ''))
        if method == 'POST' and path == '/bin/ssw0230' and act == 'E1' and not self.generated:
            if self.form_page is None:
                raise RuntimeError('Formulário OP455 ausente.')
            if self.form_page.url != self.base_url + '/bin/ssw0230':
                raise RuntimeError('Página OP455 inválida.')
            form = self.form_page.locator('form[action="/bin/ssw0230"]')
            if form.count() != 1:
                raise RuntimeError('Formulário OP455 ambíguo.')
            context = form.evaluate("""form => {
                const data = new FormData(form);
                return Object.fromEntries(['f2','f3','reg_tipo'].map(k => [k,data.getAll(k)]));
            }""")
            if context['f2'] != [self.unidade]:
                raise RuntimeError('Unidade do formulário divergente.')
            for key, entries in context.items():
                if len(entries) > 1:
                    raise RuntimeError('Campo de contexto ambíguo: ' + key)
                if entries and key not in values:
                    values[key] = entries[0]
            if values.get('f2') != self.unidade:
                raise RuntimeError('Unidade do payload divergente.')
            self.generated = True
        elif method == 'POST' and path == '/bin/ssw1440' and (not act or (self.job and act == 'DOW'+self.job)):
            pass
        elif method == 'GET' and path == '/bin/ssw0424' and self.job:
            name = values.get('filename', '')
            match = re.fullmatch(r'CSVROD([0-9]+)\.sswweb', name) if isinstance(name, str) and len(name) <= 128 else None
            if (not match or not re.fullmatch(r'[0-9]+', str(self.job))
                    or int(match.group(1)) != int(self.job)
                    or values != {'act':name,'filename':name,'path':'/usr/aws/jobs/ROD/','down':'1','nw':'1'}):
                raise ValueError('Arquivo fora do contrato OP156.')
        else:
            raise ValueError('Operação não autorizada.')
        page = self.form_page if path == "/bin/ssw0230" else self.session.page
        if page is None:
            raise RuntimeError("Formulário OP455 não selecionado.")
        if urlsplit(page.url).scheme != 'https' or urlsplit(page.url).netloc != 'tms.ssw.inf.br':
            raise RuntimeError('Origem inválida.')
        args = {'form' if method == 'POST' else 'params': values}
        raw = page.context.request.fetch(self.base_url+path, method=method, timeout=60000, max_redirects=0, max_retries=0, headers={'Origin':self.base_url,'Referer':page.url}, **args)
        try:
            if raw.status != 200:
                raise RuntimeError('Resposta HTTP inesperada.')
            response = requests.Response()
            response.status_code = raw.status
            response.headers.update(raw.headers)
            response._content = raw.body()
            response.encoding = requests.utils.get_encoding_from_headers(response.headers) or 'latin1'
            return response
        finally:
            raw.dispose()

