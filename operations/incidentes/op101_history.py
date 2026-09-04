import re
import unicodedata
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import unquote

from ssw.client import SSWClient
from ssw.utils import dummy


class OP101History:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    @staticmethod
    def split_ctrc(serie_numero: str) -> tuple[str, str]:
        texto = str(serie_numero or "").strip().upper()

        # Aceita:
        # JOI813812-5
        # JOI 813812
        # JOI813812
        match = re.search(
            r"([A-Z]{2}[A-Z0-9])\D*(\d{6})(?:\D*\d)?",
            texto,
        )

        if not match:
            raise ValueError(
                f"Formato de CTRC inválido: {serie_numero}"
            )

        return match.group(1), match.group(2)

    @staticmethod
    def data_ssw(data: datetime) -> str:
        return data.strftime("%d%m%y")

    def consultar_sequencial(
        self,
        serie: str,
        numero_ctrc: str,
        dias_busca: int = 730,
    ) -> str:
        hoje = datetime.now()
        data_inicial = hoje - timedelta(days=dias_busca)

        response = self.client.get(
            "/bin/ssw0385",
            params={
                "dd_select": "ctrc",
                "dd_chave": numero_ctrc,
                "dd_f_t_ser_ctrc": serie,
                "dd_f_t_data_ini": self.data_ssw(data_inicial),
                "dd_f_t_data_fin": self.data_ssw(hoje),
                "dummy": dummy(),
            },
        )

        sequencial = ""

        # Primeira tentativa: resposta JSON normal
        try:
            data = response.json()
            sequencial = str(
                data.get("sequencial", "")
            ).strip()
        except Exception:
            pass

        # Segunda tentativa: procura direto no texto da resposta
        if not sequencial:
            match = re.search(
                r'"sequencial"\s*:\s*"([^"]+)"',
                response.text,
            )

            if match:
                sequencial = match.group(1).strip()

        if not sequencial:
            raise ValueError(
                f"Sequencial não encontrado para CTRC "
                f"{serie}{numero_ctrc}"
            )

        return sequencial

    def abrir_ctrc(
        self,
        serie: str,
        numero: str,
        sequencial: str,
    ) -> str:
        hoje = datetime.now()
        data_inicial = hoje - timedelta(days=730)

        response = self.client.post(
            "/bin/ssw0053",
            {
                "act": "P1",
                "t_ser_ctrc": serie,
                "t_nro_ctrc": numero,
                "t_data_ini": self.data_ssw(data_inicial),
                "t_data_fin": self.data_ssw(hoje),
                "seq_ctrc": sequencial,
                "local": "",
                "FAMILIA": "ROD",
                "dummy": dummy(),
            },
        )

        return response.text

    def abrir_ocorrencias(
        self,
        numero: str,
        sequencial: str,
    ) -> str:
        hoje = datetime.now()
        data_inicial = hoje - timedelta(days=730)

        response = self.client.post(
            "/bin/ssw0053",
            {
                "act": "O",
                "g_ctrc_nro_ctrc": numero,
                "seq_ctrc": sequencial,
                "FAMILIA": "ROD",
                "data_ini_inf": data_inicial.strftime("%d/%m/%y"),
                "data_fin_inf": hoje.strftime("%d/%m/%y"),
                "dummy": dummy(),
            },
        )

        return response.text

    @staticmethod
    def limpar_html(valor: str) -> str:
        valor = unquote(unescape(valor or ""))
        valor = re.sub(r"<br\s*/?>", " ", valor, flags=re.I)
        valor = re.sub(r"<[^>]+>", " ", valor)
        valor = re.sub(r"\s+", " ", valor)
        return valor.strip()

    @staticmethod
    def identificar_tipo_operacao(html: str) -> str:
        texto = unquote(unescape(html or ""))
        texto = re.sub(r"<[^>]+>", " ", texto)
        texto = re.sub(r"\s+", " ", texto)
        texto = texto.upper().strip()

        texto = unicodedata.normalize(
            "NFKD",
            texto,
        ).encode(
            "ASCII",
            "ignore",
        ).decode(
            "ASCII",
        )

        if not texto:
            return "NAO_IDENTIFICADO"

        indicadores_reversa = (
            "CT-E REVERSA",
            "CTE REVERSA",
            "DEVOLUCAO (DEVOLUCAO)",
            "DEVOLUCAO DO CTRC",
            "CTRC GERADO COMO DEVOLUCAO",
        )

        if any(
            indicador in texto
            for indicador in indicadores_reversa
        ):
            return "REVERSA"

        return "COLETA"

    @staticmethod
    def normalizar_codigo_ocorrencia(valor: str) -> str:
        texto = str(valor or "").strip()

        # Exemplo:
        # "99 - ATUALIZACAO"
        # "47 - CTE EMITIDO"
        match = re.search(r"\b(\d{1,3})\b", texto)

        if not match:
            return ""

        return match.group(1).lstrip("0") or "0"

    @staticmethod
    def separar_codigo_descricao(valor: str) -> tuple[str, str]:
        texto = str(valor or "").strip()

        codigo = OP101History.normalizar_codigo_ocorrencia(texto)

        descricao = re.sub(
            r"^\s*0*\d{1,3}\s*[-–:]?\s*",
            "",
            texto,
        ).strip()

        return codigo, descricao

    @staticmethod
    def converter_data_hora(valor: str) -> datetime | None:
        texto = str(valor or "").strip()

        formatos = [
            "%d/%m/%y %H:%M",
            "%d/%m/%Y %H:%M",
            "%d/%m/%y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
        ]

        for formato in formatos:
            try:
                return datetime.strptime(texto, formato)
            except ValueError:
                continue

        return None

    def extrair_linhas_xml(self, html: str) -> list[dict]:
        decoded = unquote(unescape(html or ""))

        linhas_xml = re.findall(
            r"<r>(.*?)</r>",
            decoded,
            flags=re.I | re.S,
        )

        registros = []

        for linha_xml in linhas_xml:
            campos = {}

            matches = re.findall(
                r"<f(\d+)>(.*?)</f\d+>",
                linha_xml,
                flags=re.I | re.S,
            )

            for indice, valor in matches:
                campos[f"f{indice}"] = self.limpar_html(valor)

            if campos:
                registros.append(campos)

        return registros

    def mapear_ocorrencia(self, campos: dict) -> dict | None:
        inclusao = campos.get("f0", "")
        dominio = campos.get("f1", "")
        filial = campos.get("f2", "")
        inclusao_local = campos.get("f3", "")
        usuario = campos.get("f4", "")
        ocorrencia_texto = campos.get("f5", "")
        complemento = campos.get("f6", "")

        # O usuário pode vir como:
        # #u#pacheco|8#/u#
        usuario = re.sub(
            r"#u#(.*?)\|\d+#/u#",
            r"\1",
            usuario,
            flags=re.I,
        )

        usuario = self.limpar_html(usuario)

        codigo, descricao = self.separar_codigo_descricao(
            ocorrencia_texto
        )

        # Existem linhas de instrução/complemento sem código de ocorrência.
        # Elas devem continuar no histórico, pois ajudam a entender a evolução.
        if not ocorrencia_texto and not complemento:
            return None

        data_hora_local = self.converter_data_hora(
            inclusao_local
        )

        data_hora_inclusao = self.converter_data_hora(
            inclusao
        )

        return {
            "inclusao": inclusao,
            "inclusao_dt": data_hora_inclusao,

            "dominio": dominio,
            "filial": filial,

            "data_hora": inclusao_local or inclusao,
            "data_hora_dt": (
                data_hora_local
                or data_hora_inclusao
            ),

            "usuario": usuario,

            "codigo": codigo,
            "descricao": descricao,
            "ocorrencia_original": ocorrencia_texto,
            "complemento": complemento,

            "detalhe": campos.get("f7", ""),
            "documentos": campos.get("f8", ""),
            "imagem": campos.get("f9", ""),
            "ocorrencia_ssw": campos.get("f10", ""),
            "conferentes": campos.get("f11", ""),
            "sequencia": campos.get("f12", ""),

            "raw": campos,
        }

    def extrair_historico(self, html: str) -> list[dict]:
        historico = []

        for campos in self.extrair_linhas_xml(html):
            ocorrencia = self.mapear_ocorrencia(campos)

            if ocorrencia:
                historico.append(ocorrencia)

        historico.sort(
            key=lambda item: (
                item.get("data_hora_dt") is not None,
                item.get("data_hora_dt") or datetime.min,
            ),
            reverse=True,
        )

        return historico

    def consultar_historico(
        self,
        serie_numero_ctrc: str,
        limite: int = 5,
    ) -> dict:
        serie, numero = self.split_ctrc(serie_numero_ctrc)

        sequencial = self.consultar_sequencial(
            serie=serie,
            numero_ctrc=numero,
        )

        # Mantemos a abertura do CTRC antes da aba de ocorrências,
        # reproduzindo o fluxo do SSW.
        #
        # Aproveitamos a própria tela principal para identificar
        # se o documento pertence a uma operação convencional
        # ou de logística reversa.
        html_ctrc = self.abrir_ctrc(
            serie=serie,
            numero=numero,
            sequencial=sequencial,
        )

        tipo_operacao = self.identificar_tipo_operacao(
            html_ctrc
        )

        html_ocorrencias = self.abrir_ocorrencias(
            numero=numero,
            sequencial=sequencial,
        )

        ocorrencias = self.extrair_historico(
            html_ocorrencias
        )[:limite]

        ultima_linha = (
            ocorrencias[0]
            if ocorrencias
            else None
        )

        ultima_ocorrencia_com_codigo = next(
            (
                ocorrencia
                for ocorrencia in ocorrencias
                if ocorrencia.get("codigo")
            ),
            None,
        )

        return {
            "ctrc": serie_numero_ctrc,
            "serie": serie,
            "numero": numero,
            "sequencial": sequencial,

            "tipo_operacao": tipo_operacao,

            "ocorrencias": ocorrencias,

            "ultimo_registro": ultima_linha,

            "ultima_ocorrencia": (
                ultima_ocorrencia_com_codigo
            ),
        }