"""Portão transacional da OC73 com confirmação por referência."""
from dataclasses import dataclass
from datetime import date
from typing import Callable

from modules.ocorrencia_73.referencia import (
    referencia_valida,
    ocorrencia_confirma_referencia,
)


@dataclass(frozen=True)
class ResultadoPortao:
    status: str
    tentativa_id: int | None = None
    detalhe: str = ""


class PortaoOC73:
    def __init__(self, repository):
        self.repository = repository

    @classmethod
    def _confirmacao_valida(cls, ocorrencia, referencia):
        return ocorrencia_confirma_referencia(
            ocorrencia,
            referencia,
        )

    def processar(
        self, *, execucao_id: int, serie: str, numero: str,
        fluxo: str, data_inicial: date, data_final: date,
        enviar: Callable, confirmar: Callable,
    ) -> ResultadoPortao:

        if not isinstance(execucao_id, int) or execucao_id <= 0:
            raise ValueError("execucao_id persistido é obrigatório")

        if not callable(enviar) or not callable(confirmar):
            raise ValueError("Callbacks obrigatórios")

        reserva = self.repository.reservar_ctrc(
            execucao_id=execucao_id,
            serie=serie,
            numero=numero,
            fluxo=fluxo,
            data_inicial=data_inicial,
            data_final=data_final,
        )

        tentativa_id = reserva["id"]

        if (
            not reserva.get("reserva_criada")
            or reserva.get("status") != "reservado"
            or reserva.get("execucao_id") != execucao_id
        ):
            return ResultadoPortao(
                "bloqueado_reserva_existente", tentativa_id
            )

        referencia = reserva.get("referencia")

        if not referencia_valida(referencia):
            return ResultadoPortao(
                "bloqueado_sem_referencia",
                tentativa_id,
            )

        if not self.repository.iniciar_envio(
            tentativa_id, execucao_id
        ):
            return ResultadoPortao(
                "bloqueado_transicao", tentativa_id
            )

        try:
            resposta = enviar(referencia)
            ocorrencia = confirmar(referencia)

            if not self._confirmacao_valida(
                ocorrencia, referencia
            ):
                raise RuntimeError(
                    "OC73 sem referência correspondente no histórico"
                )

            self.repository.confirmar_lancamento(
                tentativa_id=tentativa_id,
                resposta_ssw=str(
                    getattr(resposta, "mensagem", "")
                )[:2000],
            )

            return ResultadoPortao(
                "confirmado", tentativa_id
            )

        except Exception as exc:
            try:
                self.repository.marcar_incerto(
                    tentativa_id=tentativa_id,
                    mensagem_erro=(
                        f"{type(exc).__name__}: {exc}"
                    )[:2000],
                )
            except Exception as persistencia_exc:
                raise RuntimeError(
                    "Estado de envio não pôde ser persistido; "
                    "intervenção manual necessária"
                ) from persistencia_exc

            return ResultadoPortao(
                "incerto", tentativa_id, str(exc)
            )
