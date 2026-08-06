from pathlib import Path

from modules.ocorrencia_73.config import (
    CLIENTES_PERMITIDOS,
    ROTAS_PERMITIDAS,
)
from modules.ocorrencia_73.parser import (
    carregar_relatorio,
    filtrar_registros,
)


def test_carregar_e_filtrar_relatorio():
    arquivo = Path(
        "downloads/CSVROD00332012ROD[1]075307.sswweb"
    )

    registros = carregar_relatorio(arquivo)

    assert isinstance(registros, list)
    assert len(registros) > 0

    filtrados = filtrar_registros(
        registros=registros,
        clientes_permitidos=CLIENTES_PERMITIDOS,
        rotas_permitidas=ROTAS_PERMITIDAS,
    )

    assert isinstance(filtrados, list)

    for item in filtrados:
        rota = (
            item["unidade_emissora"],
            item["cidade_destinatario"],
        )

        assert rota in {
            ("JOI", "FLORIANOPOLIS"),
            ("CWB", "CURITIBA"),
        }

from modules.ocorrencia_73.parser import (
    filtrar_registros,
)


def test_filtrar_todas_nomenclaturas_clientes():
    clientes = [
        "WHIRLPOOL S/A",
        "WHIRLPOOL S/A (C2)",
        "WHIRLPOOL SA",
        "MLOG ARMAZEM GERAL LTDA",
        "BUD COM. DE ELETRODOM. LTDA",
    ]

    registros = []

    for indice, cliente in enumerate(
        clientes,
        start=1,
    ):
        registros.append({
            "SERIE/NUMERO CTRC": (
                f"JOI83960{indice}-{indice}"
            ),
            "CLIENTE PAGADOR": cliente,
            "CIDADE DO DESTINATARIO": (
                "FLORIANOPOLIS"
            ),
            "UNIDADE EMISSORA": "JOI",
        })

    filtrados = filtrar_registros(
        registros=registros,
        clientes_permitidos=set(clientes),
        rotas_permitidas={
            "JOI": {
                "FLORIANOPOLIS",
            },
            "CWB": {
                "CURITIBA",
            },
        },
    )

    assert len(filtrados) == 5

    clientes_filtrados = {
        item["cliente_pagador"]
        for item in filtrados
    }

    assert clientes_filtrados == set(clientes)

def test_rotas_permitidas():
    registros = [
        {
            "SERIE/NUMERO CTRC": "JOI111111-0",
            "CLIENTE PAGADOR": "WHIRLPOOL SA",
            "CIDADE DO DESTINATARIO": "FLORIANOPOLIS",
            "UNIDADE EMISSORA": "JOI",
        },
        {
            "SERIE/NUMERO CTRC": "JOI111112-0",
            "CLIENTE PAGADOR": "WHIRLPOOL SA",
            "CIDADE DO DESTINATARIO": "CURITIBA",
            "UNIDADE EMISSORA": "JOI",
        },
        {
            "SERIE/NUMERO CTRC": "CWB111113-0",
            "CLIENTE PAGADOR": "WHIRLPOOL SA",
            "CIDADE DO DESTINATARIO": "CURITIBA",
            "UNIDADE EMISSORA": "CWB",
        },
        {
            "SERIE/NUMERO CTRC": "CWB111114-0",
            "CLIENTE PAGADOR": "WHIRLPOOL SA",
            "CIDADE DO DESTINATARIO": "FLORIANOPOLIS",
            "UNIDADE EMISSORA": "CWB",
        },
    ]

    filtrados = filtrar_registros(
        registros=registros,
        clientes_permitidos={
            "WHIRLPOOL SA",
        },
        rotas_permitidas={
            "JOI": {
                "FLORIANOPOLIS",
            },
            "CWB": {
                "CURITIBA",
            },
        },
    )

    assert len(filtrados) == 2

    assert {
        (
            item["unidade_emissora"],
            item["cidade_destinatario"],
        )
        for item in filtrados
    } == {
        ("JOI", "FLORIANOPOLIS"),
        ("CWB", "CURITIBA"),
    }