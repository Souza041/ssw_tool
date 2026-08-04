import re
import time

from dataclasses import asdict, dataclass
from datetime import date
from typing import Optional


@dataclass
class CTRCConsultado:
    encontrado: bool
    serie: str
    numero: str
    seq_ctrc: str = ""
    local: str = ""
    familia: str = ""
    mensagem: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class OP101Ocorrencias:
    """
    Consulta CTRCs na OP101.

    Nesta etapa, esta classe somente:
    - abre a OP101;
    - consulta o CTRC;
    - abre o documento;
    - extrai seq_ctrc, local e FAMILIA.

    Nenhuma ocorrência é lançada aqui.
    """

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _dummy() -> str:
        return str(int(time.time() * 1000))

    @staticmethod
    def _normalizar_serie(valor: str) -> str:
        return str(valor or "").strip().upper()

    @staticmethod
    def _normalizar_numero(valor: str) -> str:
        texto = str(valor or "").strip()

        # Remove caracteres que não sejam números.
        return re.sub(r"\D", "", texto)

    @staticmethod
    def _data_ssw(valor: date | str) -> str:
        if isinstance(valor, date):
            return valor.strftime("%d%m%y")

        texto = str(valor or "").strip()

        if not re.fullmatch(r"\d{6}", texto):
            raise ValueError(
                "A data da OP101 deve estar no formato DDMMAA."
            )

        return texto

    def abrir(self, unidade: str) -> None:
        """
        Abre a OP101 explicitamente na unidade informada.

        Para CTRCs JOI, a unidade utilizada será JOI.
        """

        unidade = self._normalizar_serie(unidade)

        if not unidade:
            raise ValueError(
                "Unidade não informada para abertura da OP101."
            )

        # Troca explicitamente a unidade da sessão.
        self.client.get(
            "/bin/ssw0082",
            params={
                "quadro_menu01": unidade,
                "dummy": self._dummy(),
            },
        )

        # Abre a opção 101.
        self.client.post(
            "/bin/menu01",
            {
                "act": "TRO",
                "f2": unidade,
                "f3": "101",
                "dummy": self._dummy(),
            },
        )

        # Carrega a tela da OP101.
        self.client.post(
            "/bin/ssw0053",
            {
                "sequencia": "101",
                "dummy": self._dummy(),
            },
        )

    def pesquisar_chave(
        self,
        serie: str,
        numero: str,
        data_inicial: date | str,
        data_final: date | str,
    ) -> str:
        """
        Executa a pesquisa rápida usada pela OP101.

        Esse GET foi confirmado no HAR:
        /bin/ssw0385
        """

        serie = self._normalizar_serie(serie)
        numero = self._normalizar_numero(numero)

        data_ini_ssw = self._data_ssw(data_inicial)
        data_fim_ssw = self._data_ssw(data_final)

        response = self.client.get(
            "/bin/ssw0385",
            params={
                "dd_select": "ctrc",
                "dd_chave": numero,
                "dd_f_t_data_ini": data_ini_ssw,
                "dd_f_t_data_fin": data_fim_ssw,
                "dd_f_t_ser_ctrc": serie,
            },
        )

        return response.text

    def abrir_ctrc(
        self,
        serie: str,
        numero: str,
        data_inicial: date | str,
        data_final: date | str,
    ) -> str:
        """
        Abre o CTRC após a pesquisa.

        Reproduz o POST act=P1 capturado no HAR.
        """

        serie = self._normalizar_serie(serie)
        numero = self._normalizar_numero(numero)

        data_ini_ssw = self._data_ssw(data_inicial)
        data_fim_ssw = self._data_ssw(data_final)

        response = self.client.post(
            "/bin/ssw0053",
            {
                "act": "P1",
                "t_ser_ctrc": serie,
                "t_nro_ctrc": numero,
                "t_data_ini": data_ini_ssw,
                "t_data_fin": data_fim_ssw,
                "dd_f_t_data_ini": "",
                "dd_f_t_data_fin": "",
                "dd_f_t_ser_ctrc": "",
                "dd_f_t_ser_nf": "",
                "dd_f_t_nro_pedido": "",
                "data_ini_inf": "30/12/99",
                "data_fin_inf": "30/12/99",
                "seq_ctrc": "0",
                "local": "",
                "FAMILIA": "",
                "dummy": self._dummy(),
            },
        )

        return response.text

    @staticmethod
    def _extrair_input_hidden(
        html: str,
        nome: str,
    ) -> str:
        """
        Extrai inputs mesmo quando o HTML do SSW não usa aspas
        em todos os atributos.
        """

        padroes = [
            rf"""
                <input
                [^>]*
                name\s*=\s*["']?{re.escape(nome)}["']?
                [^>]*
                value\s*=\s*["']([^"']*)["']
            """,
            rf"""
                <input
                [^>]*
                name\s*=\s*["']?{re.escape(nome)}["']?
                [^>]*
                value\s*=\s*([^\s>]+)
            """,
            rf"""
                <input
                [^>]*
                value\s*=\s*["']([^"']*)["']
                [^>]*
                name\s*=\s*["']?{re.escape(nome)}["']?
            """,
        ]

        for padrao in padroes:
            match = re.search(
                padrao,
                html,
                flags=re.IGNORECASE | re.VERBOSE,
            )

            if match:
                return match.group(1).strip()

        return ""

    @classmethod
    def extrair_dados_ctrc(
        cls,
        html: str,
    ) -> dict:
        """
        Extrai os identificadores internos necessários para
        abrir a tela de ocorrências posteriormente.
        """

        seq_ctrc = cls._extrair_input_hidden(
            html,
            "seq_ctrc",
        )

        local = cls._extrair_input_hidden(
            html,
            "local",
        )

        familia = cls._extrair_input_hidden(
            html,
            "FAMILIA",
        )

        # Fallback: alguns layouts expõem seq_ctrc somente
        # dentro de JavaScript ou links do DACTE.
        if not seq_ctrc:
            match = re.search(
                r"seq_ctrc\s*=\s*(\d+)",
                html,
                flags=re.IGNORECASE,
            )

            if match:
                seq_ctrc = match.group(1)

        if not familia:
            match = re.search(
                r"FAMILIA\s*=\s*([A-Z0-9_-]+)",
                html,
                flags=re.IGNORECASE,
            )

            if match:
                familia = match.group(1).upper()

        return {
            "seq_ctrc": seq_ctrc,
            "local": local,
            "familia": familia,
        }

    @staticmethod
    def _pagina_indica_nao_encontrado(
        html: str,
    ) -> bool:
        texto = str(html or "").lower()

        indicadores = (
            "ctrc não encontrado",
            "ctrc nao encontrado",
            "documento não encontrado",
            "documento nao encontrado",
            "nenhum registro encontrado",
            "não foram encontrados",
            "nao foram encontrados",
        )

        return any(
            indicador in texto
            for indicador in indicadores
        )

    def consultar_ctrc(
        self,
        serie: str,
        numero: str,
        data_referencia: date | str,
    ) -> CTRCConsultado:
        """
        Consulta completa e segura da OP101.

        Para emissões do dia, usa a própria data de referência
        como início e fim da pesquisa.
        """

        serie = self._normalizar_serie(serie)
        numero = self._normalizar_numero(numero)
        data_ssw = self._data_ssw(data_referencia)

        if not serie:
            raise ValueError(
                "Série do CTRC não informada."
            )

        if not numero:
            raise ValueError(
                "Número do CTRC não informado."
            )

        self.abrir(unidade=serie)

        self.pesquisar_chave(
            serie=serie,
            numero=numero,
            data_inicial=data_ssw,
            data_final=data_ssw,
        )

        html = self.abrir_ctrc(
            serie=serie,
            numero=numero,
            data_inicial=data_ssw,
            data_final=data_ssw,
        )

        if self._pagina_indica_nao_encontrado(html):
            return CTRCConsultado(
                encontrado=False,
                serie=serie,
                numero=numero,
                mensagem="CTRC não encontrado na OP101.",
            )

        dados = self.extrair_dados_ctrc(html)

        seq_ctrc = dados["seq_ctrc"]

        if not seq_ctrc or seq_ctrc == "0":
            return CTRCConsultado(
                encontrado=False,
                serie=serie,
                numero=numero,
                local=dados["local"],
                familia=dados["familia"],
                mensagem=(
                    "A OP101 abriu a resposta, mas não foi "
                    "possível extrair o seq_ctrc."
                ),
            )

        return CTRCConsultado(
            encontrado=True,
            serie=serie,
            numero=numero,
            seq_ctrc=seq_ctrc,
            local=dados["local"] or "Q",
            familia=dados["familia"] or "ROD",
            mensagem="CTRC localizado com sucesso.",
        )