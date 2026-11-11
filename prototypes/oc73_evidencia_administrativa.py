"""Contrato offline de evidência administrativa independente do laboratório."""

from dataclasses import dataclass
from datetime import datetime, timezone


class EvidenciaAdministrativaInvalida(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenciaAdministrativa:
    instancia_id: str
    responsavel: str
    porta_local: int
    processo_id: int
    executavel: str
    datadir: str
    volume_dedicado: str
    sem_replicacao_operacional: bool
    produto_origem: str
    versao: str
    conta_experimental: str
    escopo_privilegios: str
    emitida_em: datetime
    valida_ate: datetime
    revisao_pacote: str

    def validar(self, agora: datetime) -> None:
        textos = (
            self.instancia_id, self.responsavel, self.executavel, self.datadir,
            self.volume_dedicado, self.produto_origem, self.versao,
            self.conta_experimental, self.escopo_privilegios,
            self.revisao_pacote,
        )
        if any(not isinstance(item, str) or not item.strip() for item in textos):
            raise EvidenciaAdministrativaInvalida("Evidência administrativa incompleta.")
        if type(self.porta_local) is not int or self.porta_local != 3308:
            raise EvidenciaAdministrativaInvalida("Porta administrativa inválida.")
        if type(self.processo_id) is not int or self.processo_id <= 0:
            raise EvidenciaAdministrativaInvalida("Processo não comprovado.")
        if self.sem_replicacao_operacional is not True:
            raise EvidenciaAdministrativaInvalida("Ausência de replicação não comprovada.")
        if self.emitida_em.tzinfo is None or self.valida_ate.tzinfo is None:
            raise EvidenciaAdministrativaInvalida("Validade da evidência inválida.")
        instante = agora.astimezone(timezone.utc)
        if not self.emitida_em <= instante < self.valida_ate:
            raise EvidenciaAdministrativaInvalida("Evidência administrativa expirada.")
