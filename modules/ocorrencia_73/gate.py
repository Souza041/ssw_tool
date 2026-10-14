"""Portão transacional da OC73. Não acessa o SSW diretamente.

Só o serviço deve fornecer os callbacks de envio e confirmação.
Nenhum estado incerto é reenviado automaticamente.
"""
from dataclasses import dataclass
from datetime import date
from typing import Callable


@dataclass(frozen=True)
class ResultadoPortao:
    status: str
    tentativa_id: int | None = None
    detalhe: str = ""


class PortaoOC73:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _codigo73(ocorrencia):
        if ocorrencia is None:
            return False
        codigo = (ocorrencia.get("codigo") if isinstance(ocorrencia, dict)
                  else getattr(ocorrencia, "codigo", None))
        return str(codigo).strip() == "73"

    def processar(
        self, *, execucao_id: int, serie: str, numero: str, fluxo: str,
        data_inicial: date, data_final: date,
        enviar: Callable, confirmar: Callable,
    ) -> ResultadoPortao:
        """Pré-condição: histórico inicial foi consultado e OC73 não existia.

        `confirmar` precisa retornar ocorrência com codigo=73, ou None.
        Um erro depois de `iniciar_envio` não permite novo POST.
        """
        if not isinstance(execucao_id, int) or execucao_id <= 0:
            raise ValueError("execucao_id persistido é obrigatório")
        if not callable(enviar) or not callable(confirmar):
            raise ValueError("callbacks de envio e confirmação obrigatórios")

        reserva = self.repository.reservar_ctrc(
            execucao_id=execucao_id, serie=serie, numero=numero,
            fluxo=fluxo, data_inicial=data_inicial, data_final=data_final,
        )
        tentativa_id = reserva["id"]
        if (not reserva.get("reserva_criada")
                or reserva.get("status") != "reservado"
                or reserva.get("execucao_id") != execucao_id):
            return ResultadoPortao("bloqueado_reserva_existente", tentativa_id)

        # Commit no banco deve ocorrer antes do callback de envio.
        if not self.repository.iniciar_envio(tentativa_id, execucao_id):
            return ResultadoPortao("bloqueado_transicao", tentativa_id)

        try:
            resposta = enviar()
            # Consultar mesmo quando a resposta do POST indica erro:
            # pode ter havido gravação antes de uma falha de resposta.
            ocorrencia = confirmar()
            if not self._codigo73(ocorrencia):
                raise RuntimeError("OC73 não confirmada no histórico OP101")
            self.repository.confirmar_lancamento(
                tentativa_id=tentativa_id,
                resposta_ssw=str(getattr(resposta, "mensagem", ""))[:2000],
            )
            return ResultadoPortao("confirmado", tentativa_id)
        except Exception as exc:
            # Se a confirmação foi persistida mas a resposta se perdeu,
            # marcar_incerto pode falhar: não disfarçar essa falha.
            try:
                self.repository.marcar_incerto(
                    tentativa_id=tentativa_id,
                    mensagem_erro=f"{type(exc).__name__}: {exc}"[:2000],
                )
            except Exception as persistencia_exc:
                raise RuntimeError(
                    "Estado de envio não pôde ser persistido; intervenção manual necessária"
                ) from persistencia_exc
            return ResultadoPortao("incerto", tentativa_id, str(exc))
