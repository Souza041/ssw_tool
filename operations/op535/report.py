import html
import re
from pathlib import Path
from urllib.parse import unquote

from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import dummy


class OP535Report:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self, unidade: str | None = None) -> None:
        unidade = unidade or settings.unidade

        self.client.post(
            "/bin/menu01",
            {
                "act": "TRO",
                "f2": unidade,
                "f3": "535",
                "dummy": dummy(),
            },
        )

        self.client.post(
            "/bin/ssw0323",
            {
                "sequencia": "535",
                "dummy": dummy(),
            },
        )

    def gerar_relatorio(self) -> str:
        response = self.client.post(
            "/bin/ssw0323",
            {
                "act": "E",
                "fg_atividade": "S",
                "conta_corrente": "A",
                "excel": "S",
                "dummy": dummy(),
            },
        )

        return response.text

    @staticmethod
    def extrair_download(html_response: str) -> tuple[str, str]:
        """
        Extrai do campo hidden `web_body` os argumentos enviados
        para a função JavaScript abrir():

            abrir(
                'dhionata092257ssw0323.csv',
                'CSVssw0323.sswweb',
                1,
                1,
                '',
                4
            );

        Retorna:
            (arquivo_interno, nome_download)
        """

        match = re.search(
            r'name=["\']?web_body["\']?[^>]*value=["\']([^"\']+)["\']',
            html_response,
            flags=re.IGNORECASE,
        )

        if not match:
            raise ValueError(
                "OP535 gerou o relatório, mas o campo "
                "web_body não foi encontrado."
            )

        web_body = html.unescape(
            unquote(match.group(1))
        )

        match_abrir = re.search(
            r"""abrir\s*\(\s*
                ['"]([^'"]+)['"]\s*,\s*
                ['"]([^'"]+)['"]
            """,
            web_body,
            flags=re.IGNORECASE | re.VERBOSE,
        )

        if not match_abrir:
            raise ValueError(
                "Campo web_body encontrado, mas não foi "
                f"possível interpretar abrir(). Retorno: {web_body[:300]}"
            )

        arquivo_interno = match_abrir.group(1).strip()
        nome_download = match_abrir.group(2).strip()

        return arquivo_interno, nome_download

    def baixar_relatorio(
        self,
        arquivo_interno: str,
        nome_download: str,
        output_dir: Path,
    ) -> Path:
        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        response = self.client.get(
            "/bin/ssw0424",
            params={
                "act": arquivo_interno,
                "filename": nome_download,
                "path": "",
                "down": "1",
                "nw": "1",
            },
        )

        if response.status_code != 200:
            raise ValueError(
                "Falha ao baixar relatório OP535. "
                f"HTTP {response.status_code}."
            )

        if not response.content:
            raise ValueError(
                "Download da OP535 retornou arquivo vazio."
            )

        destino = (
            output_dir
            / "OP535_FORNECEDORES.sswweb"
        )

        destino.write_bytes(
            response.content
        )

        return destino

    def gerar_e_baixar(
        self,
        output_dir: Path,
        unidade: str | None = None,
        timeout_seconds: int = 300,
    ) -> Path:
        # Mantido por compatibilidade com o workflow.
        _ = timeout_seconds

        self.open(
            unidade=unidade
        )

        html_response = self.gerar_relatorio()

        if not html_response:
            raise ValueError(
                "OP535 não retornou resposta "
                "ao gerar relatório."
            )

        arquivo_interno, nome_download = (
            self.extrair_download(
                html_response
            )
        )

        return self.baixar_relatorio(
            arquivo_interno=arquivo_interno,
            nome_download=nome_download,
            output_dir=output_dir,
        )