import re
import xml.etree.ElementTree as ET

from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import dummy

from html import unescape
from urllib.parse import unquote

from datetime import datetime

import unicodedata


def normalizar_texto(texto: str) -> str:
    texto = str(texto).strip().upper()

    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ASCII", "ignore").decode("ASCII")

    return texto

class OP001Coleta:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self, unidade: str | None = None) -> None:
        unidade = unidade or settings.unidade

        self.client.post(
            "/bin/menu01",
            {
                "act": "TRO",
                "f2": unidade,
                "f3": "001",
                "dummy": dummy(),
            },
        )

        self.client.post(
            "/bin/ssw0017",
            {
                "sequencia": "1",
                "dummy": dummy(),
            },
        )

    def consultar_remetente(self, cnpj: str) -> dict:
        response = self.client.get(
            "/bin/ssw0017",
            params={
                "ajax": "S",
                "emit": cnpj,
                "dummy": dummy(),
            },
        )

        root = ET.fromstring(response.text)

        def get(tag: str) -> str:
            el = root.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        return {
            "nome_curva": get("nome_curva"),
            "nome": get("nome"),
            "endereco": get("ende"),
            "numero": get("num"),
            "bairro": get("bair"),
            "cep": get("cep"),
            "setor": get("set"),
            "pagador": get("pag"),
            "latitude": get("lat"),
            "longitude": get("lng"),
            "complemento": get("comple"),
            "cidade": get("cid"),
            "uf": get("uf"),
            "cidade_uf": f"{get('cid')} / {get('uf')}" if get("cid") and get("uf") else "",
            "filial": get("fil"),
        }

    def salvar_coleta_reversa(
        self,
        solicitante: str,
        tipo_frete: str,
        cnpj_remetente: str,
        nota_fiscal: str,
        cnpj_destinatario: str,
        data_programada: str,
        hora_limite: str,
        especie: str = "001",
        especie_descricao: str = " DIVERSOS",
        volumes: str = "1",
        peso: str = "1,000",
        m3: str = "1,0000",
        peso_calculo: str = "250,000",
        valor_mercadoria: str = "1,00",
    ) -> dict:
        remetente = self.consultar_remetente(cnpj_remetente)

        payload = {
            "act": "SAVECOL",
            "f5": data_programada,
            "f24": "N",
            "cod_fil": "#cod_fil#",
            "devolucao": "REVERSA",

            "f30": solicitante,
            "f31": tipo_frete,

            "f33": remetente["nome"],
            "f35": cnpj_remetente,
            "_nome_emit": remetente["nome_curva"],

            "f37": nota_fiscal,
            "f39": remetente["endereco"],
            "f40": remetente["numero"],
            "comple_local_coleta": remetente["complemento"],
            "f41": remetente["bairro"],
            "f44": remetente["cep"],

            # Estes vieram do HAR. Depois podemos buscar dinamicamente se necessário.
            "cid_col": remetente.get("cidade_uf") or remetente.get("cidade") or "",
            "fil_col": remetente.get("filial") or "",

            "f48": cnpj_destinatario,
            "_nome_dest": "ELECTROLUX DO BRASI A2",
            "ed": "(ED)",

            "f51": cnpj_destinatario,
            "_nome_pag": "ELECTROLUX DO BRASI A2",
            "credito": "Banco",
            "ent": " ",

            "cad_cli_cnpj": "#cad_cli_cnpj#",
            "cad_cli_cfop_descr": "#cad_cli_cfop_descr#",
            "cad_cli_cidade_uf": "#cad_cli_cidade_uf#",

            "id_local_entrega": "JOAO LUNARDELLI,2205",
            "vlr_loc": "0,00",
            "setor": remetente.get("setor", ""),
            "id_cidade_entrega": "CURITIBA/PR",
            "fil_ent": "CWB",

            "id_data_prog": data_programada,
            "id_hora_limite": hora_limite,
            "id_especie": especie,
            "especie_descricao": especie_descricao,
            "id_qtde_vol": volumes,
            "id_peso": peso,
            "id_m3": m3,
            "id_peso_calculo": peso_calculo,

            "cubatot": "0,0000",
            "id_merc_vlr": valor_mercadoria,

            "verReload": "N",
            "latitude": remetente["latitude"],
            "longitude": remetente["longitude"],
            "customPedido": "",
            "dummy": dummy(),
        }

        for i in range(1, 21):
            payload[f"cuba{i}"] = "0,0000"

        response = self.client.post("/bin/ssw0017", payload)

        html_final = response.text

        if self.eh_popup_unidade_nao_atendida(html_final):
            html_final = self.confirmar_unidade_remetente(html_final)

        return self.extrair_resultado(html_final)
    
    def extrair_hidden_inputs(self, html: str) -> dict:
        decoded = unquote(unescape(html or ""))

        inputs = re.findall(
            r'<input[^>]+type=hidden[^>]+>',
            decoded,
            flags=re.I,
        )

        data = {}

        for input_html in inputs:
            name_match = re.search(r'name=["\']?([^"\'>\s]+)', input_html, flags=re.I)
            value_match = re.search(r'value=["\']([^"\']*)', input_html, flags=re.I)

            if name_match:
                name = name_match.group(1)
                value = value_match.group(1) if value_match else ""
                data[name] = value

        return data


    def eh_popup_unidade_nao_atendida(self, html: str) -> bool:
        decoded = unquote(unescape(html or "")).lower()

        return (
            "cadastrar na minha unidade" in decoded
            and "continuar e cadastrar na unidade do remetente" in decoded
            and "btn_99999" in decoded
        )


    def confirmar_unidade_remetente(self, html: str) -> str:
        payload = self.extrair_hidden_inputs(html)

        payload["act"] = "SAVECOL"
        payload["btn_99999"] = "2"
        payload["dummy"] = dummy()

        response = self.client.post("/bin/ssw0017", payload)

        return response.text

    def extrair_resultado(self, html: str) -> dict:
        decoded = unquote(unescape(html or ""))

        texto_limpo = re.sub(r"<br\s*/?>", " ", decoded, flags=re.I)
        texto_limpo = re.sub(r"<.*?>", " ", texto_limpo)
        texto_limpo = re.sub(r"\s+", " ", texto_limpo).strip()

        match = re.search(
            r"Coleta\s+([A-Z]{3})\s+(\d+).*?INCLU[IÍ]DA\s+com\s+sucesso",
            texto_limpo,
            flags=re.I,
        )

        match_seq = re.search(r"seq_coleta=(\d+)", decoded)

        if match:
            filial = match.group(1).upper()
            numero = match.group(2)

            return {
                "sucesso": True,
                "filial": filial,
                "numero_coleta": numero,
                "coleta": f"{filial}{numero}",
                "seq_coleta": match_seq.group(1) if match_seq else "",
                "mensagem": f"Coleta {filial} {numero} incluída com sucesso.",
                "raw": decoded[:1000],
            }

        return {
            "sucesso": False,
            "filial": "",
            "numero_coleta": "",
            "coleta": "",
            "seq_coleta": match_seq.group(1) if match_seq else "",
            "mensagem": texto_limpo[:500],
            "raw": decoded[:3000],
        }
    
    def salvar_coleta_nfd(
        self,
        nfd: str,
        cnpj: str,
        solicitante: str = "AutomacaoColeta",
        tipo_frete: str = "F",
        cnpj_destinatario: str = "76487032004031",
        hora_limite: str = "1800",
    ) -> dict:
        hoje = datetime.now().strftime("%d%m%y")

        return self.salvar_coleta_reversa(
            solicitante=solicitante,
            tipo_frete=tipo_frete,
            cnpj_remetente=str(cnpj).strip(),
            nota_fiscal=str(nfd).strip(),
            cnpj_destinatario=cnpj_destinatario,
            data_programada=hoje,
            hora_limite=hora_limite,
            especie="001",
            especie_descricao=" DIVERSOS",
            volumes="1",
            peso="1,000",
            m3="1,0000",
            peso_calculo="250,000",
            valor_mercadoria="1,00",
        )

    def consultar_lista_clientes(self, nome_cliente: str) -> list[dict]:
        response = self.client.get(
            "/bin/ssw0017",
            params={
                "ajax": "S",
                "lista": nome_cliente,
                "tipo": "N",
                "dummy": dummy(),
            },
        )

        decoded = unquote(unescape(response.text or ""))

        cnpjs = re.findall(r"<cgc>(.*?)</cgc>", decoded, flags=re.I | re.S)
        nomes = re.findall(r"<nome>(.*?)</nome>", decoded, flags=re.I | re.S)
        cidades = re.findall(r"<cidade>(.*?)</cidade>", decoded, flags=re.I | re.S)

        clientes = []

        for cnpj, nome, cidade in zip(cnpjs, nomes, cidades):
            clientes.append({
                "cnpj": re.sub(r"\D", "", cnpj),
                "nome": re.sub(r"\s+", " ", nome).strip(),
                "cidade": re.sub(r"\s+", " ", cidade).strip().upper(),
            })

        return clientes

    def selecionar_cliente_por_destino(
        self,
        nome_cliente: str,
        municipio_destino: str,
        uf_destino: str,
    ) -> dict:
        clientes = self.consultar_lista_clientes(nome_cliente)

        alvo = normalizar_texto(
            f"{municipio_destino} / {uf_destino}"
        )

        for cliente in clientes:
            cidade_cliente = normalizar_texto(
                cliente["cidade"]
            )

            if cidade_cliente == alvo:
                return cliente

        raise ValueError(
            f"Nenhum cadastro encontrado para {nome_cliente} em {alvo}. "
            f"Opções encontradas: {[c['cidade'] for c in clientes]}"
        )


    def consultar_previsao_entrega(
        self,
        cidade: str,
        uf: str,
        cnpj_emitente: str,
        cnpj_destinatario: str,
        cnpj_pagador: str,
        data_programada: str,
    ) -> str:
        response = self.client.get(
            "/bin/ssw0017",
            params={
                "ajax": "S",
                "prev": "S",
                "nome": cidade,
                "uf": uf,
                "cgc_emit": cnpj_emitente,
                "cgc_dest": cnpj_destinatario,
                "cgc_pag": cnpj_pagador,
                "data_prog": data_programada,
                "dummy": dummy(),
            },
        )

        return response.text
    
    def consultar_cliente(self, cnpj: str) -> dict:
        response = self.client.get(
            "/bin/ssw0017",
            params={
                "ajax": "S",
                "emit": str(cnpj).strip(),
                "dummy": dummy(),
            },
        )

        root = ET.fromstring(response.text)

        def get(tag: str) -> str:
            el = root.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        return {
            "nome_curva": get("nome_curva"),
            "nome": get("nome"),
            "endereco": get("ende"),
            "numero": get("num"),
            "bairro": get("bair"),
            "cep": get("cep"),
            "setor": get("set"),
            "pagador": get("pag"),
            "latitude": get("lat"),
            "longitude": get("lng"),
            "complemento": get("comple"),
            "cidade": get("cid"),
            "uf": get("uf"),
            "cidade_uf": get("cidade_uf"),
            "filial": get("fil"),
            "credito": get("credito"),
        }
    
    def consultar_destinatario(self, cnpj: str) -> dict:
        response = self.client.get(
            "/bin/ssw0017",
            params={
                "ajax": "S",
                "dest": str(cnpj).strip(),
                "dummy": dummy(),
            },
        )

        root = ET.fromstring(response.text)

        def get(tag):
            el = root.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        return {
            "nome": get("nome"),
            "cidade": get("cid"),
            "uf": get("uf"),
            "pagador": get("pag"),
            "local_entrega": get("ent"),
        }


    def definir_mercadoria_cliente(self, cnpj_destinatario: str) -> dict:
        cnpj = re.sub(r"\D", "", str(cnpj_destinatario))

        if cnpj == "62058318000695":
            return {
                "id_cod_merc": "9",
                "aux_mercadoria": "L.BRANCA",
            }

        return {
            "id_cod_merc": "127",
            "aux_mercadoria": "WHP PECAS M3 ATE 0,333",
        }


    def continuar_fluxo_coleta(self, html: str) -> str:
        html_atual = html

        for _ in range(8):
            decoded = unquote(unescape(html_atual or ""))

            if "Coleta" in decoded and "INCLU" in decoded.upper():
                return html_atual

            payload = self.extrair_hidden_inputs(decoded)

            if not payload:
                return html_atual

            if "btn_99999" in payload:
                payload["btn_99999"] = "2"

            if "btn_156" in payload:
                payload["btn_156"] = "S"

            if "id_merc_vlr" in payload and not payload.get("id_merc_vlr"):
                payload["id_merc_vlr"] = "1,00"

            payload["dummy"] = dummy()

            response = self.client.post("/bin/ssw0017", payload)
            html_atual = response.text

            if not payload:
                print("[OP001] Sem payload para continuar. Retorno:")
                print(decoded[:1500])
                return html_atual

        return html_atual
    
    def salvar_coleta_transporte(
        self,
        nome_cliente: str,
        municipio_destino: str,
        uf_destino: str,
        transporte: str,
        ordem_inversa: str,
        cnpj_destinatario: str,
        data_programada: str | None = None,
        hora_limite: str = "1800",
        solicitante: str = "AutomacaoColeta",
        tipo_frete: str = "F",
    ) -> dict:
        if data_programada is None:
            data_programada = datetime.now().strftime("%d%m%y")

        cliente = self.selecionar_cliente_por_destino(
            nome_cliente=nome_cliente,
            municipio_destino=municipio_destino,
            uf_destino=uf_destino,
        )

        cnpj_remetente = cliente["cnpj"]

        remetente = self.consultar_cliente(cnpj_remetente)
        destinatario = self.consultar_destinatario(cnpj_destinatario)

        print("[DESTINATARIO]", destinatario)

        mercadoria = self.definir_mercadoria_cliente(cnpj_destinatario)

        id_cidade_entrega = ""

        if destinatario.get("cidade") and destinatario.get("uf"):
            id_cidade_entrega = (
                f"{destinatario['cidade']}/{destinatario['uf']}"
            )

        fil_ent = destinatario.get("filial") or ""
        setor = destinatario.get("setor") or ""

        if id_cidade_entrega and "/" in id_cidade_entrega:
            cidade_prev, uf_prev = [parte.strip() for parte in id_cidade_entrega.split("/", 1)]

            self.consultar_previsao_entrega(
                cidade=cidade_prev,
                uf=uf_prev,
                cnpj_emitente=cnpj_remetente,
                cnpj_destinatario=cnpj_destinatario,
                cnpj_pagador=cnpj_destinatario,
                data_programada=data_programada,
            )

        cnpj_dest_limpo = re.sub(r"\D", "", str(cnpj_destinatario))

        if cnpj_dest_limpo == "59105999006974":
            self.consultar_previsao_entrega(
                cidade="SAO PAULO",
                uf="SP",
                cnpj_emitente=cnpj_remetente,
                cnpj_destinatario=cnpj_destinatario,
                cnpj_pagador=cnpj_destinatario,
                data_programada=data_programada,
            )

            id_cidade_entrega = "SAO PAULO/SP"
            fil_ent = "GRU"
            setor = "858"

        observacao = f"{transporte} {ordem_inversa}".strip()

        print("[DEBUG OP001]")
        print("cliente selecionado:", cliente)
        print("cnpj_remetente:", cnpj_remetente)
        print("cnpj_destinatario:", cnpj_destinatario)
        print("destinatario:", destinatario)
        print("id_cidade_entrega:", id_cidade_entrega)
        print("fil_ent:", fil_ent)
        print("setor:", setor)

        transporte_limpo = re.sub(r"\D", "", str(transporte))
        ordem_limpa = re.sub(r"\D", "", str(ordem_inversa))

        identificador = transporte_limpo or ordem_limpa

        solicitante_unico = f"AUT{identificador}"[:20]

        observacao = (
            f"TRANSPORTE: {transporte} | "
            f"ORDEM: {ordem_inversa}"
        ).strip()

        payload = {
            "act": "SAVECOL",
            "f5": data_programada,
            "f24": "N",
            "cod_fil": "#cod_fil#",
            "devolucao": "REVERSA",

            "f30": solicitante_unico,
            "f31": tipo_frete,

            "f33": remetente.get("nome"),
            "f35": cnpj_remetente,
            "_nome_emit": remetente.get("nome_curva") or remetente.get("nome"),

            "f37": "",
            "f39": remetente.get("endereco"),
            "f40": remetente.get("numero"),
            "comple_local_coleta": remetente.get("complemento"),
            "f41": remetente.get("bairro"),
            "f44": remetente.get("cep"),

            "cid_col": remetente.get("cidade_uf") or remetente.get("cidade"),
            "fil_col": remetente.get("filial"),

            "f48": cnpj_destinatario,
            "_nome_dest": destinatario.get("nome_curva") or destinatario.get("nome"),
            "ed": "(ED)",

            "f51": cnpj_destinatario,
            "_nome_pag": destinatario.get("nome_curva") or destinatario.get("nome"),
            "credito": destinatario.get("credito") or "Banco",
            "ent": " ",

            "cad_cli_cnpj": "#cad_cli_cnpj#",
            "cad_cli_cfop_descr": "#cad_cli_cfop_descr#",
            "cad_cli_cidade_uf": "#cad_cli_cidade_uf#",

            "id_local_entrega": destinatario.get("local_entrega", ""),
            "vlr_loc": "0,00",
            "setor": setor,
            "id_cidade_entrega": id_cidade_entrega,
            "fil_ent": fil_ent,

            "id_data_prog": data_programada,
            "id_hora_limite": hora_limite,

            "id_especie": "001",
            "especie_descricao": " DIVERSOS",
            "id_qtde_vol": "1",
            "id_peso": "1,000",
            "id_m3": "1,0000",
            "id_peso_calculo": "250,000",

            "id_cod_merc": mercadoria["id_cod_merc"],
            "aux_mercadoria": mercadoria["aux_mercadoria"],
            "cubatot": "0,0000",
            "id_merc_vlr": "1,00",

            "id_obs1": observacao,
            "customPedido": f"|{transporte}|{ordem_inversa}",
            "-2": transporte,
            "-1": ordem_inversa,

            "verReload": "N",
            "latitude": remetente.get("latitude"),
            "longitude": remetente.get("longitude"),

            "dummy": dummy(),
        }

        for i in range(1, 21):
            payload[f"cuba{i}"] = "0,0000"

        print("[DEBUG PAYLOAD CIDADE]")
        print("f48:", payload.get("f48"))
        print("f51:", payload.get("f51"))
        print("id_cidade_entrega:", payload.get("id_cidade_entrega"))
        print("fil_ent:", payload.get("fil_ent"))
        print("setor:", payload.get("setor"))

        response = self.client.post("/bin/ssw0017", payload)

        html_final = self.continuar_fluxo_coleta(response.text)

        return self.extrair_resultado(html_final)
