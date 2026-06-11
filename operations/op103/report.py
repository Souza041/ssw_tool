import re
from html import unescape
from pathlib import Path
from urllib.parse import unquote

from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import dummy


class OP103Report:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self, unidade: str | None = None) -> None:
        unidade = unidade or settings.unidade

        self.client.post(
            "/bin/menu01",
            {
                "act": "TRO",
                "f2": unidade,
                "f3": "103",
                "dummy": dummy(),
            },
        )

        self.client.post(
            "/bin/ssw0166",
            {
                "sequencia": "103",
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

    def baixar_arquivo_direto(self, info: dict, output_dir: Path) -> Path:
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

    def gerar_relatorio_devolucao(
        self,
        act: str,
        data_inicial: str,
        data_final: str,
        unidade_base: str,
        unidade_coleta: str,
        unidade_destinataria: str,
        mostrar_em: str = "e",
        por_data: str = "i",
    ) -> str:
        response = self.client.post(
            "/bin/ssw0166",
            {
                "act": act,
                "f2": "ROD",
                "f4": unidade_base,

                # Coletas normais
                "f14": data_inicial,
                "f15": data_final,
                "f16": por_data,
                "f17": mostrar_em,
                "f19": unidade_coleta,

                # Coletas devolução/reversa
                "f34": data_inicial,
                "f35": data_final,
                "f36": por_data,
                "f37": mostrar_em,
                "f39": unidade_coleta,
                "f43": unidade_destinataria,

                "dummy": dummy(),
            },
        )

        return response.text

    def gerar_e_baixar_devolucao(
        self,
        output_dir: Path,
        data_inicial: str,
        data_final: str,
        unidade_base: str = "CWB",
        unidade_coleta: str = "CWB",
        unidade_destinataria: str = "CWB",
        tipo_consulta: str = "coleta",
    ) -> Path:
        self.open(unidade=unidade_base)

        if tipo_consulta == "coleta":
            act = "ORI_DEV"
        elif tipo_consulta == "destinataria":
            act = "FIL_DEV"
        else:
            raise ValueError("tipo_consulta deve ser 'coleta' ou 'destinataria'.")

        html = self.gerar_relatorio_devolucao(
            act=act,
            data_inicial=data_inicial,
            data_final=data_final,
            unidade_base=unidade_base,
            unidade_coleta=unidade_coleta,
            unidade_destinataria=unidade_destinataria,
        )

        info = self.extrair_arquivo_direto(html)

        if not info:
            raise ValueError(f"OP103 não retornou arquivo direto. Retorno: {html[:500]}")

        return self.baixar_arquivo_direto(info, output_dir)