
from unittest.mock import Mock

import pytest

from modules.incidentes.mysql_gate import (
    importar_snapshot_validado,
)


@pytest.fixture
def arquivo_auditoria(tmp_path):
    caminho = tmp_path / "incidentes_auditoria.xlsx"
    caminho.write_bytes(b"arquivo-simulado")
    return caminho


@pytest.fixture
def importador():
    return Mock(
        side_effect=[
            {"status": "SIMULADO", "linhas": 304},
            {"status": "VALIDADA", "importacao_id": 2},
        ]
    )


def executar(
    arquivo,
    importador,
    *,
    total=2,
    extraidos=None,
    sem_dados=None,
    falhas=None,
    permitir_sem_dados=False,
):
    return importar_snapshot_validado(
        arquivo=arquivo,
        total=total,
        extraidos=(
            [("GRUPO", "111"), ("GRUPO", "222")]
            if extraidos is None
            else extraidos
        ),
        sem_dados=[] if sem_dados is None else sem_dados,
        falhas_extracao=[] if falhas is None else falhas,
        importar_auditoria=importador,
        log=lambda mensagem: None,
        permitir_sem_dados=permitir_sem_dados,
    )


def test_importacao_permitida_sem_publicacao(
    arquivo_auditoria,
    importador,
):
    resultado = executar(arquivo_auditoria, importador)

    assert resultado["status"] == "VALIDADA"
    assert resultado["importado"] is True
    assert resultado["publicado"] is False

    assert importador.call_count == 2

    primeira = importador.call_args_list[0]
    segunda = importador.call_args_list[1]

    assert primeira.kwargs["dry_run"] is True
    assert segunda.kwargs["dry_run"] is False

    assert primeira.kwargs["publicar"] is False
    assert segunda.kwargs["publicar"] is False


def test_timeout_bloqueia_importacao(
    arquivo_auditoria,
    importador,
):
    resultado = executar(
        arquivo_auditoria,
        importador,
        falhas=[("GRUPO", "222", "TIMEOUT")],
    )

    assert resultado["status"] == "BLOQUEADO"
    assert resultado["importado"] is False
    importador.assert_not_called()


def test_cobertura_incompleta_bloqueia(
    arquivo_auditoria,
    importador,
):
    resultado = executar(
        arquivo_auditoria,
        importador,
        total=3,
    )

    assert resultado["status"] == "BLOQUEADO"
    assert "Cobertura incompleta" in resultado["motivo"]
    importador.assert_not_called()


def test_cnpj_sem_dados_bloqueia_por_padrao(
    arquivo_auditoria,
    importador,
):
    resultado = executar(
        arquivo_auditoria,
        importador,
        extraidos=[("GRUPO", "111")],
        sem_dados=[("GRUPO", "222")],
    )

    assert resultado["status"] == "BLOQUEADO"
    assert "sem dados" in resultado["motivo"].lower()
    importador.assert_not_called()


def test_sem_dados_autorizado_explicitamente(
    arquivo_auditoria,
    importador,
):
    resultado = executar(
        arquivo_auditoria,
        importador,
        extraidos=[("GRUPO", "111")],
        sem_dados=[("GRUPO", "222")],
        permitir_sem_dados=True,
    )

    assert resultado["status"] == "VALIDADA"
    assert resultado["importado"] is True
    assert resultado["sem_dados"] == 1
    assert importador.call_count == 2


def test_total_zero_bloqueia(
    arquivo_auditoria,
    importador,
):
    resultado = executar(
        arquivo_auditoria,
        importador,
        total=0,
        extraidos=[],
    )

    assert resultado["status"] == "BLOQUEADO"
    importador.assert_not_called()


def test_arquivo_inexistente_bloqueia(
    tmp_path,
    importador,
):
    caminho = tmp_path / "inexistente.xlsx"

    resultado = executar(caminho, importador)

    assert resultado["status"] == "BLOQUEADO"
    importador.assert_not_called()


def test_falha_na_importacao_nao_publica(
    arquivo_auditoria,
):
    importador = Mock(
        side_effect=[
            {"status": "SIMULADO"},
            RuntimeError("Falha simulada no MySQL"),
        ]
    )

    with pytest.raises(
        RuntimeError,
        match="Falha simulada no MySQL",
    ):
        executar(arquivo_auditoria, importador)

    assert importador.call_count == 2

    for chamada in importador.call_args_list:
        assert chamada.kwargs["publicar"] is False
