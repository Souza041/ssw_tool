import pytest
from modules.ocorrencia_73.parser import filtrar_registros


def registro(ctrc, emissora='CWB', cidade='CURITIBA', ocorrencia='63', uf='PR', cliente='CLIENTE TESTE', receptora='CWB'):
    return {
        'Cliente Pagador': cliente,
        'Cidade do Destinatario': cidade,
        'Unidade Emissora': emissora,
        'Unidade Receptora': receptora,
        'Serie/Numero CTRC': ctrc,
        'Codigo Ultima Ocorrencia': ocorrencia,
        'UF do Expedidor': uf,
    }


def test_regras_ocorrencia_73():
    registros = [
        registro('CWB100001-1'),
        registro('JOI100002-2', emissora='JOI', cidade='FLORIANOPOLIS', ocorrencia='1'),
        registro('JOI100003-3', emissora='JOI', cidade='FLORIANOPOLIS', ocorrencia='64', uf='SC', receptora='BIG'),
        registro('CWB100004-4', ocorrencia='64'),
        registro('CWB100005-5', emissora='POA'),
        registro('CWB100006-6', cidade='SAO JOSE'),
        registro('CWB100007-7', cliente='CLIENTE QUALQUER LTDA'),
    ]
    cwb = filtrar_registros(registros, fluxo='CWB')
    big = filtrar_registros(registros, fluxo='BIG')
    assert {r['ctrc_original'] for r in cwb} == {'CWB100001-1', 'CWB100007-7'}
    assert {r['ctrc_original'] for r in big} == {'JOI100003-3'}
    assert all(r['regra_filtro'] == 'cwb_oc63' for r in cwb)
    assert all(r['regra_filtro'] == 'big_florianopolis_oc64' for r in big)


def test_big_oc64_qualquer_emissora():
    registros = [registro(f'{emissora}200001-1', emissora=emissora, cidade='FLORIANOPOLIS', ocorrencia='64', uf='SC', receptora='BIG')
                 for emissora in ('BHZ', 'GRU', 'CWB', 'JOI')]
    resultado = filtrar_registros(registros, fluxo='BIG')
    assert {r['ctrc_original'] for r in resultado} == {r['Serie/Numero CTRC'] for r in registros}


def test_big_oc64_cliente_nao_permitido():
    registros = [registro('BHZ200002-2', emissora='BHZ', cidade='FLORIANOPOLIS', ocorrencia='64', uf='SC', cliente='CLIENTE NAO PERMITIDO', receptora='BIG')]
    resultado = filtrar_registros(registros, clientes_permitidos={'WHIRLPOOL S/A'}, fluxo='BIG')
    assert len(resultado) == 1  # BIG nao filtra cliente
    assert resultado[0]['cliente_pagador'] == 'CLIENTE NAO PERMITIDO'


@pytest.mark.parametrize('cidade,receptora,ocorrencia,esperado', [
    ('FLORIANOPOLIS', 'BIG', '64', 1),
    ('PALHOCA', 'BIG', '64', 0),
    ('FLORIANOPOLIS', 'CWB', '64', 0),
    ('FLORIANOPOLIS', 'BIG', '63', 0),
])
def test_big_exige_floria_receptora_big_e_oc64(cidade, receptora, ocorrencia, esperado):
    registros = [registro('BHZ200003-3', emissora='BHZ', cidade=cidade, receptora=receptora, ocorrencia=ocorrencia)]
    assert len(filtrar_registros(registros, fluxo='BIG')) == esperado


def test_big_nao_usa_uf_como_prova_de_destino():
    registros = [registro('BHZ200004-4', emissora='BHZ', cidade='FLORIANOPOLIS',
                         receptora='BIG', ocorrencia='64', uf='PR')]
    assert len(filtrar_registros(registros, fluxo='BIG')) == 1
