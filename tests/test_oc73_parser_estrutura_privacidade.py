import json
from pathlib import Path

import pytest

from modules.ocorrencia_73.parser import (
    carregar_relatorio,
    decompor_ctrc,
    encontrar_coluna,
    filtrar_registros,
)
from modules.ocorrencia_73.service import Ocorrencia73Service


CABECALHO = (
    "Cliente Pagador;Cidade do Destinatario;Unidade Emissora;"
    "Unidade Receptora;Serie/Numero CTRC;Codigo Ultima Ocorrencia"
)
REGISTRO = "CLIENTE SINTETICO;CURITIBA;CWB;CWB;CWB123-1;63"
SEGREDO = "SEGREDO_SINTETICO_NAO_PERSISTIR"


def relatorio(tmp_path, *linhas, terminador=False, nome="teste.sswweb"):
    arquivo = tmp_path / nome
    cabecalho = CABECALHO + (";" if terminador else "")
    arquivo.write_text(
        "PREAMBULO\n" + cabecalho + "\n" + "\n".join(linhas) + "\n",
        encoding="cp1252",
    )
    return arquivo


def test_relatorio_valido_sem_controle_preserva_campos_de_filtro(tmp_path):
    registros = carregar_relatorio(relatorio(tmp_path, REGISTRO))
    assert len(registros) == 1
    filtrados = filtrar_registros(registros, fluxo="CWB")
    assert len(filtrados) == 1
    assert (filtrados[0]["serie"], filtrados[0]["numero"]) == ("CWB", "123")
    assert filtrados[0]["ultima_ocorrencia"] == "63"
    assert filtrados[0]["unidade_emissora"] == "CWB"
    assert filtrados[0]["cidade_destinatario"] == "CURITIBA"
    assert "registro_original" not in filtrados[0]


def test_controle_final_comprovado_nao_vira_registro(tmp_path):
    arquivo = relatorio(tmp_path, REGISTRO, "9", terminador=True)
    assert len(carregar_relatorio(arquivo)) == 1


@pytest.mark.parametrize(
    "linhas,terminador",
    [
        ((REGISTRO, "8"), True),
        ((REGISTRO, "9"), False),
        (("9", REGISTRO), True),
        ((REGISTRO, f"{SEGREDO};CURITIBA"), False),
        ((REGISTRO + f";{SEGREDO}",), False),
    ],
)
def test_largura_invalida_falha_sem_expor_conteudo(tmp_path, linhas, terminador):
    with pytest.raises(ValueError, match="largura inválida") as erro:
        carregar_relatorio(relatorio(tmp_path, *linhas, terminador=terminador))
    assert SEGREDO not in str(erro.value)
    assert REGISTRO not in str(erro.value)


def test_cabecalho_obrigatorio_ausente_nao_expoe_preambulo(tmp_path):
    arquivo = relatorio(tmp_path, REGISTRO.replace("CLIENTE", SEGREDO))
    texto = arquivo.read_text(encoding="cp1252").replace(
        "Cliente Pagador", "Outro Campo"
    ).replace("PREAMBULO", SEGREDO)
    arquivo.write_text(texto, encoding="cp1252")
    with pytest.raises(ValueError, match="cabeçalho") as erro:
        carregar_relatorio(arquivo)
    assert SEGREDO not in str(erro.value)
    assert "Outro Campo" not in str(erro.value)


def test_cabecalho_duplicado_apos_normalizacao(tmp_path):
    arquivo = relatorio(tmp_path, REGISTRO + ";VALOR", terminador=False)
    texto = arquivo.read_text(encoding="cp1252").replace(
        "Codigo Ultima Ocorrencia\n",
        "Codigo Ultima Ocorrencia;CÓDIGO ULTIMA OCORRENCIA\n",
    )
    arquivo.write_text(texto, encoding="cp1252")
    with pytest.raises(ValueError, match="duplicado"):
        carregar_relatorio(arquivo)


def test_erros_de_campos_nao_expoem_valores_arbitrarios():
    with pytest.raises(ValueError) as ctrc:
        decompor_ctrc(SEGREDO)
    with pytest.raises(KeyError) as coluna:
        encontrar_coluna({SEGREDO: "valor"}, "Serie/Numero CTRC")
    assert SEGREDO not in str(ctrc.value)
    assert SEGREDO not in str(coluna.value)


def test_resultado_json_remove_registro_original_recursivamente(tmp_path):
    resultado = {
        "itens": [{"status": "pendente_lancamento", "registro_original": {"nome": SEGREDO}}],
        "lotes": [{"registro_original": SEGREDO}],
    }
    servico = object.__new__(Ocorrencia73Service)
    arquivo = servico.salvar_resultado_json(resultado, tmp_path)
    gravado = json.loads(arquivo.read_text(encoding="utf-8"))
    assert SEGREDO not in arquivo.read_text(encoding="utf-8")
    assert gravado["itens"][0]["status"] == "pendente_lancamento"
    assert "registro_original" not in gravado["itens"][0]
    assert "registro_original" not in gravado["lotes"][0]
    assert resultado["itens"][0]["registro_original"]["nome"] == SEGREDO


ARQUIVO_REAL = (
    Path(__file__).resolve().parents[1]
    / "output/homologacao_op156_897436/CSVROD00897436.sswweb"
)


@pytest.mark.skipif(not ARQUIVO_REAL.is_file(), reason="arquivo homologado ausente")
def test_arquivo_homologado_tem_304_registros_e_zero_elegiveis():
    registros = carregar_relatorio(ARQUIVO_REAL)
    assert len(registros) == 304
    assert filtrar_registros(registros, fluxo="CWB") == []
