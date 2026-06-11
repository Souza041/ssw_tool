import re
from html import unescape
from pathlib import Path
from urllib.parse import unquote

from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import dummy


class OP150Report:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self, unidade: str | None = None) -> None:
        unidade = unidade or settings.unidade

        self.client.post(
            "/bin/menu01",
            {
                "act": "TRO",
                "f2": unidade,
                "f3": "150",
                "dummy": dummy(),
            },
        )

        self.client.post(
            "/bin/ssw0861",
            {
                "sequencia": "150",
                "dummy": dummy(),
            },
        )

    def extrair_arquivo_direto(self, html: str) -> dict | None:
        html = unquote(unescape(html or ""))

        match = re.search(
            r"abrir\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*\d+\s*,\s*\d+\s*,\s*['\"]([^'\"]*)['\"]\s*,\s*(\d+)\s*\)",
            html,
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

        filename = self._extrair_nome_arquivo(response.headers) or info["nome_download"]

        file_path = output_dir / filename
        file_path.write_bytes(response.content)

        return file_path

    def _extrair_nome_arquivo(self, headers) -> str | None:
        content_disposition = headers.get("Content-Disposition", "")

        match = re.search(r'filename="?([^"]+)"?', content_disposition)

        if match:
            return match.group(1)

        return None

    def gerar_relatorio(
        self,
        data_inicial: str,
        data_final: str,
        unidade: str,
        nome_unidade: str,
        f7: str = "R",
        f8: str = "s",
        f9: str = "N",
    ) -> str:
        response = self.client.post(
            "/bin/ssw0861",
            {
                "act": "ENV",
                "f1": data_inicial,
                "f2": data_final,
                "f6": unidade,
                "unidade6": nome_unidade,
                "f7": f7,
                "f8": f8,
                "f9": f9,
                "dummy": dummy(),
            },
        )

        return response.text

    def gerar_e_baixar(
        self,
        output_dir: Path,
        data_inicial: str,
        data_final: str,
        unidade: str = "CWB",
        nome_unidade: str = "RODOBRAS TRANSP RODOVIARIOS",
        f7: str = "R",
        f8: str = "s",
        f9: str = "N",
    ) -> Path:
        self.open(unidade=unidade)

        html = self.gerar_relatorio(
            data_inicial=data_inicial,
            data_final=data_final,
            unidade=unidade,
            nome_unidade=nome_unidade,
            f7=f7,
            f8=f8,
            f9=f9,
        )

        info = self.extrair_arquivo_direto(html)

        if not info:
            raise ValueError(f"OP150 não retornou arquivo direto. Retorno: {html[:500]}")

        return self.baixar_arquivo_direto(
            info=info,
            output_dir=output_dir,
        )