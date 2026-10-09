import csv
import re
import unicodedata

from pathlib import Path

from openpyxl import load_workbook

CLIENTE_COLUNA = "Cliente Pagador"
CIDADE_COLUNA = "Cidade do Destinatario"
UNIDADE_COLUNA = "Unidade Emissora"
CTRC_COLUNA = "Serie/Numero CTRC"
OCORRENCIA_COLUNA = "Codigo Ultima Ocorrencia"
UNIDADE_RECEPTORA_COLUNA = "Unidade Receptora"
UF_REMETENTE_COLUNA = "UF do Remetente"

ALIASES_COLUNAS = {
    CLIENTE_COLUNA: {
        "CLIENTE PAGADOR",
        "CLIENTE DO PAGADOR",
        "PAGADOR",
        "NOME PAGADOR",
    },
    CIDADE_COLUNA: {
        "CIDADE DO DESTINATARIO",
        "CIDADE DESTINATARIO",
        "CIDADE DO DESTINO",
        "CIDADE DESTINO",
    },
    UNIDADE_COLUNA: {
        "UNIDADE EMISSORA",
        "UNIDADE DE EMISSAO",
        "UNID EMISSORA",
        "FILIAL EMISSORA",
    },
    CTRC_COLUNA: {
        "SERIE/NUMERO CTRC",
        "SERIE NUMERO CTRC",
        "SERIE/NRO CTRC",
        "SERIE/NÚMERO CTRC",
    },
    OCORRENCIA_COLUNA: {
        "CODIGO ULTIMA OCORRENCIA",
        "CODIGO DA ULTIMA OCORRENCIA",
        "ULTIMA OCORRENCIA",
        "OCORRENCIA",
    },
    UNIDADE_RECEPTORA_COLUNA: {
        "UNIDADE RECEPTORA",
        "UNIDADE DE RECEPCAO",
        "UNID RECEPTORA",
        "FILIAL RECEPTORA",
    },
    UF_REMETENTE_COLUNA: {
        "UF DO REMETENTE",
        "UF REMETENTE",
    },
}


def normalizar_texto(valor: object) -> str:
    texto = str(valor or "")

    texto = texto.replace("\xa0", " ")
    texto = " ".join(texto.split())
    texto = texto.strip().upper()

    return texto


def normalizar_sem_acento(valor: object) -> str:
    texto = normalizar_texto(valor)

    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caractere) != "Mn"
    )

def aliases_normalizados(
    nome_esperado: str,
) -> set[str]:
    aliases = ALIASES_COLUNAS.get(
        nome_esperado,
        {nome_esperado},
    )

    return {
        normalizar_sem_acento(alias)
        for alias in aliases
    }


def detectar_encoding(arquivo: Path) -> str:
    conteudo = arquivo.read_bytes()

    # UTF-8 com BOM
    if conteudo.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    # UTF-16 LE com BOM
    if conteudo.startswith(b"\xff\xfe"):
        return "utf-16-le"

    # UTF-16 BE com BOM
    if conteudo.startswith(b"\xfe\xff"):
        return "utf-16-be"

    # Sem BOM, não tentamos UTF-16 automaticamente.
    for encoding in (
        "utf-8",
        "cp1252",
        "latin1",
    ):
        try:
            conteudo.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue

    return "latin1"


def localizar_cabecalho(
    linhas: list[list[str]],
) -> int:
    obrigatorias = (
        CLIENTE_COLUNA,
        CIDADE_COLUNA,
        UNIDADE_COLUNA,
        UNIDADE_RECEPTORA_COLUNA,
        CTRC_COLUNA,
        OCORRENCIA_COLUNA,
    )

    for indice, linha in enumerate(
        linhas[:50]
    ):
        colunas_normalizadas = {
            normalizar_sem_acento(coluna)
            for coluna in linha
            if normalizar_texto(coluna)
        }

        encontrou_todas = True

        for nome_esperado in obrigatorias:
            aliases = aliases_normalizados(
                nome_esperado
            )

            if not (
                aliases
                & colunas_normalizadas
            ):
                encontrou_todas = False
                break

        if encontrou_todas:
            return indice

    primeiras_linhas = []

    for indice, linha in enumerate(
        linhas[:10]
    ):
        valores = [
            normalizar_texto(valor)
            for valor in linha
            if normalizar_texto(valor)
        ]

        primeiras_linhas.append(
            f"Linha {indice + 1}: {valores}"
        )

    raise ValueError(
        "Não foi possível localizar o cabeçalho "
        "do relatório OP455.\n"
        + "\n".join(primeiras_linhas)
    )

def carregar_linhas_xlsx(
    arquivo: Path,
) -> list[list[str]]:
    workbook = load_workbook(
        filename=arquivo,
        read_only=True,
        data_only=True,
    )

    try:
        worksheet = workbook.active

        linhas = []

        for row in worksheet.iter_rows(
            values_only=True,
        ):
            linha = [
                "" if valor is None else str(valor)
                for valor in row
            ]

            linhas.append(linha)

        return linhas
    finally:
        workbook.close()

def carregar_relatorio(
    arquivo: Path,
) -> list[dict]:
    arquivo = Path(arquivo)

    if not arquivo.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {arquivo}"
        )

    extensao = arquivo.suffix.lower()

    if extensao in {".xlsx", ".xlsm"}:
        linhas = carregar_linhas_xlsx(arquivo)

    elif extensao in {
        ".sswweb",
        ".csv",
        ".txt",
    }:
        encoding = detectar_encoding(arquivo)

        try:
            texto = arquivo.read_text(
                encoding=encoding,
            )
        except UnicodeError:
            texto = arquivo.read_text(
                encoding="latin1",
                errors="replace",
            )

        amostra = texto[:5000]

        try:
            dialect = csv.Sniffer().sniff(
                amostra,
                delimiters=";,\t",
            )
            delimitador = dialect.delimiter
        except csv.Error:
            delimitador = ";"

        linhas = list(
            csv.reader(
                texto.splitlines(),
                delimiter=delimitador,
            )
        )

    else:
        raise ValueError(
            "Formato de arquivo não suportado: "
            f"{extensao or 'sem extensão'}"
        )

    indice_cabecalho = localizar_cabecalho(
        linhas
    )

    cabecalho = [
        normalizar_texto(coluna)
        for coluna in linhas[indice_cabecalho]
    ]

    registros = []

    for linha in linhas[indice_cabecalho + 1:]:
        if not any(
            normalizar_texto(valor)
            for valor in linha
        ):
            continue

        if len(linha) < len(cabecalho):
            linha += [""] * (
                len(cabecalho) - len(linha)
            )

        registro = {
            cabecalho[indice]: linha[indice]
            for indice in range(len(cabecalho))
        }

        registros.append(registro)

    return registros

def encontrar_coluna(
    registro: dict,
    nome_esperado: str,
) -> str:
    aliases = aliases_normalizados(
        nome_esperado
    )

    for coluna in registro:
        coluna_normalizada = (
            normalizar_sem_acento(
                coluna
            )
        )

        if coluna_normalizada in aliases:
            return coluna

    disponiveis = [
        str(coluna)
        for coluna in registro.keys()
    ]

    raise KeyError(
        "Coluna não encontrada no relatório: "
        f"{nome_esperado}. "
        f"Colunas disponíveis: {disponiveis}"
    )

def normalizar_codigo_ocorrencia(
    valor: object,
) -> str:
    texto = normalizar_texto(valor)

    if not texto:
        return ""

    match = re.match(
        r"^0*(\d+)",
        texto,
    )

    if not match:
        return texto

    return str(
        int(match.group(1))
    )

def filtrar_registros(
    registros: list[dict],
    clientes_permitidos: set[str] | None = None,
    rotas_permitidas: dict[str, set[str]] | None = None,
    *,
    fluxo: str,
) -> list[dict]:

    fluxo = normalizar_texto(fluxo)

    if fluxo not in {"CWB", "BIG"}:
        raise ValueError(
            f"Fluxo inválido para OC73: {fluxo}"
        )

    if not registros:
        return []

    exemplo = registros[0]

    coluna_ctrc = encontrar_coluna(
        exemplo, CTRC_COLUNA
    )
    coluna_ocorrencia = encontrar_coluna(
        exemplo, OCORRENCIA_COLUNA
    )

    # As colunas são exigidas somente quando
    # fazem parte das regras do respectivo fluxo.
    if fluxo == "CWB":
        coluna_unidade = encontrar_coluna(
            exemplo, UNIDADE_COLUNA
        )
        coluna_cidade = encontrar_coluna(
            exemplo, CIDADE_COLUNA
        )
    else:
        coluna_uf = encontrar_coluna(
            exemplo, UF_REMETENTE_COLUNA
        )

    emissoras_permitidas = {
        "BHZ", "CWB", "GRU", "JOI"
    }

    cidades_permitidas = {
        "CURITIBA",
        "ARAUCARIA",
        "CAMPO LARGO",
        "FAZENDA RIO GRANDE",
        "PINHAIS",
        "SAO JOSE DOS PINHAIS",
        "COLOMBO",
    }

    filtrados = []

    for registro in registros:

        ocorrencia = normalizar_codigo_ocorrencia(
            registro.get(coluna_ocorrencia)
        )

        if fluxo == "CWB":
            emissora = normalizar_sem_acento(
                registro.get(coluna_unidade)
            )
            cidade = normalizar_sem_acento(
                registro.get(coluna_cidade)
            )

            elegivel = (
                emissora in emissoras_permitidas
                and cidade in cidades_permitidas
                and ocorrencia == "63"
            )

        else:
            uf_remetente = normalizar_sem_acento(
                registro.get(coluna_uf)
            )

            elegivel = (
                uf_remetente == "SC"
                and ocorrencia == "64"
            )

        if not elegivel:
            continue

        ctrc = normalizar_texto(
            registro.get(coluna_ctrc)
        )

        if not ctrc:
            continue

        dados_ctrc = decompor_ctrc(ctrc)

        # Recupera os campos informativos que
        # também são utilizados pelo service.
        def obter_opcional(nome):
            try:
                coluna = encontrar_coluna(
                    exemplo, nome
                )
                return normalizar_texto(
                    registro.get(coluna)
                )
            except KeyError:
                return ""

        filtrados.append({
            "ctrc_original": ctrc,
            "serie": dados_ctrc["serie"],
            "numero": dados_ctrc["numero"],
            "digito": dados_ctrc["digito"],
            "cliente_pagador": obter_opcional(
                CLIENTE_COLUNA
            ),
            "cidade_destinatario": obter_opcional(
                CIDADE_COLUNA
            ),
            "unidade_emissora": obter_opcional(
                UNIDADE_COLUNA
            ),
            "unidade_receptora": obter_opcional(
                UNIDADE_RECEPTORA_COLUNA
            ),
            "uf_remetente": obter_opcional(
                UF_REMETENTE_COLUNA
            ),
            "ultima_ocorrencia": ocorrencia,
            "regra_filtro": (
                "cwb_oc63"
                if fluxo == "CWB"
                else "big_sc_oc64"
            ),
            "fluxo": fluxo,
            "registro_original": registro,
        })

    return filtrados

PADRAO_CTRC = re.compile(
    r"^\s*([A-Z]{2,4})\s*(\d+)(?:-(\d+))?\s*$",
    re.IGNORECASE,
)


def decompor_ctrc(
    valor: str,
) -> dict:
    texto = normalizar_texto(valor)

    match = PADRAO_CTRC.match(texto)

    if not match:
        raise ValueError(
            f"Formato de CTRC não reconhecido: {valor}"
        )

    return {
        "serie": match.group(1).upper(),
        "numero": match.group(2),
        "digito": match.group(3) or "",
        "original": texto,
    }

def diagnosticar_filtros(
    registros: list[dict],
    clientes_permitidos: set[str],
    rotas_permitidas: dict[str, set[str]],
) -> dict:
    if not registros:
        return {
            "total": 0,
            "rota": 0,
            "cliente": 0,
            "rota_antiga_cliente": 0,
            "cwb_curitiba": 0,
            "joi_florianopolis": 0,
            "joi_big": 0,
            "joi_big_oc64": 0,
            "joi_big_oc64_cliente": 0,
            "todos_filtros": 0,
            "rotas_encontradas": {},
            "clientes_encontrados": [],
            "cidades_encontradas": [],
            "unidades_encontradas": [],
        }

    exemplo = registros[0]

    coluna_cliente = encontrar_coluna(
        exemplo,
        CLIENTE_COLUNA,
    )
    coluna_cidade = encontrar_coluna(
        exemplo,
        CIDADE_COLUNA,
    )
    coluna_unidade = encontrar_coluna(
        exemplo,
        UNIDADE_COLUNA,
    )
    coluna_receptora = encontrar_coluna(
        exemplo,
        UNIDADE_RECEPTORA_COLUNA,
    )
    coluna_ocorrencia = encontrar_coluna(
        exemplo,
        OCORRENCIA_COLUNA,
    )

    clientes_normalizados = {
        normalizar_sem_acento(cliente)
        for cliente in clientes_permitidos
    }

    rotas_normalizadas = {
        normalizar_sem_acento(unidade): {
            normalizar_sem_acento(cidade)
            for cidade in cidades
        }
        for unidade, cidades
        in rotas_permitidas.items()
    }

    total_rota = 0
    total_cliente = 0

    total_rota_antiga_cliente = 0

    total_cwb_curitiba = 0
    total_joi_florianopolis = 0

    total_joi_big = 0
    total_joi_big_oc64 = 0
    total_joi_big_oc64_cliente = 0

    total_final = 0

    clientes_encontrados = set()
    cidades_encontradas = set()
    unidades_encontradas = set()

    rotas_encontradas: dict[str, int] = {}

    for registro in registros:
        cliente_original = normalizar_texto(
            registro.get(coluna_cliente)
        )

        cidade_original = normalizar_texto(
            registro.get(coluna_cidade)
        )

        unidade_original = normalizar_texto(
            registro.get(coluna_unidade)
        )

        receptora_original = normalizar_texto(
            registro.get(coluna_receptora)
        )

        cliente = normalizar_sem_acento(
            cliente_original
        )

        cidade = normalizar_sem_acento(
            cidade_original
        )

        unidade = normalizar_sem_acento(
            unidade_original
        )

        receptora = normalizar_sem_acento(
            receptora_original
        )

        ocorrencia = normalizar_codigo_ocorrencia(
            registro.get(coluna_ocorrencia)
        )

        if cliente_original:
            clientes_encontrados.add(
                cliente_original
            )

        if cidade_original:
            cidades_encontradas.add(
                cidade_original
            )

        if unidade_original:
            unidades_encontradas.add(
                unidade_original
            )

        cidades_validas = (
            rotas_normalizadas.get(
                unidade,
                set(),
            )
        )

        atende_rota_antiga = (
            cidade in cidades_validas
        )

        atende_cliente = (
            cliente in clientes_normalizados
        )

        if atende_rota_antiga:
            total_rota += 1

            chave_rota = (
                f"{unidade_original}"
                f" -> "
                f"{cidade_original}"
            )

            rotas_encontradas[chave_rota] = (
                rotas_encontradas.get(
                    chave_rota,
                    0,
                )
                + 1
            )

        if atende_cliente:
            total_cliente += 1

        # ==========================================
        # ROTAS ANTIGAS
        # ==========================================

        if atende_rota_antiga:
            total_rota_antiga_cliente += 1

            if (
                unidade == "CWB"
                and cidade == "CURITIBA"
            ):
                total_cwb_curitiba += 1

            if (
                unidade == "JOI"
                and cidade == "FLORIANOPOLIS"
            ):
                total_joi_florianopolis += 1

        # ==========================================
        # NOVA REGRA JOI / BIG / 64
        # ==========================================

        if (
            receptora == "BIG"
        ):
            total_joi_big += 1

            if ocorrencia == "64":
                total_joi_big_oc64 += 1

                if atende_cliente:
                    total_joi_big_oc64_cliente += 1

        # ==========================================
        # RESULTADO FINAL
        # ==========================================

        atende_nova_regra = (
            receptora == "BIG"
            and ocorrencia == "64"
            and atende_cliente
        )

        atende_regra_antiga = (
            atende_rota_antiga
        )

        if (
            atende_regra_antiga
            or atende_nova_regra
        ):
            total_final += 1

    return {
        "total": len(registros),
        "rota": total_rota,
        "cliente": total_cliente,
        "rota_antiga_cliente": (
            total_rota_antiga_cliente
        ),
        "cwb_curitiba": total_cwb_curitiba,
        "joi_florianopolis": (
            total_joi_florianopolis
        ),
        "joi_big": total_joi_big,
        "joi_big_oc64": total_joi_big_oc64,
        "joi_big_oc64_cliente": (
            total_joi_big_oc64_cliente
        ),
        "todos_filtros": total_final,
        "rotas_encontradas": dict(
            sorted(
                rotas_encontradas.items()
            )
        ),
        "clientes_encontrados": sorted(
            clientes_encontrados
        ),
        "cidades_encontradas": sorted(
            cidades_encontradas
        ),
        "unidades_encontradas": sorted(
            unidades_encontradas
        ),
    }
