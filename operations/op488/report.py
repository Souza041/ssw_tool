from pathlib import Path

from operations.op156.queue import OP156Queue
from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import dummy


class OP488Report:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self, unidade: str | None = None) -> None:
        unidade = unidade or settings.unidade

        self.client.post(
            "/bin/menu01",
            {
                "act": "TRO",
                "f2": unidade,
                "f3": "488",
                "dummy": dummy(),
            },
        )

        self.client.post(
            "/bin/ssw0099",
            {
                "sequencia": "488",
                "dummy": dummy(),
            },
        )

    def gerar_relatorio(
        self,
        cod_evento: str,
        evento: str,
        mes_comp: str,
        sit_desp: str = "X",
        sit_arq: str = "T",
    ) -> str:
        response = self.client.post(
            "/bin/ssw0099",
            {
                "act": "ARQ",
                "cod_emp_ctb": "00",
                "cod_evento": cod_evento,
                "evento": evento,
                "mes_comp": mes_comp,
                "sit_desp": sit_desp,
                "sit_arq": sit_arq,
                "dummy": dummy(),
            },
        )

        return response.text

    def gerar_e_baixar(
        self,
        output_dir: Path,
        cod_evento: str,
        evento: str,
        mes_comp: str,
        unidade: str | None = None,
        sit_desp: str = "X",
        sit_arq: str = "T",
        timeout_seconds: int = 300,
    ) -> Path:
        unidade = unidade or settings.unidade

        self.open(unidade=unidade)

        html = self.gerar_relatorio(
            cod_evento=cod_evento,
            evento=evento,
            mes_comp=mes_comp,
            sit_desp=sit_desp,
            sit_arq=sit_arq,
        )

        if "Solicita" not in html and "processamento" not in html:
            raise ValueError(f"OP488 não confirmou envio para fila 156. Retorno: {html[:500]}")

        fila = OP156Queue(self.client)

        return fila.aguardar_e_baixar(
            output_dir=output_dir,
            opcao_contains="488 - Consulta de Despesas - Parcial",
            unidade=unidade,
            timeout_seconds=timeout_seconds,
            intervalo=5,
        )