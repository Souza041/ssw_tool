import json
from collections import Counter
from datetime import datetime
from time import sleep
from pymysql.err import OperationalError
from .database import conectar

import re

from .regras_ocorrencias import (
    OCORRENCIAS_IGNORADAS,
    ocorrencia_ignorada,
)

def texto(valor):
    return str(valor or "").strip()

def normalizar_pacote(valor):
    valor = " ".join(texto(valor).upper().split())

    formatos = (
        r"ROD\s+([A-Z0-9]{3})\s+(\d+)",
        r"([A-Z0-9]{3})\s*-\s*(\d+)",
    )

    for formato in formatos:
        encontrado = re.fullmatch(formato, valor)
        if encontrado:
            unidade, numero = encontrado.groups()
            return f"{unidade}-{numero}"

    # Formatos desconhecidos permanecem para comparação/revisão.
    return valor

def pacotes(dados, relatorio):
    campo = {
        "OP435": "NROPACOTE",
        "OP455": "NUMERODOPACOTEDEARQUIVAMENTO",
    }[relatorio]

    valores = set()
    encontrado = False

    for nome, valor in dados.items():
        nome_normalizado = "".join(nome.upper().split())
        if nome_normalizado == campo:
            encontrado = True
            valor = normalizar_pacote(valor)
            if valor:
                valores.add(valor)

    if not encontrado:
        raise ValueError(
            f"Coluna de pacote não encontrada em {relatorio}."
        )

    return valores


def momento(data, hora):
    valor = f"{texto(data)} {texto(hora)}"

    for formato in (
        "%d/%m/%y %H:%M",
        "%d/%m/%y %H:%M:%S",
    ):
        try:
            return datetime.strptime(valor, formato)
        except ValueError:
            pass

    raise ValueError("Data ou hora inválida na OP930.")

def acumular_ocorrencia(eventos, dados):
    if ocorrencia_ignorada(dados["COD_OCOR"]):
        return

    ocorrencia = momento(
        dados["DATA_OCOR"], dados["HORA_OCOR"]
    )
    inclusao = momento(
        dados["DIA_INCLUSAO_OCOR"],
        dados["HORA_INCLUSAO_OCOR"],
    )
    codigo = texto(dados["COD_OCOR"])
    cancelado = texto(dados["CANCELADO"]).upper()

    if not codigo.isdigit():
        raise ValueError("Código de ocorrência inválido.")

    if cancelado not in {"SIM", "NAO", "NÃO"}:
        raise ValueError("Situação de cancelamento inválida.")

    codigo = str(int(codigo)).zfill(2)

    # Agrupa observações equivalentes entre arquivos.
    chave = (
        ocorrencia,
        inclusao,
        codigo,
        texto(dados["EMPR_OCOR"]),
        texto(dados["UNID_OCOR"]),
    )
    eventos.setdefault(chave, set()).add(cancelado == "SIM")

def selecionar_ultima(eventos):
    # Sem identificador único do evento, estados contraditórios
    # ficam para revisão, em vez de escolher um arbitrariamente.
    if any(len(estados) != 1 for estados in eventos.values()):
        return "cancelamento_divergente", None

    validos = [
        chave
        for chave, estados in eventos.items()
        if estados == {False}
    ]

    if not validos:
        return "sem_ocorrencia_valida", None

    maior = max((chave[0], chave[1]) for chave in validos)

    codigos = {
        chave[2]
        for chave in validos
        if (chave[0], chave[1]) == maior
    }

    if len(codigos) != 1:
        return "empate_entre_codigos", None

    return "resolvida", next(iter(codigos))

def consultar_com_retry(sql, parametros=()):
    for tentativa in range(1, 4):
        conexao = None

        try:
            conexao = conectar()

            with conexao.cursor() as cursor:
                cursor.execute("SELECT DATABASE() AS banco")

                if cursor.fetchone()["banco"] != "documentos_lg":
                    raise RuntimeError(
                        "Banco conectado não é documentos_lg."
                    )

                cursor.execute(sql, parametros)
                return cursor.fetchall()

        except OperationalError as exc:
            codigo = exc.args[0] if exc.args else None

            if (
                codigo not in {1205, 1213, 2003, 2006, 2013}
                or tentativa == 3
            ):
                raise

            espera = (5, 15)[tentativa - 1]

        finally:
            if conexao is not None:
                conexao.close()

        print(
            "[MySQL] Consulta interrompida; "
            f"nova conexão em {espera}s "
            f"({tentativa + 1}/3).",
            flush=True,
        )
        sleep(espera)

def simular():
    conexao = conectar()

    try:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS banco")
            if cursor.fetchone()["banco"] != "documentos_lg":
                raise RuntimeError("Banco incorreto.")

            cursor.execute("""
                SELECT id
                FROM lg_importacoes
                WHERE relatorio = 'OP435'
                  AND status = 'concluida'
                ORDER BY id DESC
                LIMIT 1
            """)
            importacao = cursor.fetchone()
            if not importacao:
                raise RuntimeError("Nenhuma OP435 concluída.")

            importacao_id = importacao["id"]

            cursor.execute("""
                SELECT ctrc_chave, dados_json
                FROM lg_registros_importados
                WHERE importacao_id = %s
            """, (importacao_id,))

            base = {}

            for registro in cursor.fetchall():
                chave = registro["ctrc_chave"]
                if not chave:
                    raise ValueError("OP435 contém CTRC sem chave.")

                item = base.setdefault(chave, {
                    "OP435": set(),
                    "OP455": set(),
                    "eventos": {},
                    "erros_op930": 0,
                    "codigos_op435": set(),
                })

                dados435 = json.loads(registro["dados_json"])
                item["OP435"].update(pacotes(dados435, "OP435"))

                for nome, valor in dados435.items():
                    campo = "".join(nome.upper().split()).replace(".", "")

                    if campo == "ULTOCOR":
                        valor = texto(valor)
                        if valor.isdigit():
                            item["codigos_op435"].add(
                                str(int(valor)).zfill(2)
                            )

            chaves = sorted(base)

            for inicio in range(0, len(chaves), 300):
                lote = chaves[inicio:inicio + 300]
                marcadores = ",".join(["%s"] * len(lote))

                registros = consultar_com_retry(f"""
                    SELECT
                        i.relatorio,
                        r.ctrc_chave,
                        r.dados_json
                    FROM lg_registros_importados AS r
                    INNER JOIN lg_importacoes AS i
                        ON i.id = r.importacao_id
                    WHERE i.status = 'concluida'
                      AND i.relatorio IN ('OP455', 'OP930')
                      AND r.ctrc_chave IN ({marcadores})
                """, tuple(lote))

                for registro in registros:
                    item = base[registro["ctrc_chave"]]
                    dados = json.loads(registro["dados_json"])

                    if registro["relatorio"] == "OP455":
                        item["OP455"].update(pacotes(dados, "OP455"))
                    else:
                        try:
                            acumular_ocorrencia(item["eventos"], dados)
                        except (KeyError, ValueError, TypeError):
                            item["erros_op930"] += 1

                print(
                    f"Consultados {min(inicio + 100, len(chaves))}"
                    f"/{len(chaves)} CTRCs.",
                    flush=True,
                )

    finally:
        conexao.close()

    resumo_pacotes = Counter()
    resumo_ocorrencias = Counter()
    comparacao_op435 = Counter()
    amostras = []
    amostras_empates = []
    resolvidos = 0

    for chave in sorted(base):
        item = base[chave]
        candidatos = item["OP435"] | item["OP455"]

        if not candidatos:
            situacao_pacote = "sem_pacote"
        elif len(candidatos) == 1:
            situacao_pacote = "resolvido"
        else:
            situacao_pacote = "pacotes_divergentes"

        if item["erros_op930"]:
            situacao_ocorrencia = "dados_invalidos"
            codigo = None
        else:
            situacao_ocorrencia, codigo = selecionar_ultima(
                item["eventos"]
            )

        if (
            situacao_ocorrencia == "empate_entre_codigos"
            and len(amostras_empates) < 10
        ):
            validos = [
                evento
                for evento, estados in item["eventos"].items()
                if estados == {False}
            ]

            maior = max(
                (evento[0], evento[1])
                for evento in validos
            )

            amostras_empates.append({
                "ctrc": chave,
                "candidatos": [
                    {
                        "ocorrencia": evento[0].isoformat(),
                        "inclusao": evento[1].isoformat(),
                        "codigo": evento[2],
                        "empresa": evento[3],
                        "unidade": evento[4],
                    }
                    for evento in sorted(validos)
                    if (evento[0], evento[1]) == maior
                ],
            })

        referencias = item["codigos_op435"]

        if not referencias:
            comparacao_op435["sem_codigo_valido_op435"] += 1

        elif len(referencias) > 1:
            comparacao_op435["codigos_divergentes_na_op435"] += 1

        else:
            referencia = next(iter(referencias))

            if situacao_ocorrencia == "resolvida":
                resultado = (
                    "resolvida_confere_com_op435"
                    if codigo == referencia
                    else "resolvida_difere_da_op435"
                )
                comparacao_op435[resultado] += 1

            elif situacao_ocorrencia == "empate_entre_codigos":
                eventos_validos = [
                    evento
                    for evento, estados in item["eventos"].items()
                    if estados == {False}
                ]
                maior_horario = max(
                    (evento[0], evento[1])
                    for evento in eventos_validos
                )
                candidatos = {
                    evento[2]
                    for evento in eventos_validos
                    if (evento[0], evento[1]) == maior_horario
                }

                resultado = (
                    "empate_com_codigo_op435_entre_candidatos"
                    if referencia in candidatos
                    else "empate_sem_codigo_op435_entre_candidatos"
                )
                comparacao_op435[resultado] += 1

            else:
                comparacao_op435["outras_pendencias_op930"] += 1

        resumo_pacotes[situacao_pacote] += 1
        resumo_ocorrencias[situacao_ocorrencia] += 1

        if (
            situacao_pacote == "resolvido"
            and situacao_ocorrencia == "resolvida"
        ):
            resolvidos += 1
        elif len(amostras) < 10:
            amostras.append({
                "ctrc": chave,
                "pacotes_op435": sorted(item["OP435"]),
                "pacotes_op455": sorted(item["OP455"]),
                "situacao_pacote": situacao_pacote,
                "situacao_ocorrencia": situacao_ocorrencia,
                "codigo_ocorrencia": codigo,
            })

    print(json.dumps({
        "importacao_op435": importacao_id,
        "ctrcs": len(base),
        "pacotes": dict(resumo_pacotes),
        "ocorrencias": dict(resumo_ocorrencias),
        "pacote_e_ocorrencia_resolvidos": resolvidos,
        "amostras_para_revisao": amostras,
        "amostras_empates_ocorrencias": amostras_empates,
        "comparacao_com_op435": dict(comparacao_op435),
        "ocorrencias_ignoradas": sorted(OCORRENCIAS_IGNORADAS),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    simular()
