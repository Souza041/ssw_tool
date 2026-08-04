from pathlib import Path

from modules.ocorrencia_73.config import (
    CIDADES_PERMITIDAS,
    CLIENTES_PERMITIDOS,
    UNIDADE_EMISSORA_PERMITIDA,
)
from modules.ocorrencia_73.parser import (
    carregar_relatorio,
    filtrar_registros,
)


def test_carregar_e_filtrar_relatorio():
    arquivo = Path(
        "downloads/CSVROD00314202ROD[1]164516.sswweb"
    )

    registros = carregar_relatorio(arquivo)

    assert isinstance(registros, list)
    assert len(registros) > 0

    filtrados = filtrar_registros(
        registros=registros,
        clientes_permitidos=CLIENTES_PERMITIDOS,
        cidades_permitidas=CIDADES_PERMITIDAS,
        unidade_emissora=UNIDADE_EMISSORA_PERMITIDA,
    )

    assert isinstance(filtrados, list)

    for item in filtrados:
        assert item["unidade_emissora"] == "JOI"

        assert item["cidade_destinatario"] in {
            "CURITIBA",
            "FLORIANOPOLIS",
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
                "CURITIBA"
                if indice % 2
                else "FLORIANOPOLIS"
            ),
            "UNIDADE EMISSORA": "JOI",
        })

    filtrados = filtrar_registros(
        registros=registros,
        clientes_permitidos=set(clientes),
        cidades_permitidas={
            "CURITIBA",
            "FLORIANOPOLIS",
        },
        unidade_emissora="JOI",
    )

    assert len(filtrados) == 5

    clientes_filtrados = {
        item["cliente_pagador"]
        for item in filtrados
    }

    assert clientes_filtrados == set(clientes)