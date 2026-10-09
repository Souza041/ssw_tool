
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ResultadoReconciliacao:
    tentativa_id: int
    status: str
    mensagem: str
    confirmou: bool = False
    permite_reenvio: bool = False


class ReconciliadorOC73:
    """
    Reconciliação conservadora de tentativas OC73.

    Não realiza lançamentos.
    Não libera reenvio automático.
    """

    ESTADOS_RECONCILIAVEIS = {
        "envio_iniciado",
        "incerto",
    }

    def __init__(
        self,
        repository,
        consultar_historico: Callable,
    ):
        self.repository = repository
        self.consultar_historico = consultar_historico

    @staticmethod
    def _campo(objeto, nome, padrao=None):
        if isinstance(objeto, dict):
            return objeto.get(nome, padrao)

        return getattr(objeto, nome, padrao)

    def _encontrar_oc73(self, historico):
        if historico is None:
            raise RuntimeError(
                "Histórico indisponível ou não carregado."
            )

        for ocorrencia in historico:
            codigo = self._campo(
                ocorrencia,
                "codigo",
            )

            if str(codigo).strip() == "73":
                return ocorrencia

        return None

    def reconciliar(self, tentativa: dict):
        tentativa_id = tentativa["id"]
        status_atual = tentativa["status"]

        if status_atual == "confirmado":
            return ResultadoReconciliacao(
                tentativa_id=tentativa_id,
                status="confirmado",
                mensagem="Tentativa já confirmada.",
                confirmou=True,
            )

        if status_atual not in self.ESTADOS_RECONCILIAVEIS:
            return ResultadoReconciliacao(
                tentativa_id=tentativa_id,
                status=status_atual,
                mensagem=(
                    "Estado não elegível para reconciliação."
                ),
            )

        serie = tentativa["serie"]
        numero = tentativa["numero"]

        try:
            historico = self.consultar_historico(
                serie=serie,
                numero=numero,
                data_inicial=tentativa[
                    "data_inicial_pesquisa"
                ],
                data_final=tentativa[
                    "data_final_pesquisa"
                ],
            )

            ocorrencia = self._encontrar_oc73(
                historico
            )

        except Exception as exc:
            return ResultadoReconciliacao(
                tentativa_id=tentativa_id,
                status=status_atual,
                mensagem=(
                    "Falha na consulta do histórico: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        if ocorrencia is None:
            return ResultadoReconciliacao(
                tentativa_id=tentativa_id,
                status=status_atual,
                mensagem=(
                    "OC73 não localizada. "
                    "Tentativa permanece bloqueada."
                ),
            )

        # A confirmação é persistida somente após
        # encontrar a OC73 no histórico consultado.
        self.repository.confirmar_lancamento(
            tentativa_id=tentativa_id,
            resposta_ssw=(
                "OC73 localizada no histórico OP101."
            ),
        )

        return ResultadoReconciliacao(
            tentativa_id=tentativa_id,
            status="confirmado",
            mensagem=(
                "OC73 localizada e confirmação persistida."
            ),
            confirmou=True,
        )
