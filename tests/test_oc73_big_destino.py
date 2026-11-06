"""Regra BIG: destino, receptora, OC64 e data; UF apenas informativa."""
from datetime import date

import pytest

from modules.ocorrencia_73.parser import filtrar_registros

D = date(2026, 11, 5)


def registro(**alteracoes):
    item = {
        "Serie/Numero CTRC": "CWB490897-0",
        "Unidade Emissora": "CWB",
        "Cidade do Destinatário": "FLORIANOPOLIS",
        "Cidade de Entrega": "BIGUACU",
        "Unidade Receptora": "BIG",
        "UF do Expedidor": "SP",
        "Codigo Ultima Ocorrencia": "64",
        "Data da Ultima Ocorrencia": "05/11/2026",
        "Data de Emissao": "03/11/2026",
    }
    item.update(alteracoes)
    return item


def filtrar(item):
    return filtrar_registros(
        [item], fluxo="BIG", data_inicial_ocorrencia=D, data_final_ocorrencia=D,
    )


@pytest.mark.parametrize("uf", ["SP", "SC", "MG", "PR", "", None])
@pytest.mark.parametrize("cidade", ["FLORIANOPOLIS", "Florianópolis", " florianópolis "])
def test_aceita_destino_big_independente_de_uf(uf, cidade):
    itens = filtrar(registro(**{"UF do Expedidor": uf, "Cidade do Destinatário": cidade}))
    assert len(itens) == 1
    assert itens[0]["serie"] == "CWB"
    assert itens[0]["numero"] == "490897"
    assert itens[0]["fluxo"] == "BIG"
    assert itens[0]["data_oc64_relatorio"] == "05/11/2026"


def test_uf_nao_e_mais_coluna_obrigatoria():
    item = registro()
    del item["UF do Expedidor"]
    assert len(filtrar(item)) == 1


@pytest.mark.parametrize("campo,valor", [
    ("Cidade do Destinatário", "BIGUACU"),
    ("Cidade do Destinatário", ""),
    ("Unidade Receptora", "JOI"),
    ("Unidade Receptora", "BIGP"),
    ("Codigo Ultima Ocorrencia", "63"),
    ("Codigo Ultima Ocorrencia", "77"),
    ("Codigo Ultima Ocorrencia", "73"),
    ("Data da Ultima Ocorrencia", "04/11/2026"),
    ("Data da Ultima Ocorrencia", "06/11/2026"),
    ("Data da Ultima Ocorrencia", ""),
])
def test_rejeita_fora_da_regra_mesmo_com_uf_sc(campo, valor):
    item = registro(**{"UF do Expedidor": "SC", campo: valor, "Cidade de Entrega": "FLORIANOPOLIS"})
    assert filtrar(item) == []


@pytest.mark.parametrize("campo", ["Cidade do Destinatário", "Unidade Receptora"])
def test_coluna_ausente_ou_ambigua_bloqueia(campo):
    item = registro()
    del item[campo]
    with pytest.raises(KeyError):
        filtrar(item)
    item = registro()
    item[campo + " "] = item[campo]
    with pytest.raises(KeyError):
        filtrar(item)
