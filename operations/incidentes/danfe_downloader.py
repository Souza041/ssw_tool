import html
import re
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from ssw.client import SSWClient

class XMLCTeDownloader:
    PREFIXO_ARQUIVO = "000800"

    def __init__(
        self,
        client: SSWClient,
        output_dir: Path | str,
    ):
        self.client = client
        self.output_dir = Path(output_dir)

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    @staticmethod
    def normalizar_sequencial(
        sequencial: str,
    ) -> str:
        texto = str(sequencial or "").strip()

        texto = re.sub(
            r"\D",
            "",
            texto,
        )

        if not texto:
            raise ValueError(
                "Sequencial do CTRC não informado."
            )

        return texto

    @classmethod
    def montar_nome_requisicao(
        cls,
        sequencial: str,
    ) -> str:
        sequencial_normalizado = (
            cls.normalizar_sequencial(sequencial)
        )

        return (
            f"CTe_{cls.PREFIXO_ARQUIVO}"
            f"{sequencial_normalizado}.zip"
        )

    @staticmethod
    def sanitizar_nome_arquivo(
        nome: str,
    ) -> str:
        nome = Path(
            str(nome or "").strip()
        ).name

        nome = re.sub(
            r'[<>:"/\\|?*]',
            "_",
            nome,
        )

        return nome

    @staticmethod
    def extrair_nome_resposta(
        response,
        nome_padrao: str,
    ) -> str:
        content_disposition = response.headers.get(
            "content-disposition",
            "",
        )

        match = re.search(
            r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)',
            content_disposition,
            flags=re.IGNORECASE,
        )

        if match:
            nome = match.group(1).strip()

            if nome:
                return XMLCTeDownloader.sanitizar_nome_arquivo(
                    nome
                )

        return XMLCTeDownloader.sanitizar_nome_arquivo(
            nome_padrao
        )

    @staticmethod
    def validar_resposta(
        response,
        sequencial: str,
    ) -> None:
        if response.status_code != 200:
            raise ValueError(
                "Falha ao baixar ZIP do CT-e "
                f"{sequencial}. HTTP {response.status_code}."
            )

        conteudo = response.content

        if not conteudo:
            raise ValueError(
                "O SSW retornou um arquivo vazio para o "
                f"sequencial {sequencial}."
            )

        content_type = (
            response.headers.get("content-type", "")
            .lower()
        )

        inicio = conteudo[:200].lower()

        resposta_html = (
            "text/html" in content_type
            or b"<html" in inicio
            or b"<!doctype html" in inicio
        )

        if resposta_html:
            raise ValueError(
                "O SSW retornou uma página HTML em vez do ZIP "
                f"para o sequencial {sequencial}. "
                "A sessão pode ter expirado ou o documento "
                "pode não estar disponível."
            )

    @staticmethod
    def separar_ctrc(
        ctrc: str,
    ) -> tuple[str, str]:
        texto = str(ctrc or "").strip().upper()

        match = re.match(
            r"^([A-Z]{3})(\d+)",
            texto,
        )

        if not match:
            raise ValueError(
                f"Formato de CTRC inválido: {ctrc}"
            )

        serie = match.group(1)
        numero = match.group(2)

        return serie, numero


    @staticmethod
    def data_ssw(
        valor: datetime,
    ) -> str:
        return valor.strftime("%d/%m/%y")


    @staticmethod
    def extrair_nome_zip_gerado(
        response,
        nome_padrao: str,
    ) -> str:
        texto = response.text or ""

        texto = html.unescape(texto)
        texto = unquote(texto)

        padroes = [
            r'CTe_\d+\.zip',
            r'abrir\(\s*["\']([^"\']+\.zip)',
            r'name=["\']web_body["\'][^>]+value=["\'][^"\']*'
            r'(CTe_\d+\.zip)',
        ]

        for padrao in padroes:
            match = re.search(
                padrao,
                texto,
                flags=re.IGNORECASE,
            )

            if not match:
                continue

            if match.lastindex:
                nome = match.group(1)
            else:
                nome = match.group(0)

            nome = str(nome).strip()

            if nome:
                return Path(nome).name

        return nome_padrao

    def preparar_zip(
        self,
        ctrc: str,
        sequencial: str,
    ) -> str:
        serie, numero = self.separar_ctrc(ctrc)

        hoje = datetime.now()
        inicio = hoje - timedelta(days=90)

        sequencial_normalizado = (
            self.normalizar_sequencial(sequencial)
        )

        nome_padrao = self.montar_nome_requisicao(
            sequencial_normalizado
        )

        response = self.client.post(
            "/bin/ssw0053",
            data={
                "act": "XML",
                "aviso_resgate": "#aviso_resgate#",
                "dd_f_t_data_ini": "",
                "dd_f_t_data_fin": "",
                "dd_f_t_ser_ctrc": "",
                "dd_f_t_ser_nf": "",
                "dd_f_t_nro_pedido": "",
                "g_ctrc_ser_ctrc": serie,
                "g_ctrc_nro_ctrc": numero,
                "gw_nro_nf_ini": "0",
                "g_ctrc_nf_vol_ini": "0",
                "gw_ctrc_nr_sscc": "",
                "g_ctrc_nro_ctl_form": "0",
                "gw_ctrc_parc_nro_ctrc_parc": "0",
                "g_ctrc_c_chave_fis": "",
                "gw_gaiola_codigo": "0",
                "gw_pallet_codigo": "0",
                "local": "Q",
                "data_ini_inf": self.data_ssw(inicio),
                "data_fin_inf": self.data_ssw(hoje),
                "seq_ctrc": sequencial_normalizado,
                "FAMILIA": "ROD",
                "dummy": str(int(time.time() * 1000)),
            },
        )

        if response.status_code != 200:
            raise ValueError(
                "Falha ao preparar ZIP do CT-e "
                f"{ctrc}. HTTP {response.status_code}."
            )

        nome_gerado = self.extrair_nome_zip_gerado(
            response=response,
            nome_padrao=nome_padrao,
        )

        return nome_gerado

    def baixar(
        self,
        sequencial: str,
        ctrc: str | None = None,
        sobrescrever: bool = False,
    ) -> Path:
        sequencial_normalizado = (
            self.normalizar_sequencial(sequencial)
        )

        if not ctrc:
            raise ValueError(
                "CTRC é obrigatório para preparar o ZIP XML."
            )

        nome_requisicao = self.preparar_zip(
            ctrc=ctrc,
            sequencial=sequencial_normalizado,
        )

        response = self.client.get(
            "/bin/ssw0424",
            params={
                "act": nome_requisicao,
                "filename": nome_requisicao,
                "path": "binary",
                "down": "1",
                "nw": "1",
            },
        )

        self.validar_resposta(
            response=response,
            sequencial=sequencial_normalizado,
        )

        nome_resposta = self.extrair_nome_resposta(
            response=response,
            nome_padrao=nome_requisicao,
        )

        ctrc_seguro = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            str(ctrc).strip(),
        )

        nome_saida = (
            f"{ctrc_seguro}__{nome_resposta}"
        )

        caminho_saida = (
            self.output_dir
            / self.sanitizar_nome_arquivo(nome_saida)
        )

        if caminho_saida.exists() and not sobrescrever:
            if zipfile.is_zipfile(caminho_saida):
                return caminho_saida

            caminho_saida.unlink()

        caminho_temporario = caminho_saida.with_suffix(
            caminho_saida.suffix + ".part"
        )

        caminho_temporario.write_bytes(
            response.content
        )

        if not zipfile.is_zipfile(caminho_temporario):
            caminho_temporario.unlink(
                missing_ok=True
            )

            raise ValueError(
                "O arquivo retornado pelo SSW não é um ZIP "
                f"válido para o CTRC {ctrc}."
            )

        caminho_temporario.replace(
            caminho_saida
        )

        return caminho_saida