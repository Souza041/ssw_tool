"""Testes offline da classificação e fechamento OC73; nenhum acesso externo."""
from datetime import date
from unittest.mock import MagicMock

import pytest

from modules.ocorrencia_73.service import (
    Ocorrencia73Service,
    calcular_indicadores_oc73,
)

DATA = date(2026, 10, 12)


@pytest.mark.parametrize("estados,esperado", [
    ([], (0, 0, 0)),
    (["lancada", "ja_existia", "ignorado_limite"], (0, 0, 0)),
    (["incerto", "incerto"], (2, 0, 0)),
    (["bloqueado_reserva_existente", "bloqueado_transicao", "bloqueado_seguranca"], (0, 3, 0)),
    (["erro_consulta", "erro_persistencia", "erro_lancamento"], (0, 0, 3)),
    (["lancada", "incerto", "bloqueado_transicao", "erro_consulta"], (1, 1, 1)),
])
def test_classificacao(estados, esperado):
    resultado = calcular_indicadores_oc73([{"status": s} for s in estados])
    assert (resultado["total_incerto"], resultado["total_bloqueado"], resultado["total_erro_tecnico"]) == esperado


@pytest.mark.parametrize("estados,status_final,total_erros", [
    (["lancada", "ja_existia"], "concluido", 0),
    (["incerto"], "concluido_com_pendencias", 1),
    (["bloqueado_reserva_existente"], "concluido_com_pendencias", 0),
    (["bloqueado_seguranca"], "concluido_com_pendencias", 0),
    (["erro_consulta", "incerto", "bloqueado_transicao"], "concluido_com_pendencias", 2),
])
def test_fechamento_persistido(monkeypatch, tmp_path, estados, status_final, total_erros):
    monkeypatch.setattr("modules.ocorrencia_73.service.DRY_RUN", False)
    repo = MagicMock()
    repo.iniciar_execucao.return_value = 101
    service = Ocorrencia73Service(repository=repo)
    service.base_output_dir = tmp_path
    interno = MagicMock(return_value={
        "itens": [{"status": s} for s in estados],
        "total_relatorio": len(estados),
        "total_filtrado": len(estados),
        "total_lancado": estados.count("lancada"),
    })
    monkeypatch.setattr(service, "_executar_interno", interno)
    resultado = service.executar(DATA)
    assert resultado["execucao_id"] == 101
    assert resultado["total_incerto"] == estados.count("incerto")
    assert resultado["total_bloqueado"] == sum(s.startswith("bloqueado_") for s in estados)
    assert resultado["total_erro_tecnico"] == sum(s.startswith("erro_") for s in estados)
    repo.finalizar_execucao.assert_called_once()
    assert repo.finalizar_execucao.call_args.kwargs["status"] == status_final
    assert repo.finalizar_execucao.call_args.kwargs["total_erros"] == total_erros
