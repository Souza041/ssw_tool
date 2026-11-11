from dataclasses import dataclass
from enum import StrEnum


class EstadoEnvio(StrEnum):
    PENDENTE = "PENDENTE"
    PROCESSANDO = "PROCESSANDO"
    ENVIADO = "ENVIADO"
    ERRO = "ERRO"
    VERIFICAR_ENVIO = "VERIFICAR_ENVIO"


class EstadoExecucao(StrEnum):
    PROCESSANDO = "PROCESSANDO"
    SIMULADO = "SIMULADO"
    ERRO = "ERRO"


@dataclass(frozen=True)
class ResumoTransporte:
    transporte: str
    destinatarios: tuple[str, ...]
    total_linhas: int
    pendentes: int
    ja_enviados: int
    erros: int

    def as_dict(self) -> dict:
        return {
            "transporte": self.transporte,
            "destinatarios": list(self.destinatarios),
            "total_linhas": self.total_linhas,
            "pendentes": self.pendentes,
            "ja_enviados": self.ja_enviados,
            "erros": self.erros,
        }
