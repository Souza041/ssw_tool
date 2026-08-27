import re
from datetime import datetime, timedelta

from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import data_barra_curta, data_ddmmaa, dummy

def possui_oc_entrega_realizada(html: str) -> bool:
    if not html:
        return False

    texto = re.sub(r"\s+", " ", html).upper()

    padroes = [
        r"\b0?1\s*-\s*ENTREGA REALIZADA NORMALMENTE\b",
        r"\b0?1\s*-\s*ENTREGA REALIZADA\b",
    ]

    return any(
        re.search(padrao, texto, flags=re.IGNORECASE)
        for padrao in padroes
    )

def split_ctrc(serie_numero: str) -> tuple[str, str]:
    texto = str(serie_numero).strip().upper()

    match = re.search(r"([A-Z]{2}[A-Z0-9])\D*(\d{6})(?:\D*\d)?", texto)

    if not match:
        raise ValueError(f"Formato de CTRC inválido: {serie_numero}")

    return match.group(1), match.group(2)


class OP101Instructions:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self) -> None:
        self.client.open_option("101", settings.unidade)

    def consultar_sequencial(self, numero_ctrc: str) -> str:
        hoje = datetime.now()
        inicio = hoje - timedelta(days=90)

        response = self.client.get(
            "/bin/ssw0385",
            params={
                "dd_select": "ctrc",
                "dd_chave": numero_ctrc,
                "dd_f_t_data_ini": data_ddmmaa(inicio),
                "dd_f_t_data_fin": data_ddmmaa(hoje),
            },
        )

        match = re.search(r'"sequencial"\s*:\s*"([^"]+)"', response.text)

        if not match:
            raise ValueError(f"Sequencial não encontrado para CTRC {numero_ctrc}")

        return match.group(1)

    def buscar_ctrc(self, numero_ctrc: str) -> str:
        hoje = datetime.now()
        inicio = hoje - timedelta(days=90)

        response = self.client.post(
            "/bin/ssw0053",
            {
                "act": "P1",
                "t_nro_ctrc": numero_ctrc,
                "t_data_ini": data_ddmmaa(inicio),
                "t_data_fin": data_ddmmaa(hoje),
                "seq_ctrc": "0",
                "dummy": dummy(),
            },
        )

        return response.text

    def abrir_ocorrencias(
        self,
        numero_ctrc: str,
        seq_ctrc: str,
    ) -> str:
        hoje = datetime.now()
        inicio = hoje - timedelta(days=90)

        response = self.client.post(
            "/bin/ssw0053",
            {
                "act": "O",
                "g_ctrc_nro_ctrc": numero_ctrc,
                "seq_ctrc": seq_ctrc,
                "FAMILIA": "ROD",
                "data_ini_inf": data_barra_curta(inicio),
                "data_fin_inf": data_barra_curta(hoje),
                "dummy": dummy(),
            },
        )

        return response.text

    def salvar_instrucao(
        self,
        numero_ctrc: str,
        seq_ctrc: str,
        texto: str,
    ) -> None:
        agora = datetime.now()

        self.client.post(
            "/bin/ssw0122",
            {
                "act": "II4",
                "observ": texto,
                "f4": data_ddmmaa(agora),
                "f5": agora.strftime("%H%M"),
                "f8": "N",
                "f11": "N",
                "seq_instr": "0",
                "seq_ctrc": seq_ctrc,
                "FAMILIA": "ROD",
                "dummy": dummy(),
            },
        )

    def lancar_instrucao(
        self,
        serie_numero_ctrc: str,
        texto: str,
    ) -> str:
        serie, numero = split_ctrc(serie_numero_ctrc)

        seq_ctrc = self.consultar_sequencial(numero)

        html_ctrc = self.buscar_ctrc(numero)

        # Primeira proteção:
        # CTRC já está entregue na tela principal da OP101.
        if possui_oc_entrega_realizada(html_ctrc):
            return "IGNORADO"

        html_ocorrencias = self.abrir_ocorrencias(
            numero,
            seq_ctrc,
        )

        # Segunda proteção:
        # valida novamente imediatamente antes de salvar.
        if possui_oc_entrega_realizada(html_ocorrencias):
            return "IGNORADO"

        self.salvar_instrucao(
            numero,
            seq_ctrc,
            texto,
        )

        return "OK"