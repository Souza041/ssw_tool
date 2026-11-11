from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi import HTTPException

from modules.incidentes import router
from modules.incidentes.schemas import DashboardFilters
from modules.incidentes.service import (
    REGISTRO_REGRA_CAMPOS,
    IncidentesService,
)


def request_sintetico():
    return SimpleNamespace(session={})


def argumentos_handler(request):
    return {
        "request": request,
        "regra": "CODIGO_CONFIRMADO",
        "pagina": 1,
        "limite": 25,
        "busca": "",
        "data_inicial": None,
        "data_final": None,
        "grupos_clientes": None,
        "clientes": None,
        "unidades": None,
        "codigos_ocorrencia": None,
        "status_debito": None,
        "regras_debito": None,
        "produtos": None,
        "tipos_operacao": None,
    }


def test_anonimo_negado_antes_da_consulta(monkeypatch):
    consulta = Mock()
    monkeypatch.setattr(router, "obter_client", lambda request: None)
    monkeypatch.setattr(
        router,
        "is_admin",
        lambda request: pytest.fail("Autorização não deve preceder autenticação"),
    )
    monkeypatch.setattr(router.service, "consultar_registros_regra", consulta)

    with pytest.raises(HTTPException) as erro:
        router.consultar_registros_regra(
            **argumentos_handler(request_sintetico())
        )

    assert erro.value.status_code == 401
    assert erro.value.headers == {"Cache-Control": "no-store"}
    consulta.assert_not_called()


def test_autenticado_sem_permissao_negado_antes_da_consulta(monkeypatch):
    consulta = Mock()
    monkeypatch.setattr(router, "obter_client", lambda request: object())
    monkeypatch.setattr(router, "is_admin", lambda request: False)
    monkeypatch.setattr(router.service, "consultar_registros_regra", consulta)

    with pytest.raises(HTTPException) as erro:
        router.consultar_registros_regra(
            **argumentos_handler(request_sintetico())
        )

    assert erro.value.status_code == 403
    assert erro.value.headers == {"Cache-Control": "no-store"}
    consulta.assert_not_called()


def test_admin_autorizado_preserva_parametros(monkeypatch):
    esperado = {
        "regra": "CODIGO_CONFIRMADO",
        "total": 0,
        "pagina": 2,
        "limite": 10,
        "total_paginas": 0,
        "registros": [],
    }
    consulta = Mock(return_value=esperado)
    monkeypatch.setattr(router, "obter_client", lambda request: object())
    monkeypatch.setattr(router, "is_admin", lambda request: True)
    monkeypatch.setattr(router.service, "consultar_registros_regra", consulta)
    argumentos = argumentos_handler(request_sintetico())
    argumentos.update({
        "pagina": 2,
        "limite": 10,
        "busca": "CWB123",
        "unidades": ["CWB"],
        "status_debito": ["DEBITO"],
    })

    assert router.consultar_registros_regra(**argumentos) == esperado
    chamada = consulta.call_args.kwargs
    assert chamada["regra"] == "CODIGO_CONFIRMADO"
    assert chamada["pagina"] == 2
    assert chamada["limite"] == 10
    assert chamada["busca"] == "CWB123"
    assert chamada["filtros"].unidades == ["CWB"]
    assert chamada["filtros"].status_debito == ["DEBITO"]


class RepositorioSintetico:
    def __init__(self, linhas):
        self.linhas = linhas
        self.consultas = 0

    def auditoria(self):
        self.consultas += 1
        return pd.DataFrame(self.linhas)


def linha_sintetica(ctrc, nf, unidade, regra="CODIGO_CONFIRMADO"):
    return {
        "CTRC": ctrc,
        "NRO_NOTA_FISCAL": nf,
        "EMISSAO_CTRC": "10/11/2026",
        "DATA_OCORRENCIA": "11/11/2026",
        "GRUPO_CLIENTE": "GRUPO A",
        "NOME_PAGADOR": "CLIENTE A",
        "UNIDADE": unidade,
        "TIPO_OPERACAO": "NORMAL",
        "COD_OCOR": "73",
        "STATUS_DEBITO": "DEBITO",
        "REGRA_VALIDACAO_DEBITO": regra,
        "PRODUTO": "PRODUTO A",
        "VALOR_CARGA_CTE": "1.234,56",
        "CPF": "12345678901",
        "SENHA": "segredo-sintetico",
        "PASSWORD": "password-sintetico",
        "TOKEN": "token-sintetico",
        "COOKIE": "cookie-sintetico",
        "DB_HOST": "banco-interno",
        "PAYLOAD_JSON": '{"interno":true}',
        "URL_TEMPORARIA": "https://interno.invalid/segredo",
    }


def test_resposta_usa_lista_fechada_sem_campos_sensiveis():
    repositorio = RepositorioSintetico([
        linha_sintetica("CWB100-1", "NF-1", "CWB")
    ])
    resposta = IncidentesService(repositorio).consultar_registros_regra(
        regra="CODIGO_CONFIRMADO",
        filtros=DashboardFilters(),
    )

    registro = resposta["registros"][0]
    assert tuple(registro) == REGISTRO_REGRA_CAMPOS
    assert registro["ctrc"] == "CWB100-1"
    assert registro["numero_nota"] == "NF-1"
    assert registro["data_inclusao_ctrc"] == date(2026, 11, 10)
    assert registro["data_ocorrencia"] == date(2026, 11, 11)
    assert registro["valor_carga"] == 1234.56
    representacao = repr(resposta)
    for segredo in (
        "12345678901",
        "segredo-sintetico",
        "password-sintetico",
        "token-sintetico",
        "cookie-sintetico",
        "banco-interno",
        "interno.invalid",
    ):
        assert segredo not in representacao


def test_paginacao_busca_e_filtros_sao_preservados():
    repositorio = RepositorioSintetico([
        linha_sintetica("CWB100-1", "NF-1", "CWB"),
        linha_sintetica("GRU200-2", "NF-2", "GRU"),
        linha_sintetica("GRU300-3", "NF-3", "GRU", regra="OUTRA"),
    ])
    service = IncidentesService(repositorio)

    filtrada = service.consultar_registros_regra(
        regra="CODIGO_CONFIRMADO",
        filtros=DashboardFilters(unidades=["GRU"]),
        busca="GRU200",
        pagina=1,
        limite=10,
    )
    assert filtrada["total"] == 1
    assert filtrada["total_paginas"] == 1
    assert filtrada["registros"][0]["ctrc"] == "GRU200-2"

    paginada = service.consultar_registros_regra(
        regra="CODIGO_CONFIRMADO",
        filtros=DashboardFilters(),
        pagina=2,
        limite=1,
    )
    assert paginada["total"] == 2
    assert paginada["pagina"] == 2
    assert paginada["limite"] == 1
    assert paginada["total_paginas"] == 2
    assert paginada["registros"][0]["ctrc"] == "GRU200-2"
