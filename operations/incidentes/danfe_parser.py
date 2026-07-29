import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


class CTeXMLParser:
    NAMESPACE = {
        "cte": "http://www.portalfiscal.inf.br/cte",
    }

    @staticmethod
    def texto(
        elemento: ET.Element | None,
        caminho: str,
        default: str = "",
    ) -> str:
        if elemento is None:
            return default

        encontrado = elemento.find(
            caminho,
            CTeXMLParser.NAMESPACE,
        )

        if encontrado is None:
            return default

        return str(
            encontrado.text or default
        ).strip()

    @staticmethod
    def texto_primeiro(
        elemento: ET.Element | None,
        caminhos: list[str],
        default: str = "",
    ) -> str:
        for caminho in caminhos:
            valor = CTeXMLParser.texto(
                elemento,
                caminho,
            )

            if valor:
                return valor

        return default

    @staticmethod
    def decimal(
        valor: Any,
    ) -> float | None:
        texto = str(valor or "").strip()

        if not texto:
            return None

        texto = texto.replace(",", ".")

        try:
            return float(texto)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def normalizar_chave(
        valor: Any,
    ) -> str:
        return re.sub(
            r"\D",
            "",
            str(valor or ""),
        )

    @staticmethod
    def separar_sku_produto(
        produto_predominante: str,
    ) -> tuple[str, str]:
        texto = str(
            produto_predominante or ""
        ).strip()

        if not texto:
            return "", ""

        padroes = [
            r"^(\d{5,})\s*[-–—]\s*(.+)$",
            r"^(\d{5,})\s+(.+)$",
        ]

        for padrao in padroes:
            match = re.match(
                padrao,
                texto,
            )

            if match:
                return (
                    match.group(1).strip(),
                    match.group(2).strip(),
                )

        return "", texto

    @staticmethod
    def encontrar_xml_cte(
        arquivo_zip: Path,
    ) -> str:
        with zipfile.ZipFile(arquivo_zip) as zip_ref:
            candidatos = [
                nome
                for nome in zip_ref.namelist()
                if nome.lower().endswith(".xml")
            ]

            if not candidatos:
                raise ValueError(
                    f"Nenhum XML encontrado em {arquivo_zip.name}."
                )

            for nome in candidatos:
                conteudo = zip_ref.read(nome)

                try:
                    raiz = ET.fromstring(conteudo)
                except ET.ParseError:
                    continue

                tag = raiz.tag.lower()

                if (
                    "cteproc" in tag
                    or tag.endswith("cte")
                ):
                    return nome

        raise ValueError(
            f"XML de CT-e não encontrado em {arquivo_zip.name}."
        )

    def analisar_zip(
        self,
        arquivo_zip: Path | str,
        ctrc: str = "",
        sequencial: str = "",
    ) -> dict:
        caminho_zip = Path(arquivo_zip)

        if not caminho_zip.exists():
            raise FileNotFoundError(
                f"ZIP não encontrado: {caminho_zip}"
            )

        nome_xml = self.encontrar_xml_cte(
            caminho_zip
        )

        with zipfile.ZipFile(caminho_zip) as zip_ref:
            conteudo_xml = zip_ref.read(
                nome_xml
            )

        raiz = ET.fromstring(
            conteudo_xml
        )

        inf_cte = raiz.find(
            ".//cte:infCte",
            self.NAMESPACE,
        )

        if inf_cte is None:
            raise ValueError(
                f"infCte não encontrado no XML {nome_xml}."
            )

        ide = inf_cte.find(
            "cte:ide",
            self.NAMESPACE,
        )

        emitente = inf_cte.find(
            "cte:emit",
            self.NAMESPACE,
        )

        remetente = inf_cte.find(
            "cte:rem",
            self.NAMESPACE,
        )

        destinatario = inf_cte.find(
            "cte:dest",
            self.NAMESPACE,
        )

        expedidor = inf_cte.find(
            "cte:exped",
            self.NAMESPACE,
        )

        recebedor = inf_cte.find(
            "cte:receb",
            self.NAMESPACE,
        )

        inf_carga = inf_cte.find(
            ".//cte:infCarga",
            self.NAMESPACE,
        )

        chave_cte = self.normalizar_chave(
            inf_cte.attrib.get("Id", "")
        )

        produto_predominante = self.texto(
            inf_carga,
            "cte:proPred",
        )

        sku, descricao_produto = (
            self.separar_sku_produto(
                produto_predominante
            )
        )

        chaves_nfe = []

        for item in inf_cte.findall(
            ".//cte:infNFe",
            self.NAMESPACE,
        ):
            chave = self.normalizar_chave(
                self.texto(
                    item,
                    "cte:chave",
                )
            )

            if chave:
                chaves_nfe.append(chave)

        documentos_outros = []

        for item in inf_cte.findall(
            ".//cte:infOutros",
            self.NAMESPACE,
        ):
            numero = self.texto(
                item,
                "cte:nDoc",
            )

            tipo = self.texto(
                item,
                "cte:tpDoc",
            )

            descricao = self.texto(
                item,
                "cte:descOutros",
            )

            documentos_outros.append({
                "tipo": tipo,
                "descricao": descricao,
                "numero": numero,
            })

        primeiro_documento = (
            documentos_outros[0]
            if documentos_outros
            else {}
        )

        return {
            "ctrc": ctrc,
            "sequencial_ctrc": sequencial,
            "arquivo_zip": str(caminho_zip),
            "arquivo_xml_cte": nome_xml,

            "chave_cte": chave_cte,
            "numero_cte": self.texto(
                ide,
                "cte:nCT",
            ),
            "serie_cte": self.texto(
                ide,
                "cte:serie",
            ),
            "data_emissao_cte": self.texto(
                ide,
                "cte:dhEmi",
            ),

            "municipio_origem": self.texto(
                ide,
                "cte:xMunIni",
            ),
            "uf_origem": self.texto(
                ide,
                "cte:UFIni",
            ),
            "municipio_destino": self.texto(
                ide,
                "cte:xMunFim",
            ),
            "uf_destino": self.texto(
                ide,
                "cte:UFFim",
            ),

            "emitente_cnpj": self.texto_primeiro(
                emitente,
                [
                    "cte:CNPJ",
                    "cte:CPF",
                ],
            ),
            "emitente_nome": self.texto(
                emitente,
                "cte:xNome",
            ),

            "remetente_cnpj": self.texto_primeiro(
                remetente,
                [
                    "cte:CNPJ",
                    "cte:CPF",
                ],
            ),
            "remetente_nome": self.texto(
                remetente,
                "cte:xNome",
            ),

            "destinatario_cnpj": self.texto_primeiro(
                destinatario,
                [
                    "cte:CNPJ",
                    "cte:CPF",
                ],
            ),
            "destinatario_nome": self.texto(
                destinatario,
                "cte:xNome",
            ),

            "expedidor_cnpj": self.texto_primeiro(
                expedidor,
                [
                    "cte:CNPJ",
                    "cte:CPF",
                ],
            ),
            "expedidor_nome": self.texto(
                expedidor,
                "cte:xNome",
            ),

            "recebedor_cnpj": self.texto_primeiro(
                recebedor,
                [
                    "cte:CNPJ",
                    "cte:CPF",
                ],
            ),
            "recebedor_nome": self.texto(
                recebedor,
                "cte:xNome",
            ),

            "valor_carga": self.decimal(
                self.texto(
                    inf_carga,
                    "cte:vCarga",
                )
            ),

            "produto_predominante": (
                produto_predominante
            ),
            "sku_produto_predominante": sku,
            "descricao_produto_predominante": (
                descricao_produto
            ),

            "quantidade_nfe": len(chaves_nfe),
            "chaves_nfe": chaves_nfe,
            "chaves_nfe_texto": ";".join(
                chaves_nfe
            ),

            "tipo_documento_outro": (
                primeiro_documento.get(
                    "tipo",
                    "",
                )
            ),
            "descricao_documento_outro": (
                primeiro_documento.get(
                    "descricao",
                    "",
                )
            ),
            "numero_documento_outro": (
                primeiro_documento.get(
                    "numero",
                    "",
                )
            ),
            "documentos_outros": documentos_outros,
        }