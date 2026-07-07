
import re 

from urllib.parse import unquote

from datetime import datetime, timedelta
from pathlib import Path

from operations.op156.queue import OP156Queue
from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import data_ddmmaa, dummy


class OP455Report:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self, unidade: str = "MTZ") -> None:
        self.client.open_option("455", unidade)

    def gerar_relatorio(
        self,
        data_inicial: datetime,
        data_final: datetime,
    ) -> str:
        payload = {
            "act": "E1",
            "cod_emp_ctb": "00",
            "f9": data_ddmmaa(data_inicial),
            "f10": data_ddmmaa(data_final),
            "f8": "T",
            "f18": "T",
            "f19": "T",
            "f20": "S",
            "f21": "X",
            "f22": "T",
            "f23": "A",
            "f25": "T",
            "f26": "A",
            "f27": "A",
            "f28": "T",
            "ibscbs": "A",
            "f29": "A",
            "f30": "A",
            "f35": "E",
            "f37": "B",
            "f38": "F",
            "basico": "N",
            "dummy": dummy(),
        }

        response = self.client.post("/bin/ssw0230", payload)
        return response.text
    
    def extrair_arquivo_direto(self, html: str) -> dict | None:
        decoded = unquote(html)

        match = re.search(
            r"abrir\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*\d+\s*,\s*\d+\s*,\s*'([^']+)'\s*,\s*(\d+)\s*\)",
            decoded,
            re.IGNORECASE,
        )

        if not match:
            return None

        return {
            "arquivo": match.group(1),
            "nome_download": match.group(2),
            "pasta": match.group(3),
            "tipo": match.group(4),
        }
    
    def baixar_arquivo_direto(
        self,
        info: dict,
        output_dir: Path,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)

        response = self.client.get(
            "/bin/ssw0424",
            params={
                "act": info["arquivo"],
                "filename": info["nome_download"],
                "path": info["pasta"],
                "down": "1",
                "nw": "1",
            },
        )

        response.raise_for_status()

        file_path = output_dir / info["nome_download"]

        file_path.write_bytes(response.content)

        return file_path
    
    def _extrair_nome_arquivo(self, headers) -> str | None:
        content_disposition = headers.get("Content-Disposition", "")

        match = re.search(r'filename="?([^"]+)"?', content_disposition)

        if match:
            return match.group(1)

        return None

    def gerar_e_baixar(
        self,
        output_dir: Path,
        dias_periodo: int = 7,
        timeout_seconds: int = 180,
    ) -> Path:
        hoje = datetime.now()
        inicio = hoje - timedelta(days=dias_periodo)

        self.open(unidade=self.client.unidade)
        html = self.gerar_relatorio(inicio, hoje)

        if "Informe a unidade" in html:
            raise ValueError("SSW retornou: Informe a unidade. Verifique a unidade usada para abrir a OP455.")

        info_direto = self.extrair_arquivo_direto(html)

        if info_direto:
            return self.baixar_arquivo_direto(
                info=info_direto,
                output_dir=output_dir,
            )

        fila = OP156Queue(self.client)

        return fila.baixar_por_opcao(
            output_dir=output_dir,
            opcao="455 - Fretes Expedidos/Recebidos - CTRCs",
            unidade=self.client.unidade,
            timeout_seconds=timeout_seconds,
            intervalo=5,
        )
    
    def gerar_e_baixar_por_datas(
        self,
        output_dir: Path,
        data_inicial: str,
        data_final: str,
        timeout_seconds: int = 300,
    ) -> Path:
        self.open()
        html = self.gerar_relatorio_por_datas(
            data_inicial=data_inicial,
            data_final=data_final,
        )

        if "Informe a unidade" in html:
            raise ValueError(
                "SSW retornou: Informe a unidade. Verifique a unidade usada na OP455."
            )

        info_direto = self.extrair_arquivo_direto(html)

        if info_direto:
            return self.baixar_arquivo_direto(
                info=info_direto,
                output_dir=output_dir,
            )

        fila = OP156Queue(self.client)

        return fila.baixar_por_opcao(
            output_dir=output_dir,
            opcao="455 - Fretes Expedidos/Recebidos - CTRCs",
            unidade="MTZ",
            timeout_seconds=timeout_seconds,
            intervalo=5,
        )
    
    def gerar_relatorio_por_datas(
        self,
        data_inicial: str,
        data_final: str,
    ) -> str:
        payload = {
            "act": "E1",
            "cod_emp_ctb": "00",

            "f9": data_inicial,
            "f10": data_final,

            "f8": "T",
            "f18": "T",
            "f19": "T",
            "f20": "S",
            "f21": "X",
            "f22": "T",
            "f23": "A",
            "f25": "T",
            "f26": "A",
            "f27": "A",
            "f28": "T",
            "ibscbs": "A",
            "f29": "A",
            "f30": "A",
            "f35": "E",
            "f37": "B",
            "f38": "F",
            "basico": "N",
            "dummy": dummy(),
        }

        response = self.client.post("/bin/ssw0230", payload)
        return response.text
    
        