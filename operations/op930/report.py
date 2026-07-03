from pathlib import Path

from operations.op156.queue import OP156Queue, RelatorioSemDados
from ssw.client import SSWClient
from ssw.utils import dummy


class OP930Report:
    def __init__(self, client: SSWClient):
        self.client = client
        self.op156 = OP156Queue(client)

    def consultar_cliente(self, cnpj: str) -> str:
        response = self.client.get(
            "/bin/ssw0846",
            params={
                "get_cliente": cnpj,
                "dummy": dummy(),
            },
        )

        texto = response.text.strip()

        if not texto:
            return ""

        return texto

    def solicitar_relatorio(
        self,
        data_inicial: str,
        data_final: str,
        cnpj: str,
        nome_cliente: str,
    ) -> None:
        self.client.post(
            "/bin/ssw0846",
            {
                "act": "ENV",
                "f1": data_inicial,
                "f2": data_final,
                "f3": "T",
                "f8": cnpj,
                "cliente": nome_cliente,
                "f9": "G",
                "dummy": dummy(),
            },
        )

    def gerar_e_baixar_por_cnpj(
        self,
        output_dir: Path,
        data_inicial: str,
        data_final: str,
        cnpj: str,
        grupo: str,
        timeout_seconds: int = 300,
    ) -> Path:
        nome_cliente = self.consultar_cliente(cnpj)

        if not nome_cliente:
            raise ValueError(f"Cliente não encontrado para CNPJ {cnpj}")
        
        html_antes = self.op156.abrir_fila()

        jobs_antes = self.op156.extrair_jobs(
            html=html_antes,
            opcao_contains="930",
        )

        ignorar_ids = {
            job["download_id"]
            for job in jobs_antes
        }

        self.solicitar_relatorio(
            data_inicial=data_inicial,
            data_final=data_final,
            cnpj=cnpj,
            nome_cliente=nome_cliente,
        )

        arquivo = self.op156.baixar_por_opcao(
            output_dir=output_dir,
            opcao="930",
            timeout_seconds=timeout_seconds,
            ignorar_ids=ignorar_ids,
        )

        novo_nome = output_dir / f"OP930_{grupo}_{cnpj}_{arquivo.name}"
        arquivo.rename(novo_nome)

        return novo_nome