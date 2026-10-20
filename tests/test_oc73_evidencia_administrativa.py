from datetime import datetime, timedelta, timezone

import pytest

from prototypes.oc73_evidencia_administrativa import (
    EvidenciaAdministrativa,
    EvidenciaAdministrativaInvalida,
)


AGORA = datetime(2026, 10, 16, 18, tzinfo=timezone.utc)


def evidencia(**mudancas):
    dados = {
        "instancia_id": "preencher-na-autorizacao",
        "responsavel": "responsavel-autorizado",
        "porta_local": 3308,
        "processo_id": 1234,
        "executavel": "C:/mysql57/bin/mysqld.exe",
        "datadir": "C:/mysql57-lab/data/",
        "volume_dedicado": "volume-lab",
        "sem_replicacao_operacional": True,
        "produto_origem": "pacote-administrativo-verificado",
        "versao": "5.7.32",
        "conta_experimental": "oc73_tester@127.0.0.1",
        "escopo_privilegios": "perfil-transacional-revisado",
        "emitida_em": AGORA - timedelta(minutes=1),
        "valida_ate": AGORA + timedelta(hours=1),
        "revisao_pacote": "abcdef1234567890",
    }
    dados.update(mudancas)
    return EvidenciaAdministrativa(**dados)


def test_evidencia_administrativa_completa_e_valida():
    evidencia().validar(AGORA)


@pytest.mark.parametrize(
    "mudancas",
    [
        {"instancia_id": ""},
        {"processo_id": 0},
        {"porta_local": 3306},
        {"sem_replicacao_operacional": False},
        {"valida_ate": AGORA},
        {"revisao_pacote": ""},
    ],
)
def test_evidencia_incompleta_expirada_ou_sem_isolamento_falha(mudancas):
    with pytest.raises(EvidenciaAdministrativaInvalida):
        evidencia(**mudancas).validar(AGORA)
