"""Serialização do formulário JDA a partir do HTML da sessão atual.
Mantém campos repetidos, caminhos DATA e metadados das linhas, sem JavaScript.
"""
from dataclasses import dataclass, replace
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET


@dataclass
class Control:
    name: str
    value: str
    path: str = ''
    checkbox: bool = False
    original: str = ''
    checked: bool = False
    options: tuple = ()
    fresh: bool = False


class JDAForm(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.controls = []
        self.select = self.option = self.textarea = None
        self.option_text = []
        self.feed(content)
        # Colunas congeladas e roláveis podem repetir o mesmo campo de linha.
        seen, controls = {}, []
        for control in self.controls:
            key = (control.path, control.name)
            if '/pod_table_row/' in control.path or '/nota_table_row/' in control.path:
                if key in seen:
                    if seen[key].value != control.value:
                        raise ValueError('Campos duplicados de linha têm valores divergentes.')
                    continue
                seen[key] = control
            controls.append(control)
        self.controls = controls

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('name') and 'disabled' not in a:
            kind = a.get('type', '').lower()
            if kind in {'button', 'submit', 'reset', 'file', 'image'}:
                return
            value = a.get('value', 'on' if kind == 'checkbox' else '')
            self.controls.append(Control(a['name'], value, a.get('path', ''),
                                         kind == 'checkbox', value, 'checked' in a))
        elif tag == 'select' and a.get('name'):
            self.select = dict(name=a['name'], path=a.get('path', ''), options=[], selected=[])
        elif tag == 'option' and self.select is not None:
            self.option = a
            self.option_text = []
        elif tag == 'textarea' and a.get('name'):
            self.textarea = dict(name=a['name'], path=a.get('path', '/DATA'), text=[])

    def handle_data(self, data):
        if self.option is not None:
            self.option_text.append(data)
        if self.textarea is not None:
            self.textarea['text'].append(data)

    def handle_endtag(self, tag):
        if tag == 'option' and self.option is not None and self.select is not None:
            value = self.option.get('value', ''.join(self.option_text))
            self.select['options'].append(value)
            if 'selected' in self.option:
                self.select['selected'].append(value)
            self.option = None
        elif tag == 'select' and self.select is not None:
            s = self.select
            value = (s['selected'] or s['options'] or [''])[0]
            self.controls.append(Control(s['name'], value, s['path'], original=value,
                                         options=tuple(s['options'])))
            self.select = None
        elif tag == 'textarea' and self.textarea is not None:
            t = self.textarea
            value = ''.join(t['text'])
            self.controls.append(Control(t['name'], value, t['path'], original=''))
            self.textarea = None

    def get(self, name, path=None):
        controls = [c for c in self.controls if c.name == name and (path is None or c.path == path)]
        if len(controls) != 1:
            raise ValueError('Campo JDA ausente ou ambíguo: ' + name)
        return controls[0].value

    def set(self, name, value, path=None):
        controls = [c for c in self.controls if c.name == name and (path is None or c.path == path)]
        if len(controls) != 1:
            raise ValueError('Campo JDA ausente ou ambíguo: ' + name)
        c = controls[0]
        if c.options and value not in c.options:
            raise ValueError('Opção não disponível no formulário JDA: ' + name)
        c.value = str(value)

    def append_pod_page(self, fragment):
        prefix = '/DATA/pod_table_list/pod_table_row/'
        existing = {(c.path, c.name) for c in self.controls if c.path.startswith(prefix)}
        incoming = [c for c in fragment.controls if c.path.startswith(prefix)]
        if any((c.path,c.name) in existing for c in incoming):
            raise ValueError('Paginação repetiu caminhos de comprovantes.')
        positions = [i for i,c in enumerate(self.controls) if c.path.startswith('/DATA/pod_table_list')]
        insertion = positions[-1]+1 if positions else len(self.controls)
        self.controls[insertion:insertion] = [replace(c,original='',fresh=True) for c in incoming]
        # getNextRows do JDA mantém o START_COUNT original ao anexar páginas.
        # Metadados de total/tamanho são validados pelo cliente HTTP.

    def xml(self, action, *, component='', offset=None, selected_left=None, selected_right=None):
        overrides = {'BUTTON_ID': action, 'COMPONENT_NBR': component}
        controls = [replace(c) for c in self.controls]
        for c in controls:
            if c.name in overrides:
                c.value = overrides[c.name]
            if c.name == 'START_COUNT' and offset is not None:
                c.value = str(offset)
            if c.checkbox:
                chosen = selected_left if c.name == 'SELECTED_KEY_LEFT' else (
                    selected_right if c.name == 'SELECTED_KEY_CD' else None)
                c.checked = chosen is not None and c.value == chosen
        for name, key in [('SELECTED_KEY_LEFT',selected_left),('SELECTED_KEY_CD',selected_right)]:
            if key is not None and sum(c.name == name and c.checked for c in controls) != 1:
                raise ValueError('Não foi possível selecionar exclusivamente o registro esperado: ' + name)
        root = ET.Element('REQUEST')
        ET.SubElement(root, 'AJAX_UPDATE', Value='true')
        for c in controls:
            self.validate_control(c)
            if not c.checkbox or c.checked:
                ET.SubElement(root,c.name,Value=c.value)
        data = ET.SubElement(root, 'DATA')
        paths = {'/DATA': data}
        for c in controls:
            if not (c.path == '/DATA' or c.path.startswith('/DATA/')):
                continue
            self.validate_control(c)
            parent = data
            current = '/DATA'
            for segment in c.path.split('/')[2:]:
                current += '/' + segment
                if current not in paths:
                    if segment.isdigit():
                        # O índice identifica UMA ocorrência do row, não vira tag XML.
                        tag = c.path.split('/')[-2]
                        paths[current] = ET.SubElement(parent,tag)
                    else:
                        # row/42 é criado junto, sem um contêiner row adicional.
                        pieces = c.path.split('/')
                        if segment in {'pod_table_row','nota_table_row'} and pieces[-1].isdigit():
                            paths[current] = parent
                        else:
                            paths[current] = ET.SubElement(parent,segment)
                parent = paths[current]
            attrs = dict(Value=c.value)
            if c.checkbox:
                attrs['Checked'] = str(c.checked).lower()
                if c.checked or c.fresh:
                    attrs['OldChecked'] = '' if c.fresh else 'false'
            elif c.fresh or c.value != c.original or c.name == 'ST_EVENT_RST':
                attrs['OldValue'] = c.original
            ET.SubElement(parent,c.name,attrs)
        return ET.tostring(root,encoding='utf-8',short_empty_elements=True)

    @staticmethod
    def validate_control(c):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',c.name):
            raise ValueError('Nome de campo JDA inválido.')
        if c.path and any(not re.fullmatch(r'[A-Za-z0-9_]+',p) for p in c.path.split('/')[1:]):
            raise ValueError('Caminho JDA inválido.')
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',c.value):
            raise ValueError('Valor JDA contém caracteres inválidos em XML.')
