"""Cliente de abertura OP455 restrito ao contexto Playwright autenticado."""
from urllib.parse import urlsplit
import re
import requests
from ssw.utils import dummy


class BrowserSSWClient:
    def __init__(self, page, *, base_url, usuario, unidade, cancel=None, validate=None):
        if base_url.rstrip('/') != 'https://tms.ssw.inf.br':
            raise ValueError('Origem SSW não autorizada.')
        self.page, self.base_url = page, base_url.rstrip('/')
        self.usuario, self.unidade = usuario, unidade.upper()
        self.cancel = cancel or (lambda: None)
        self.validate = validate or (lambda: None)

    def close(self):
        # O contexto pertence ao runtime, não ao consumidor do relatório.
        pass

    def _request(self, method, path, values):
        self.cancel()
        self.validate()
        values = dict(values or {})
        act = str(values.get('act', ''))
        allowed = (
            (method == 'GET' and path == '/bin/ssw0082' and values.get('quadro_menu01') == self.unidade)
            or (method == 'POST' and path == '/bin/menu01' and act == 'TRO' and values.get('f2') == self.unidade and str(values.get('f3')) == '455')
            or (method == 'POST' and path == '/bin/ssw0230' and str(values.get('sequencia')) == '455' and not act)
        )
        if not allowed:
            raise ValueError('Operação não autorizada no cliente de abertura OP455.')
        if urlsplit(self.page.url).netloc != 'tms.ssw.inf.br' or urlsplit(self.page.url).scheme != 'https':
            raise RuntimeError('Origem da sessão inválida.')
        kwargs = {'timeout': 60000, 'max_redirects': 0, 'max_retries': 0, 'headers': {'Referer': self.page.url, 'Origin': self.base_url}}
        kwargs['params' if method == 'GET' else 'form'] = values
        raw = self.page.context.request.fetch(self.base_url + path, method=method, **kwargs)
        try:
            if not 200 <= raw.status < 300:
                raise RuntimeError(f'SSW recusou a operação (HTTP {raw.status}). Nenhuma repetição automática.')
            response = requests.Response()
            response.status_code = raw.status
            response.url = raw.url
            response.headers.update(raw.headers)
            response._content = raw.body()
            response.encoding = requests.utils.get_encoding_from_headers(response.headers)
            if not response.encoding:
                response.encoding = 'latin1'
            self.cancel()
            return response
        finally:
            raw.dispose()

    def get(self, path, params=None, retries=1, wait_seconds=0):
        return self._request('GET', path, params)

    def post(self, path, data=None, retries=1, wait_seconds=0):
        return self._request('POST', path, data)

    def open_option(self, option, unidade=None):
        if str(option) != '455' or (unidade or self.unidade).upper() != self.unidade:
            raise ValueError('Opção ou unidade não autorizada.')
        self.post('/bin/menu01', {'act':'TRO','f2':self.unidade,'f3':'455','dummy':dummy()})
        return self.post('/bin/ssw0230', {'sequencia':'455','dummy':dummy()})
