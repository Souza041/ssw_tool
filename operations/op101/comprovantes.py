import re
from html import unescape
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
from openpyxl import load_workbook

from ssw.client import SSWClient
from ssw.utils import dummy


CODIGOS_VALIDOS = {"1", "93"}


class OP101Comprovantes:
    def __init__(self, client: SSWClient):
        self.client = client

    def buscar_nf(self, numero_nf: str, data_ini: str, data_fin: str) -> dict:
        response = self.client.get(
            "/bin/ssw0385",
            params={
                "dd_select": "nf",
                "dd_chave": numero_nf,
                "dd_f_t_data_ini": data_ini,
                "dd_f_t_data_fin": data_fin,
            },
        )

        data = response.json()

        return {
            "sequencial": str(data.get("sequencial", "")).strip(),
            "chave": str(data.get("chave", "")).strip(),
            "raw": data,
        }

    def abrir_op101_por_nf(self, numero_nf: str, data_ini: str, data_fin: str) -> str:
        response = self.client.post(
            "/bin/ssw0053",
            data={
                "act": "P2",
                "t_nro_nf": numero_nf,
                "t_data_ini": data_ini,
                "t_data_fin": data_fin,
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
                "dummy": dummy(),
            },
        )

        return response.text
    
    def extrair_opcoes_ctrc(self, html: str) -> list[dict]:
        texto = unquote(unescape(html or ""))

        linhas = re.findall(r"<r>(.*?)</r>", texto, flags=re.I | re.S)

        opcoes = []

        for linha in linhas:
            campos = {}

            for campo, valor in re.findall(r"<f(\d+)>(.*?)</f\d+>", linha, flags=re.I | re.S):
                valor_limpo = re.sub(r"<.*?>", "", valor)
                valor_limpo = unescape(valor_limpo).replace("\xa0", " ")
                valor_limpo = re.sub(r"\s+", " ", valor_limpo).strip()

                campos[f"f{campo}"] = valor_limpo

            familia = campos.get("f13", "")
            partes = familia.split("@")

            sequencial = ""
            data_inf = ""

            if len(partes) >= 4:
                sequencial = partes[2]
                data_inf = partes[3]

            opcoes.append({
                "dominio": campos.get("f0", ""),
                "ctrc": campos.get("f1", ""),
                "tipo": campos.get("f2", ""),
                "emissao": campos.get("f3", ""),
                "remetente": campos.get("f4", ""),
                "cidade_remetente": campos.get("f5", ""),
                "pagador": campos.get("f6", ""),
                "destinatario": campos.get("f7", ""),
                "cidade_destinatario": campos.get("f8", ""),
                "ct_e": campos.get("f11", ""),
                "familia": familia,
                "sequencial": sequencial,
                "data_inf": data_inf,
            })

        return opcoes


    def selecionar_ctrc_valido(self, html: str) -> dict | None:
        opcoes = self.extrair_opcoes_ctrc(html)

        if not opcoes:
            return None

        # Prioridade 1: Whirlpool
        for opcao in opcoes:
            remetente = opcao["remetente"].strip().upper()

            if "WHIRLPOOL" in remetente:
                return opcao

        # Prioridade 2: primeira opção não cancelada
        for opcao in opcoes:
            texto_linha = " ".join(str(v) for v in opcao.values()).upper()

            if "CANCEL" not in texto_linha:
                return opcao

        # Prioridade 3: se todas parecem canceladas, pega a primeira
        return opcoes[0]


    def abrir_op101_por_sequencial(self, sequencial: str, data_inf: str, numero_nf: str) -> str:
        response = self.client.post(
            "/bin/ssw0053",
            data={
                "act": f"FAMILIA@ROD@{sequencial}@{data_inf}",
                "dd_f_t_data_ini": "",
                "dd_f_t_data_fin": "",
                "dd_f_t_ser_ctrc": "",
                "dd_f_t_ser_nf": "",
                "dd_f_t_nro_pedido": "",
                "g_ctrc_ser_ctrc": "",
                "g_ctrc_nro_ctrc": "0",
                "gw_nro_nf_ini": numero_nf,
                "g_ctrc_nf_vol_ini": "0",
                "gw_ctrc_nr_sscc": "",
                "g_ctrc_nro_ctl_form": "0",
                "gw_ctrc_parc_nro_ctrc_parc": "0",
                "g_ctrc_c_chave_fis": "",
                "gw_gaiola_codigo": "0",
                "gw_pallet_codigo": "0",
                "data_ini_inf": "28/3/25",
                "data_fin_inf": "26/6/26",
                "seq_ctrc": "0",
                "local": "",
                "FAMILIA": "",
                "dummy": dummy(),
            },
        )

        return response.text

    def comprovante_disponivel(self, html: str) -> bool:
        texto = unquote(unescape(html or ""))

        # Quando não existe comprovante, normalmente o link vem ausente/desabilitado.
        # Quando existe, o HTML contém o link/texto de Comprov Entrega de forma clicável.
        return (
            "Comprov Entrega" in texto
            and "disabled" not in texto[texto.find("Comprov Entrega") : texto.find("Comprov Entrega") + 300].lower()
        )

    def abrir_arquivos_edi(
        self,
        numero_nf: str,
        sequencial: str,
        data_ini: str,
        data_fin: str,
    ) -> str:
        response = self.client.post(
            "/bin/ssw0053",
            data={
                "act": "ARQ",
                "aviso_resgate": "#aviso_resgate#",
                "dd_f_t_data_ini": "",
                "dd_f_t_data_fin": "",
                "dd_f_t_ser_ctrc": "",
                "dd_f_t_ser_nf": "",
                "dd_f_t_nro_pedido": "",
                "g_ctrc_ser_ctrc": "",
                "g_ctrc_nro_ctrc": "0",
                "gw_nro_nf_ini": numero_nf,
                "g_ctrc_nf_vol_ini": "0",
                "gw_ctrc_nr_sscc": "",
                "g_ctrc_nro_ctl_form": "0",
                "gw_ctrc_parc_nro_ctrc_parc": "0",
                "g_ctrc_c_chave_fis": "",
                "gw_gaiola_codigo": "0",
                "gw_pallet_codigo": "0",
                "local": "Q",
                "data_ini_inf": self.formatar_data_inf(data_ini),
                "data_fin_inf": self.formatar_data_inf(data_fin),
                "seq_ctrc": sequencial,
                "FAMILIA": "ROD",
                "dummy": dummy(),
            },
        )

        return response.text

    def formatar_data_inf(self, data: str) -> str:
        # 280326 -> 28/3/26
        data = re.sub(r"\D", "", str(data))

        if len(data) != 6:
            return data

        dia = str(int(data[:2]))
        mes = str(int(data[2:4]))
        ano = data[4:]

        return f"{dia}/{mes}/{ano}"

    def extrair_linhas_edi(self, html: str) -> list[dict]:
        texto = unquote(unescape(html or ""))

        linhas = re.findall(r"<r>(.*?)</r>", texto, flags=re.I | re.S)

        registros = []

        for linha in linhas:
            campos = {}

            for campo, valor in re.findall(r"<f(\d+)>(.*?)</f\d+>", linha, flags=re.I | re.S):
                valor_limpo = re.sub(r"<.*?>", "", valor)
                valor_limpo = unescape(valor_limpo).replace("\xa0", " ")
                valor_limpo = re.sub(r"\s+", " ", valor_limpo).strip()

                campos[f"f{campo}"] = valor_limpo

            registros.append({
                "data_hora": campos.get("f0", ""),
                "meio": campos.get("f1", ""),
                "numero": campos.get("f8", ""),
                "codigo": campos.get("f9", ""),
                "raw": campos,
            })

        return registros

    def localizar_comprovante_edi(self, html: str, numero_nf: str) -> dict | None:
        registros = self.extrair_linhas_edi(html)

        numero_nf = re.sub(r"\D", "", str(numero_nf))

        for registro in registros:
            meio = registro["meio"].strip().upper()
            codigo = registro["codigo"].strip()
            numero = re.sub(r"\D", "", registro["numero"])

            if not meio.startswith("WEBSERV"):
                continue

            if codigo not in CODIGOS_VALIDOS:
                continue

            # Quando a coluna Número vier preenchida, validamos com a NF.
            if numero and numero != numero_nf:
                continue

            return registro

        return None

    def consultar_comprovante_nf(
        self,
        numero_nf: str,
        data_ini: str,
        data_fin: str,
    ) -> str:
        html_101 = self.abrir_op101_por_nf(numero_nf, data_ini, data_fin)

        opcao_ctrc = self.selecionar_ctrc_valido(html_101)

        sequencial = ""

        if opcao_ctrc:
            sequencial = opcao_ctrc["sequencial"]

            html_101 = self.abrir_op101_por_sequencial(
                sequencial=opcao_ctrc["sequencial"],
                data_inf=opcao_ctrc["data_inf"],
                numero_nf=numero_nf,
            )
        else:
            busca = self.buscar_nf(numero_nf, data_ini, data_fin)
            sequencial = busca["sequencial"]

            if not sequencial:
                return "NF não encontrada no SSW"

        html_edi = self.abrir_arquivos_edi(
            numero_nf=numero_nf,
            sequencial=sequencial,
            data_ini=data_ini,
            data_fin=data_fin,
        )

        registro = self.localizar_comprovante_edi(html_edi, numero_nf)

        if not registro:
            return "Sem comprovante no sistema"

        return f"Comprovante subiu dia {registro['data_hora']}"