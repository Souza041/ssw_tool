import argparse
import json
import posixpath
import re
import uuid
from collections import defaultdict
from copy import copy
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZipFile

from openpyxl.utils import column_index_from_string

from .planejar_atualizacao_planilha import ler_planilha, sha_arquivo
from .regras_ocorrencias import (
    OCORRENCIAS_IGNORADAS,
    ocorrencia_ignorada,
)


TAG = rb"(?:[A-Za-z_][\w.-]*:)?"

ROW = re.compile(
    rb"<(?P<tag>" + TAG
    + rb"row)\b(?P<attrs>[^>]*?)(?<!/)>"
    + rb"(?P<body>.*?)</(?P=tag)\s*>",
    re.S,
)
CELL = re.compile(
    rb"<(?P<tag>" + TAG
    + rb"c)\b(?P<attrs>[^>]*?)"
    + rb"(?:\s*/>|>(?P<body>.*?)</(?P=tag)\s*>)",
    re.S,
)
REF = re.compile(rb"""(?:^|\s)r\s*=\s*(['"])(.*?)\1""")
TYPE = re.compile(rb"""\s+t\s*=\s*(['"]).*?\1""", re.S)


def celula_texto(tag, attrs, valor):
    prefixo = tag[:-1]
    attrs = TYPE.sub(b"", attrs)
    conteudo = escape(valor).encode("utf-8")

    return (
        b"<" + tag + attrs + b' t="inlineStr"><'
        + prefixo + b"is><"
        + prefixo + b't xml:space="preserve">'
        + conteudo
        + b"</" + prefixo + b"t></"
        + prefixo + b"is></" + tag + b">"
    )


def atualizar_xml(xml, alteracoes):
    por_linha = defaultdict(dict)

    for endereco, valor in alteracoes.items():
        partes = re.fullmatch(r"([A-Z]+)([1-9]\d*)", endereco)
        por_linha[int(partes[2])][endereco] = valor

    def coluna(endereco):
        letras = re.match(r"[A-Z]+", endereco)[0]
        return column_index_from_string(letras)

    def atualizar_linha(match):
        numero = int(REF.search(match["attrs"])[2])
        pendentes = por_linha.pop(numero, None)

        if pendentes is None:
            return match[0]

        corpo = match["body"]
        partes = []
        posicao = 0
        tag_nova = match["tag"][:-3] + b"c"

        def inserir(endereco):
            valor = pendentes.pop(endereco)
            attrs = b' r="' + endereco.encode("ascii") + b'"'
            return celula_texto(tag_nova, attrs, valor)

        for celula in CELL.finditer(corpo):
            endereco = REF.search(
                celula["attrs"]
            )[2].decode("ascii")

            partes.append(corpo[posicao:celula.start()])

            for novo in sorted(list(pendentes), key=coluna):
                if coluna(novo) < coluna(endereco):
                    partes.append(inserir(novo))

            if endereco in pendentes:
                partes.append(celula_texto(
                    celula["tag"],
                    celula["attrs"],
                    pendentes.pop(endereco),
                ))
            else:
                partes.append(celula[0])

            posicao = celula.end()

        for novo in sorted(list(pendentes), key=coluna):
            partes.append(inserir(novo))

        partes.append(corpo[posicao:])

        inicio = match[0][
            :match.start("body") - match.start()
        ]
        fim = match[0][
            match.end("body") - match.start():
        ]

        return inicio + b"".join(partes) + fim

    resultado = ROW.sub(atualizar_linha, xml)

    if por_linha:
        raise ValueError(
            "Existem linhas do plano ausentes na planilha."
        )

    ET.fromstring(resultado)
    return resultado


def endereco_aba(arquivo, nome):
    ns = {
        "s": (
            "http://schemas.openxmlformats.org/"
            "spreadsheetml/2006/main"
        )
    }
    relacionamento = (
        "{http://schemas.openxmlformats.org/"
        "officeDocument/2006/relationships}id"
    )

    workbook = ET.fromstring(arquivo.read("xl/workbook.xml"))
    rels = ET.fromstring(
        arquivo.read("xl/_rels/workbook.xml.rels")
    )

    for aba in workbook.find("s:sheets", ns):
        if aba.attrib["name"] == nome:
            identificador = aba.attrib[relacionamento]

            for rel in rels:
                if rel.attrib["Id"] == identificador:
                    if rel.attrib.get("TargetMode") == "External":
                        raise ValueError("Relacionamento externo inesperado.")

                    destino = rel.attrib["Target"]

                    if destino.startswith("/"):
                        return posixpath.normpath(destino.lstrip("/"))

                    return posixpath.normpath(
                        posixpath.join("xl", destino)
                    )

    raise ValueError("Aba não encontrada no arquivo.")


def validar_plano(plano, caminho):
    if plano.get("versao") != 1 or plano.get("aba") != "Base termo":
        raise ValueError("Versão ou aba do plano inválida.")

    if plano["ocorrencias_ignoradas"] != sorted(
        OCORRENCIAS_IGNORADAS
    ):
        raise ValueError(
            "As regras de ocorrência mudaram. Gere outro plano."
        )

    if sha_arquivo(caminho) != plano["planilha_sha256"]:
        raise ValueError(
            "A planilha mudou desde a geração do plano."
        )

    linhas, _, colunas = ler_planilha(caminho)

    if colunas != plano["colunas"]:
        raise ValueError("O mapeamento das colunas mudou.")

    por_linha = {item["linha"]: item for item in linhas}
    permitidas = {
        colunas["ocorrencia"],
        *colunas["pacotes"],
        *colunas["ctrcs"],
    }
    alteracoes = {}

    for item in plano["alteracoes"]:
        endereco = item["celula"]
        partes = re.fullmatch(r"([A-Z]+)([1-9]\d*)", endereco)

        if partes is None:
            raise ValueError(f"Endereço inválido: {endereco}")

        coluna, numero = partes[1], int(partes[2])

        if coluna not in permitidas or numero < 2:
            raise ValueError(f"Célula fora do escopo: {endereco}")

        if endereco in alteracoes:
            raise ValueError(f"Célula repetida no plano: {endereco}")

        linha = por_linha.get(numero)
        if linha is None or linha["cte"] != item["cte"]:
            raise ValueError(f"CT-e incompatível em {endereco}")

        if (
            linha["tipos"][coluna] == "f"
            or linha["tipos"][coluna] != item["tipo_anterior"]
            or linha["valores"][coluna] != item["anterior"]
        ):
            raise ValueError(f"Valor anterior incompatível em {endereco}")

        novo = item["novo"]
        if not isinstance(novo, str) or not novo.strip():
            raise ValueError(f"Novo valor inválido em {endereco}")

        if coluna in colunas["ctrcs"] and novo != item["ctrc"]:
            raise ValueError(f"CTRC incompatível em {endereco}")

        if coluna == colunas["ocorrencia"]:
            if not novo.isdigit() or ocorrencia_ignorada(novo):
                raise ValueError(f"Ocorrência inválida em {endereco}")

        alteracoes[endereco] = novo

    if len(alteracoes) != plano["resumo"]["celulas_com_alteracoes"]:
        raise ValueError("Contagem de alterações incompatível.")

    return alteracoes


def aplicar(arquivo_plano, executar=False):
    arquivo_plano = Path(arquivo_plano).resolve()
    plano = json.loads(
        arquivo_plano.read_text(encoding="utf-8")
    )
    caminho = Path(plano["planilha"]).resolve()

    if caminho.suffix.lower() != ".xlsx":
        raise ValueError("O aplicador exige um arquivo .xlsx.")

    alteracoes = validar_plano(plano, caminho)
    destino = caminho.with_name(
        f"{caminho.stem}_atualizada_{uuid.uuid4().hex[:8]}.xlsx"
    )

    criado = False

    try:
        with ZipFile(caminho) as original:
            nomes = original.namelist()

            if len(nomes) != len(set(nomes)):
                raise ValueError("Arquivo contém componentes duplicados.")

            alvo = endereco_aba(original, "Base termo")
            xml_novo = atualizar_xml(
                original.read(alvo), alteracoes
            )

            if not executar:
                print(json.dumps({
                    "status": "validado",
                    "celulas": len(alteracoes),
                    "resumo": plano["resumo"],
                }, ensure_ascii=False, indent=2))
                print("Simulação: nenhuma cópia foi gravada.")
                return

            # Criação exclusiva: não sobrescreve arquivos existentes.
            with destino.open("xb") as arquivo_saida:
                criado = True

                with ZipFile(arquivo_saida, "w") as atualizado:
                    atualizado.comment = original.comment

                    for info in original.infolist():
                        conteudo = (
                            xml_novo
                            if info.filename == alvo
                            else original.read(info.filename)
                        )
                        atualizado.writestr(copy(info), conteudo)

            # Verifica o conteúdo de todos os componentes.
            with ZipFile(destino) as atualizado:
                if atualizado.namelist() != nomes:
                    raise RuntimeError("Lista de componentes alterada.")

                if atualizado.comment != original.comment:
                    raise RuntimeError("Comentário do arquivo alterado.")

                for nome in nomes:
                    esperado = (
                        xml_novo if nome == alvo
                        else original.read(nome)
                    )

                    if atualizado.read(nome) != esperado:
                        raise RuntimeError(
                            f"Componente divergente: {nome}"
                        )

        if sha_arquivo(caminho) != plano["planilha_sha256"]:
            raise RuntimeError(
                "O original mudou durante a aplicação."
            )

        print(json.dumps({
            "status": "concluida",
            "celulas_atualizadas": len(alteracoes),
            "arquivo_gerado": str(destino),
            "arquivo_sha256": sha_arquivo(destino),
            "original_preservado": True,
            "demais_componentes_verificados": True,
        }, ensure_ascii=False, indent=2))

    except BaseException:
        if criado:
            destino.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Aplica um plano em uma cópia da planilha."
    )
    parser.add_argument("--plano", required=True)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    aplicar(args.plano, args.aplicar)


if __name__ == "__main__":
    main()
