"""Cliente HTTP JDA: requests.Session + XML reconstruído da resposta atual.
Sem Chromium, Playwright, JavaScript ou reaproveitamento de cookies dos HARs.
"""
import re
from urllib.parse import urlencode, urlsplit, urljoin, quote, urlunsplit, parse_qs
import logging
import uuid
import time
import math
from dataclasses import dataclass
import requests
from .jda_form import JDAForm
from .config import today
from .parser import parse_html, choose_invoice, mapping_success, approval_success, eligibility, invoice_from_filename


class PortalError(RuntimeError):
    pass


class PaginationChanged(PortalError):
    """Resposta de leitura válida, mas sem uma paginação consistente."""


class TransientReadError(PortalError):
    def __init__(self, reason, retry_after=None):
        super().__init__(reason)
        self.retry_after = retry_after


def transport_reason(exc):
    """Classificar localmente sem publicar URLs, cabeçalhos ou credenciais."""
    detail = str(exc).lower()
    if '407' in detail or 'proxy authentication required' in detail:
        return 'autenticação do proxy recusada (407)', False
    if '403' in detail and isinstance(exc, requests.exceptions.ProxyError):
        return 'acesso recusado pelo proxy (403)', False
    if 'certificate_verify_failed' in detail or isinstance(exc, requests.exceptions.SSLError):
        return 'certificado HTTPS inválido', False
    if isinstance(exc, requests.exceptions.Timeout):
        return 'tempo limite de comunicação', True
    if 'name resolution' in detail or 'getaddrinfo' in detail:
        return 'falha de resolução de nome', True
    if isinstance(exc, requests.exceptions.ProxyError):
        return 'falha de conexão com o proxy', True
    return 'conexão interrompida', True


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


logger = logging.getLogger(__name__)


@dataclass
class HTTPResult:
    status: int
    url: str
    text: str
    headers: dict


class Portal:
    ACTIONS = {'SearchLeft_button', 'SearchRight_button', 'SYS_REFRESH', 'Map', 'btnApprove'}
    MAX_RESPONSE_BYTES = 16 * 1024 * 1024
    READ_ATTEMPTS = 3

    def __init__(self, settings, session_factory=None, *, sleep_fn=None):
        self.settings = settings
        self.session_factory = session_factory or requests.Session
        self.session = None
        self.form = self.doc = None
        self.start = self.end = None
        self.failed = False
        self.workflow_url = None
        self.mutation_attempted = None
        self._sleep = sleep_fn or time.sleep
        self.on_retry = None

    def __enter__(self):
        self.session = self.session_factory()
        self.session.trust_env = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) LG-POD-HTTP/1.0',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.7',
        })
        # Retries ficam na lista explícita de consultas, nunca no adapter.
        adapter = requests.adapters.HTTPAdapter(max_retries=0)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        if self.settings.proxy_server:
            proxy = urlsplit(self.settings.proxy_server)
            if proxy.scheme not in {'http','https'} or not proxy.hostname:
                self.session.close()
                raise PortalError('LG_POD_PROXY_SERVER deve conter URL http ou https válida.')
            if self.settings.proxy_user:
                host = proxy.netloc.rsplit('@',1)[-1]
                auth = quote(self.settings.proxy_user,safe='') + ':' + quote(self.settings.proxy_password,safe='')
                proxy_url = urlunsplit((proxy.scheme,auth+'@'+host,proxy.path,proxy.query,''))
            else:
                proxy_url = self.settings.proxy_server
            self.session.proxies.update({'http':proxy_url,'https':proxy_url})
        logger.info('LG POD [%s]: iniciando sessão HTTP independente.', self.settings.operation)
        return self

    def __exit__(self, *args):
        if self.session is not None:
            self.session.close()
        self.form = self.doc = None

    @staticmethod
    def _origin(url):
        parsed = urlsplit(url)
        return parsed.scheme,parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80)

    def _http(self, method, url, *, data=None, headers=None, follow=False, label='requisição'):
        # Neste portal as consultas também usam POST. O verbo sozinho não
        # permite decidir se uma chamada pode ser repetida.
        target = urlsplit(url)
        read_only = (method == 'POST' and label in {'SearchLeft_button', 'SearchRight_button', 'SYS_REFRESH'}
                     and target.path == '/base/view.x2ps' and parse_qs(target.query).get('action') == [label])
        limit = self.settings.read_attempts if read_only else 1
        for attempt in range(1, limit + 1):
            try:
                result = self._http_once(method, url, data=data, headers=headers, follow=follow, label=label)
                if attempt > 1:
                    message = f'Comunicação HTTP em {label} restabelecida na tentativa {attempt}/{limit}.'
                    logger.info('LG POD [%s]: %s', self.settings.operation, message)
                    if self.on_retry is not None:
                        self.on_retry(message)
                return result
            except TransientReadError as exc:
                if attempt == limit:
                    suffix = (f'Limite de {limit} tentativa(s) de consulta atingido.' if read_only else
                              'Não houve repetição automática desta operação.')
                    raise PortalError(f'Falha HTTP em {label}: {exc}. {suffix}') from None
                delay = min(self.settings.read_retry_max_delay_s,
                            self.settings.read_retry_delay_s * 2 ** (attempt - 1))
                if exc.retry_after is not None:
                    # Um Retry-After acima do limite não autoriza repetir antes do prazo.
                    if exc.retry_after > self.settings.read_retry_max_delay_s:
                        raise PortalError(f'Falha HTTP em {label}: {exc}. Retry-After excede a espera máxima configurada.') from None
                    delay = max(delay, exc.retry_after)
                message = (f'Consulta {label}: {exc}. Nova tentativa {attempt+1}/{limit} '
                           f'em {delay:g}s; nenhuma alteração será repetida.')
                logger.warning('LG POD [%s]: %s', self.settings.operation, message)
                if self.on_retry is not None:
                    self.on_retry(message)
                self._sleep(delay)
                self.failed = False

    def _http_once(self, method, url, *, data=None, headers=None, follow=False, label='requisição'):
        if self.failed or self.session is None:
            raise PortalError('Sessão HTTP encerrada após falha; inicie uma nova execução.')
        base = self.settings.base_url.rstrip('/')
        headers = dict(headers or {})
        for _ in range(6):
            if self._origin(url) != self._origin(base):
                self.failed = True
                raise PortalError('O portal tentou redirecionar a sessão para outro servidor.')
            try:
                if method == 'POST' and label in {'Map', 'btnApprove'}:
                    # Marcar antes da chamada: um timeout não prova que o servidor não recebeu.
                    self.mutation_attempted = True
                response = self.session.request(method,url,data=data,headers=headers,
                    timeout=(min(15.0,self.settings.timeout_ms/1000),self.settings.timeout_ms/1000),
                    verify=getattr(self.settings,'ca_bundle','') or True,
                    allow_redirects=False,stream=True)
                try:
                    status = response.status_code
                    response_headers = dict(response.headers)
                    if status in {301,302,303,307,308}:
                        if not follow or (method=='POST' and status in {307,308}):
                            self.failed = True
                            raise PortalError('Redirecionamento inesperado em '+label+'; nenhum POST será repetido.')
                        location = response.headers.get('Location','')
                        if not location:
                            self.failed = True
                            raise PortalError('Redirecionamento sem destino no portal LG.')
                        url = urljoin(url,location)
                        if method=='POST':
                            method,data = 'GET',None
                            headers.pop('Content-Type',None)
                        continue
                    if status != 200:
                        self.failed = True
                        if status in {429, 502, 503, 504}:
                            retry_after = None
                            raw = response.headers.get('Retry-After', '').strip()
                            if raw:
                                try:
                                    retry_after = float(raw)
                                    if not math.isfinite(retry_after):
                                        retry_after = float('inf')
                                    else:
                                        retry_after = max(0.0, retry_after)
                                except ValueError:
                                    # HTTP-date ou formato desconhecido: não antecipar uma repetição.
                                    retry_after = float('inf')
                            raise TransientReadError(f'HTTP {status}', retry_after)
                        raise PortalError(f'Falha HTTP {status} em {label}. A operação não será repetida.')
                    chunks,size = [],0
                    for chunk in response.iter_content(chunk_size=65536):
                        size += len(chunk)
                        if size > self.MAX_RESPONSE_BYTES:
                            self.failed = True
                            raise PortalError('Resposta do portal excedeu o limite de tamanho.')
                        chunks.append(chunk)
                    content_type = response.headers.get('Content-Type','')
                    encoding = response.encoding if 'charset=' in content_type.lower() else 'utf-8'
                    text = b''.join(chunks).decode(encoding or 'utf-8',errors='replace')
                    return HTTPResult(status,url,text,response_headers)
                finally:
                    response.close()
            except requests.exceptions.SSLError:
                self.failed = True
                raise PortalError('Falha no certificado HTTPS da LG/proxy. Configure LG_POD_CA_BUNDLE se houver certificado corporativo.') from None
            except requests.exceptions.RequestException as exc:
                self.failed = True
                reason, transient = transport_reason(exc)
                if transient and isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                                                   requests.exceptions.ChunkedEncodingError)):
                    raise TransientReadError(f'{type(exc).__name__}: {reason}') from None
                raise PortalError(f'Falha HTTP em {label}: {type(exc).__name__}: {reason}. Não houve repetição automática.') from None
        self.failed = True
        raise PortalError('Limite de redirecionamentos do portal atingido.')

    def _consume(self, text, *, fragment=False):
        try:
            form,doc = JDAForm(text),parse_html(text)
            if not fragment:
                sys_id = form.get('SYS_ID')
                if not sys_id or 'tiWPodMap' not in form.get('PAGE_NAME'):
                    raise ValueError('Workflow inesperado')
                if self.form is not None and sys_id != self.form.get('SYS_ID'):
                    raise ValueError('Instância do workflow mudou')
                if form.get('USERID') != self.settings.user:
                    raise ValueError('Usuário do workflow diverge do login')
                self.form,self.doc = form,doc
            return form,doc
        except (ValueError,KeyError):
            self.failed = True
            raise PortalError('Resposta JDA não reconhecida ou sessão expirada; execução interrompida.') from None

    def login(self):
        base = self.settings.base_url.rstrip('/')
        self._http('GET',base+'/tm/admin/LoginView.jsp',follow=True,label='abertura do login')
        script = self._http('GET',base+'/tm/framework/includes/i2tmgenerated.js',follow=True,label='parâmetros do login')
        encrypted,disguised = encode_password(self.settings.password,script.text)
        response = self._http('POST',base+'/tm/admin/LoginViewController.jsp',data={
            'ControllerAction':'Login','newPassword':'','loginPassword':encrypted,
            'loginUser':self.settings.user,'dspLoginPassword':disguised,
        },headers={'Origin':base,'Referer':base+'/tm/admin/LoginView.jsp'},follow=True,label='login LG')
        if urlsplit(response.url).path != '/tm/entry/main.gsm':
            self.failed = True
            raise PortalError('O portal não confirmou o login. Verifique usuário e senha LG.')
        self.workflow_url = base+'/base/start.x2ps?'+urlencode({
            'SERVICE_NAME':'tms_load','START_WORKFLOW':'tiWPodMap','userID':self.settings.user})
        response = self._http('GET',self.workflow_url,follow=True,label='abertura do mapeamento')
        self._consume(response.text)

    def action(self, action, *, offset=None, selected_left=None, selected_right=None):
        if action not in self.ACTIONS or self.form is None:
            raise PortalError('Ação JDA indisponível na sessão atual.')
        if action == 'Map' and (not selected_left or not selected_right):
            raise PortalError('Mapear exige exatamente um comprovante e uma nota.')
        if action == 'btnApprove' and (not selected_left or selected_right is not None):
            raise PortalError('Enviar para aprovação exige exatamente um comprovante mapeado.')
        if offset is not None and action != 'SYS_REFRESH':
            raise PortalError('Paginação permitida somente na consulta SYS_REFRESH.')
        if action in {'Map','btnApprove'} and not self.settings.send_enabled:
            raise PortalError('O envio está desabilitado no .env (LG_POD_ENVIO_HABILITADO).')
        try:
            group = str(uuid.UUID(self.form.get('SYS_ID').split('^')[-1]))
            payload = self.form.xml(action,component='undefined' if offset is not None else '',
                                    offset=offset,selected_left=selected_left,selected_right=selected_right)
        except ValueError as exc:
            raise PortalError('Não foi possível montar o formulário JDA: '+str(exc)) from None
        params = {}
        if offset is not None:
            params['LAF_FILTER_ID'] = 'MPpb'
        params.update({'X-Correlation-Id':str(uuid.uuid4()),'X-Correlation-Group-Id':group,'action':action})
        base = self.settings.base_url.rstrip('/')
        response = self._http('POST',base+'/base/view.x2ps?'+urlencode(params),data=payload,
            headers={'Content-Type':'application/xml; charset=UTF-8','Origin':base,
                     'Referer':self.workflow_url or base+'/base/start.x2ps'},label=action)
        if offset is None:
            self._consume(response.text)
            return self.doc
        return self._consume(response.text,fragment=True)

    def search(self, start, end):
        self.start,self.end = start,end
        if self.form is None:
            raise PortalError('Faça login no portal LG antes da consulta.')
        self.form.set('FROM_CREATION_DATE_DC',start.strftime('%d/%m/%Y'))
        self.form.set('TO_CREATION_DATE_DC',end.strftime('%d/%m/%Y'))
        self.form.set('POD_CARRIER_CODE',self.settings.carrier)
        for attempt in range(self.READ_ATTEMPTS):
            self.action('SearchLeft_button')
            try:
                return self.load_pods()
            except PaginationChanged:
                if attempt == self.READ_ATTEMPTS - 1:
                    raise
                logger.warning('LG POD: lista mudou; reiniciando consulta de comprovantes (%s/%s).',
                               attempt + 2, self.READ_ATTEMPTS)

    @staticmethod
    def _total(doc, key):
        try:
            value = int(doc.fields[key])
            if value < 0:
                raise ValueError()
            return value
        except (KeyError,ValueError):
            raise PortalError('Contagem de registros inválida na resposta JDA.') from None

    def load_pods(self):
        total = self._total(self.doc,'RECORD_COUNT')
        if total > self.settings.max_records:
            raise PortalError('Contagem de comprovantes acima de LG_POD_MAX_REGISTROS.')
        for _ in range(self.settings.max_records//50+3):
            rows = self.doc.pods
            identifiers = [r.pod_id for r in rows]
            if any(not k.isdigit() for k in identifiers) or len(set(identifiers)) != len(identifiers):
                raise PortalError('IDs ausentes ou duplicados na paginação de comprovantes.')
            if len(rows)==total:
                return list(rows)
            if len(rows)>total:
                raise PortalError('Contagem de comprovantes inconsistente.')
            offset = len(rows)
            form,doc = self.action('SYS_REFRESH',offset=offset)
            received_total = self._total(doc,'RECORD_COUNT')
            received_offset = self._total(doc,'START_COUNT')
            if received_total != total or received_offset != offset:
                raise PaginationChanged('A lista de comprovantes mudou durante a paginação; refaça a consulta. '
                                        f'Total esperado/recebido: {total}/{received_total}; '
                                        f'posição esperada/recebida: {offset}/{received_offset}.')
            if not doc.pods:
                raise PortalError('A paginação não avançou.')
            self.form.append_pod_page(form)
            self.doc.pods.extend(doc.pods)
        raise PortalError('Limite de paginação atingido.')

    def refresh_pod(self, pod_id):
        return next((r for r in self.search(self.start,self.end) if r.pod_id==pod_id),None)

    def find_invoice(self, nf):
        self.form.set('FROM_NF_ISSUE_DATE_DC',self.settings.invoice_start.strftime('%d/%m/%Y'))
        self.form.set('TO_NF_ISSUE_DATE_DC',today().strftime('%d/%m/%Y'))
        for field in ('GERP_ORDER_NO','NF_SERIE','LD_LEG_ID0','PROTOCOL_NUMBER'):
            self.form.set(field,'')
        self.form.set('NF_NO',nf)
        self.action('SearchRight_button')
        return choose_invoice(self.doc.invoices,nf,self.settings.carrier,
                              self._total(self.doc,'MPTable_RECORD_COUNT'))

    def map(self, pod, invoice):
        self.mutation_attempted = False
        for attempt in range(self.READ_ATTEMPTS):
            try:
                rows = self.load_pods()
                break
            except PaginationChanged:
                if attempt == self.READ_ATTEMPTS - 1:
                    raise
                logger.warning('LG POD: lista mudou antes de mapear NF %s; refazendo consultas (%s/%s).',
                               invoice.nf, attempt + 2, self.READ_ATTEMPTS)
                # Descartar todas as páginas antigas. A busca da esquerda pode
                # redesenhar a direita: consultar a mesma NF e validar sua chave novamente.
                self.action('SearchLeft_button')
                refreshed = self.find_invoice(invoice.nf)
                if refreshed.key != invoice.key:
                    raise PortalError('A identidade da nota mudou durante a nova consulta.')
        current = next((r for r in rows if r.pod_id==pod.pod_id),None)
        if (not current or current.filename!=pod.filename
                or eligibility(current,self.settings.carrier)[0]!=invoice.nf
                or current.values.get('NOTAS_MAPEADAS', '').strip()):
            raise PortalError('O comprovante mudou desde a busca da NF; mapeamento interrompido.')
        selected = choose_invoice(self.doc.invoices,invoice.nf,self.settings.carrier,
                                  self._total(self.doc,'MPTable_RECORD_COUNT'))
        if selected.key != invoice.key:
            raise PortalError('A identidade da nota mudou desde a consulta.')
        doc = self.action('Map',selected_left=pod.pod_id,selected_right=invoice.key)
        if not mapping_success(doc.message,invoice.nf,invoice.values['LD_NF_SERIE']):
            raise PortalError('O portal não confirmou o mapeamento da NF e série esperadas.')
        return doc.message

    def approve(self, pod):
        self.mutation_attempted = False
        try:
            nf = invoice_from_filename(pod.filename)
        except ValueError:
            raise PortalError('Nome do comprovante não identifica uma NF válida para aprovação.') from None
        current = next((r for r in self.doc.pods if r.pod_id==pod.pod_id),None)
        if (not current or current.filename!=pod.filename or current.status!='MAPPED'
                or current.values.get('POD_STATUS')!='2'
                or current.values.get('P_CARR_CD')!=self.settings.carrier
                or current.values.get('NOTAS_MAPEADAS','').strip()!=nf
                or current.values.get('NOTAS_MAPEADAS')!=pod.values.get('NOTAS_MAPEADAS')):
            raise PortalError('O vínculo mapeado mudou antes do envio para aprovação.')
        doc = self.action('btnApprove',selected_left=pod.pod_id)
        if not approval_success(doc.message):
            raise PortalError('O portal não confirmou o envio para aprovação.')
        return doc.message
