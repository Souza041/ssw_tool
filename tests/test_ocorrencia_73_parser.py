from pathlib import Path
from modules.ocorrencia_73.parser import carregar_relatorio, filtrar_registros

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'oc73' / 'relatorio_teste.sswweb'


def test_carregar_e_filtrar_relatorio():
    registros = carregar_relatorio(FIXTURE)
    assert len(registros) == 4
    cwb = filtrar_registros(registros, fluxo='CWB')
    big = filtrar_registros(registros, fluxo='BIG')
    assert {r['ctrc_original'] for r in cwb} == {'CWB300001-1'}
    assert {r['ctrc_original'] for r in big} == {'JOI300002-2'}


def test_filtrar_todas_nomenclaturas_clientes():
    clientes = ['WHIRLPOOL S/A', 'WHIRLPOOL S/A (C2)', 'WHIRLPOOL SA', 'MLOG ARMAZEM GERAL LTDA', 'BUD COM. DE ELETRODOM. LTDA']
    registros = [{
        'SERIE/NUMERO CTRC': f'JOI83960{i}-{i}',
        'CLIENTE PAGADOR': cliente,
        'CIDADE DO DESTINATARIO': 'CURITIBA',
        'UNIDADE EMISSORA': 'JOI',
        'UNIDADE RECEPTORA': 'CWB',
        'CODIGO DA ULTIMA OCORRENCIA': '63',
    } for i, cliente in enumerate(clientes, start=1)]
    filtrados = filtrar_registros(registros, fluxo='CWB')
    assert len(filtrados) == 5
    assert {r['cliente_pagador'] for r in filtrados} == set(clientes)


def test_rotas_permitidas():
    registros = [
        {'SERIE/NUMERO CTRC': f'CWB11111{i}-0', 'CLIENTE PAGADOR': 'TESTE',
         'CIDADE DO DESTINATARIO': cidade, 'UNIDADE EMISSORA': emissora,
         'UNIDADE RECEPTORA': 'CWB', 'CODIGO DA ULTIMA OCORRENCIA': '63'}
        for i, (emissora, cidade) in enumerate([
            ('JOI', 'FLORIANOPOLIS'), ('JOI', 'CURITIBA'),
            ('CWB', 'CURITIBA'), ('CWB', 'FLORIANOPOLIS')], start=1)
    ]
    filtrados = filtrar_registros(registros, fluxo='CWB')
    assert {(r['unidade_emissora'], r['cidade_destinatario']) for r in filtrados} == {
        ('JOI', 'CURITIBA'), ('CWB', 'CURITIBA')
    }
