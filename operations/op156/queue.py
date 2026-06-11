import re
import time
from html import unescape
from pathlib import Path
from urllib.parse import unquote

from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import dummy


class OP156Queue:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def abrir_fila(self) -> str:
        response = self.client.post(
            "/bin/ssw1440",
            {"dummy": dummy()},
        )
        return response.text

    def limpar_html(self, value: str) -> str:
        value = unquote(unescape(value or ""))
        value = re.sub(r"<.*?>", "", value)
        return value.strip()

    def extrair_jobs(
        self,
        html: str,
        opcao_contains: str,
        unidade: str | None = None,
    ) -> list[dict]:
        html = unquote(unescape(html or ""))

        registros = re.findall(r"<r>(.*?)</r>", html, flags=re.I | re.S)

        jobs = []

        for registro in registros:
            campos = {}

            for idx in range(10):
                match = re.search(
                    rf"<f{idx}>(.*?)</f{idx}>",
                    registro,
                    flags=re.I | re.S,
                )

                campos[f"f{idx}"] = self.limpar_html(match.group(1)) if match else ""

            opcao = campos["f1"]
            usuario = campos["f3"]
            unidade_job = campos["f4"]
            situacao = campos["f6"]
            acao = campos["f8"]

            if opcao_contains not in opcao:
                continue

            if settings.usuario.lower() not in usuario.lower():
                continue

            if unidade and unidade_job.upper() != unidade.upper():
                continue

            jobs.append({
                "sequencia": campos["f0"],
                "opcao": opcao,
                "data_hora": campos["f2"],
                "usuario": usuario,
                "unidade": unidade_job,
                "situacao": situacao,
                "acao": acao,
                "download_id": campos["f0"],
            })

        return jobs

    def aguardar_download_id(
        self,
        opcao_contains: str,
        unidade: str | None = None,
        timeout_seconds: int = 300,
        intervalo: float = 5,
    ) -> str:
        deadline = time.time() + timeout_seconds
        tentativa = 0

        while time.time() < deadline:
            tentativa += 1

            html = self.abrir_fila()
            jobs = self.extrair_jobs(
                html=html,
                opcao_contains=opcao_contains,
                unidade=unidade,
            )

            if tentativa == 1 or tentativa % 5 == 0:
                print(f"[OP156] Aguardando {opcao_contains}... tentativa={tentativa}")

            if jobs:
                job = jobs[0]
                situacao = job["situacao"].lower()
                acao = job["acao"].lower()

                if "conclu" in situacao and "baixar" in acao:
                    print(f"[OP156] Relatório pronto para download: {job['download_id']}")
                    return job["download_id"]

            time.sleep(intervalo)

        raise TimeoutError(f"Relatório {opcao_contains} não ficou pronto dentro do tempo limite.")
    
    def extrair_arquivo_direto(self, html: str) -> dict | None:
        html = unquote(unescape(html or ""))

        match = re.search(
            r"abrir\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*\d+\s*,\s*\d+\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*(\d+)\s*\)",
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

    def baixar_arquivo(
        self,
        download_id: str,
        output_dir: Path,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)

        response_dow = self.client.post(
            "/bin/ssw1440",
            {
                "act": f"DOW{download_id}",
                "dummy": dummy(),
            },
        )

        response_dow.raise_for_status()

        info = self.extrair_arquivo_direto(response_dow.text)

        if not info:
            file_path = output_dir / f"ssw_{download_id}.sswweb"
            file_path.write_bytes(response_dow.content)
            return file_path

        response_file = self.client.get(
            "/bin/ssw0424",
            params={
                "act": info["arquivo"],
                "filename": info["nome_download"],
                "path": info["pasta"],
                "down": "1",
                "nw": "1",
            },
        )

        response_file.raise_for_status()

        file_path = output_dir / info["nome_download"]
        file_path.write_bytes(response_file.content)

        return file_path

    def aguardar_e_baixar(
        self,
        output_dir: Path,
        opcao_contains: str,
        unidade: str | None = None,
        timeout_seconds: int = 300,
        intervalo: float = 5,
    ) -> Path:
        download_id = self.aguardar_download_id(
            opcao_contains=opcao_contains,
            unidade=unidade,
            timeout_seconds=timeout_seconds,
            intervalo=intervalo,
        )

        return self.baixar_arquivo(
            download_id=download_id,
            output_dir=output_dir,
        )