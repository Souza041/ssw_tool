"""Leitura dos campos do HTML JDA, sem executar JavaScript do HAR."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePosixPath
import re


@dataclass
class Row:
    kind: str
    values: dict = field(default_factory=dict)
    path: str = ''

    @property
    def pod_id(self):
        return self.values.get('ID_POD', '')

    @property
    def filename(self):
        return self.values.get('IMAGE_NAME', '') or PurePosixPath(self.values.get('THUMBNAIL_PATH', '')).name

    @property
    def status(self):
        return self.values.get('STATUS', '').strip().upper()

    @property
    def nf(self):
        return self.values.get('LD_NF_NO', '')

    @property
    def key(self):
        return self.values.get('KEY_CODE', '')


class PortalHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pods = []
        self.invoices = []
        self.fields = {}
        self.message = ''
        self.current = None
        self.row_depth = 0
        self.cell = None
        self.cell_parts = []
        self.result_cell = False
        self.result_parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'tr':
            path = a.get('path', '')
            if '/pod_table_row/' in path or '/nota_table_row/' in path:
                self.current = Row('pod' if '/pod_table_row/' in path else 'invoice', path=path)
                self.row_depth = 1
            elif self.current:
                self.row_depth += 1
        if tag == 'input' and a.get('name'):
            if self.current:
                self.current.values[a['name']] = a.get('value', '')
            else:
                self.fields[a['name']] = a.get('value', '')
        if tag == 'td':
            self.cell = a.get('jsn') if self.current else None
            self.cell_parts = []
            if a.get('jsn') == 'ST_EVENT_RST':
                self.result_cell = True
                self.result_parts = []

    def handle_data(self, data):
        if self.cell:
            self.cell_parts.append(data)
        if self.result_cell:
            self.result_parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'td':
            if self.current and self.cell:
                value = ' '.join(' '.join(self.cell_parts).split())
                # Campos hidden preservam identificadores e zeros à esquerda.
                if self.cell not in self.current.values or value:
                    self.current.values[self.cell] = value
            self.cell = None
            if self.result_cell:
                self.message = ' '.join(' '.join(self.result_parts).split())
                self.result_cell = False
        if tag == 'tr' and self.current:
            self.row_depth -= 1
            if self.row_depth == 0:
                (self.pods if self.current.kind == 'pod' else self.invoices).append(self.current)
                self.current = None


def parse_html(content):
    result = PortalHTML()
    result.feed(content)
    # O JDA divide as colunas congeladas e roláveis em duas tabelas físicas.
    # As duas metades têm o mesmo path; representam UMA nota.
    merged = {}
    for row in result.invoices:
        if row.path in merged:
            merged[row.path].values.update(row.values)
        else:
            merged[row.path] = row
    result.invoices = list(merged.values())
    return result


def invoice_from_filename(filename: str) -> str:
    # O prefixo e o número são tratados como texto; não remover zeros.
    if not filename.startswith('ROBR'):
        raise ValueError('IGNORADO_PREFIXO')
    match = re.fullmatch(r'(ROBR[A-Z0-9]*(?:_[A-Z][A-Z0-9]*)*)_(\d+)\.[A-Za-z0-9]+', filename)
    if not match:
        raise ValueError('NOME_FORA_DO_PADRAO')
    return match.group(2)


def eligibility(row: Row, carrier: str):
    if not row.filename.startswith('ROBR'):
        return None, 'Nome não começa com ROBR.'
    if row.status != 'PENDING' or row.values.get('POD_STATUS') != '0':
        return None, 'Status diferente de PENDING.'
    if row.values.get('P_CARR_CD') != carrier:
        return None, 'Transportador diferente do configurado.'
    try:
        return invoice_from_filename(row.filename), ''
    except ValueError:
        return None, 'Nome ROBR sem número de NF reconhecível; conferir manualmente.'


def choose_invoice(rows, nf, carrier, total):
    if total != 1 or len(rows) != 1:
        raise ValueError('NF não encontrada.' if total == 0 else 'Busca retornou múltiplas notas; conferir NF e série.')
    row = rows[0]
    parts = row.key.split('|')
    if (row.nf != nf or row.values.get('LD_NOTA_CARRIER') != carrier
            or len(parts) != 4 or parts[:3] != [nf, row.values.get('LD_NF_SERIE'), carrier]):
        raise ValueError('A identidade da NF retornada não corresponde ao comprovante.')
    return row


def mapping_success(message, nf, series):
    # Nunca reaproveitar mensagem de outra nota/ação.
    return bool(re.search(r'nota_number:' + re.escape(nf) + r'\s+nota_serie:'
                          + re.escape(series) + r'\s+SUCCESS\b', message))


def approval_success(message):
    cleaned = re.sub(r'&(?:amp;)?#0?10;?', '', message).strip()
    return cleaned.casefold() == 'sucesso'
