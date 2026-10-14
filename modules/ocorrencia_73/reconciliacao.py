"""Reconciliação conservadora de OC73 por referência de tentativa.

Não lança ocorrências e nunca autoriza reenvio automático.
"""
from dataclasses import dataclass
from typing import Callable

from .gate import PortaoOC73


@dataclass(frozen=True)
class ResultadoReconciliacao:
    tentativa_id: int
    status: str
    mensagem: str
    confirmou: bool = False
    permite_reenvio: bool = False


class ReconciliadorOC73:
    ESTADOS_RECONCILIAVEIS = {"envio_iniciado", "incerto"}

    def __init__(self, repository, consultar_historico: Callable):
        self.repository = repository
        self.consultar_historico = consultar_historico

    @staticmethod
    def _encontrar_oc73(historico, referencia: str):
        if historico is None:
            raise RuntimeError("Histórico indisponível ou não carregado.")
        for ocorrencia in historico:
            if PortaoOC73._confirmacao_valida(ocorrencia, referencia):
                return ocorrencia
        return None

    def reconciliar(self, tentativa: dict) -> ResultadoReconciliacao:
        tentativa_id = tentativa["id"]
        status_atual = tentativa["status"]

        if status_atual == "confirmado":
            return ResultadoReconciliacao(
                tentativa_id, "confirmado", "Tentativa já confirmada.", confirmou=True
            )

        if status_atual not in self.ESTADOS_RECONCILIAVEIS:
            return ResultadoReconciliacao(
                tentativa_id, status_atual, "Estado não elegível para reconciliação."
            )

        # Não consultar nem confirmar com identificador inválido.
        if type(tentativa_id) is not int or tentativa_id <= 0:
            raise ValueError("ID da tentativa persistida inválido")

        referencia = tentativa.get("referencia")
        if not isinstance(referencia, str) or not referencia.startswith("OC73-"):
            return ResultadoReconciliacao(
                tentativa_id,
                status_atual,
                "Tentativa legada sem referência global; revisão manual obrigatória.",
            )
        serie = tentativa["serie"]
        numero = tentativa["numero"]

        try:
            historico = self.consultar_historico(
                serie=serie,
                numero=numero,
                data_inicial=tentativa["data_inicial_pesquisa"],
                data_final=tentativa["data_final_pesquisa"],
            )
            ocorrencia = self._encontrar_oc73(historico, referencia)
        except Exception as exc:
            return ResultadoReconciliacao(
                tentativa_id,
                status_atual,
                f"Falha na consulta ou validação do histórico: {type(exc).__name__}: {exc}",
            )

        if ocorrencia is None:
            return ResultadoReconciliacao(
                tentativa_id,
                status_atual,
                f"OC73 com referência {referencia} não localizada. "
                "Tentativa permanece bloqueada.",
            )

        try:
            self.repository.confirmar_lancamento(
                tentativa_id=tentativa_id,
                resposta_ssw=f"OC73 com referência {referencia} localizada no OP101.",
            )
        except RuntimeError:
            # A confirmação pode ter sido persistida por outra reconciliação.
            atual = self.repository.buscar_tentativa(serie=serie, numero=numero)
            if (
                atual is not None
                and atual["id"] == tentativa_id
                and atual["status"] == "confirmado"
            ):
                return ResultadoReconciliacao(
                    tentativa_id,
                    "confirmado",
                    "Tentativa confirmada por outra reconciliação concorrente.",
                    confirmou=True,
                )
            raise

        return ResultadoReconciliacao(
            tentativa_id,
            "confirmado",
            f"OC73 {referencia} localizada e confirmação persistida.",
            confirmou=True,
        )
