
from unittest.mock import Mock, call

import pytest

from modules.incidentes.publicacao_integrada import (
    FalhaPublicacaoIntegrada,
    finalizar_publicacao_incidentes,
)


@pytest.fixture
def dependencias(tmp_path):
    eventos = []

    arquivo_auditoria = tmp_path / "auditoria.xlsx"
    arquivo_volume = tmp_path / "volume.xlsx"

    arquivo_auditoria.write_bytes(b"auditoria")
    arquivo_volume.write_bytes(b"volume")

    def importador(*args, **kwargs):
        eventos.append(
            "mysql_dry_run"
            if kwargs.get("dry_run")
            else "mysql_importacao"
        )
        return {"status": "OK"}

    def guarda(**kwargs):
        eventos.append("guarda")
        return {
            "status": "VALIDADA",
            "importado": True,
            "publicado": False,
        }

    def publicar_auditoria(arquivo):
        eventos.append("auditoria_excel")
        return {
            "current": "current/auditoria.xlsx",
            "history": "history/auditoria.xlsx",
            "history_removed": 0,
        }

    def publicar_volume(arquivo):
        eventos.append("volume_excel")
        return {
            "current": "current/volume.xlsx",
            "history": "history/volume.xlsx",
            "history_removed": 0,
        }

    incident_publisher = Mock()
    incident_publisher.publish.side_effect = publicar_auditoria

    volume_publisher = Mock()
    volume_publisher.publish.side_effect = publicar_volume

    return {
        "eventos": eventos,
        "arquivo_auditoria": arquivo_auditoria,
        "arquivo_volume": arquivo_volume,
        "importador": importador,
        "guarda": guarda,
        "incident_publisher": incident_publisher,
        "volume_publisher": volume_publisher,
    }


def executar(dep, *, importar_mysql=True, guarda=None):
    return finalizar_publicacao_incidentes(
        arquivo_auditoria=dep["arquivo_auditoria"],
        arquivo_volume=dep["arquivo_volume"],
        importar_mysql=importar_mysql,
        total=2,
        extraidos=[("GRUPO", "111"), ("GRUPO", "222")],
        sem_dados=[],
        falhas_extracao=[],
        importar_auditoria=dep["importador"],
        importar_snapshot_validado=guarda or dep["guarda"],
        incident_publisher_factory=lambda: dep["incident_publisher"],
        volume_publisher_factory=lambda: dep["volume_publisher"],
        log=lambda mensagem: None,
    )


def test_publicacao_integrada_respeita_ordem(dependencias):
    resultado = executar(dependencias)

    assert dependencias["eventos"] == [
        "guarda",
        "auditoria_excel",
        "volume_excel",
    ]
    assert resultado["mysql"]["status"] == "VALIDADA"
    assert resultado["auditoria_excel"]["status"] == "PUBLICADA"
    assert resultado["volume_excel"]["status"] == "PUBLICADA"


def test_guarda_bloqueada_impede_publishers(dependencias):
    def guarda_bloqueada(**kwargs):
        dependencias["eventos"].append("guarda")
        return {
            "status": "BLOQUEADO",
            "motivo": "CNPJ com timeout",
            "importado": False,
        }

    with pytest.raises(FalhaPublicacaoIntegrada) as erro:
        executar(dependencias, guarda=guarda_bloqueada)

    assert erro.value.etapa == "MYSQL_BLOQUEADO"
    assert dependencias["eventos"] == ["guarda"]
    dependencias["incident_publisher"].publish.assert_not_called()
    dependencias["volume_publisher"].publish.assert_not_called()


def test_modo_legado_nao_executa_guarda(dependencias):
    resultado = executar(
        dependencias,
        importar_mysql=False,
    )

    assert dependencias["eventos"] == [
        "auditoria_excel",
        "volume_excel",
    ]
    assert resultado["mysql"]["status"] == "DESATIVADO"


def test_falha_volume_identifica_publicacao_parcial(dependencias):
    def falhar_volume(arquivo):
        dependencias["eventos"].append("volume_erro")
        raise RuntimeError("Falha simulada no volume")

    dependencias["volume_publisher"].publish.side_effect = falhar_volume

    with pytest.raises(FalhaPublicacaoIntegrada) as erro:
        executar(dependencias)

    assert erro.value.etapa == "VOLUME_EXCEL"
    assert erro.value.resultado["mysql"]["status"] == "VALIDADA"
    assert erro.value.resultado["auditoria_excel"]["status"] == "PUBLICADA"
    assert erro.value.resultado["volume_excel"]["status"] == "ERRO"
    assert dependencias["eventos"] == [
        "guarda",
        "auditoria_excel",
        "volume_erro",
    ]


def test_erro_na_guarda_impede_publicacoes(dependencias):
    def guarda_com_erro(**kwargs):
        raise RuntimeError("MySQL indisponivel")

    with pytest.raises(FalhaPublicacaoIntegrada) as erro:
        executar(dependencias, guarda=guarda_com_erro)

    assert erro.value.etapa == "MYSQL"
    dependencias["incident_publisher"].publish.assert_not_called()
    dependencias["volume_publisher"].publish.assert_not_called()
