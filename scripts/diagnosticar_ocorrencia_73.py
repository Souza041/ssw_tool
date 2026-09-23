from pathlib import Path

from modules.ocorrencia_73.config import (
    CLIENTES_PERMITIDOS,
    ROTAS_PERMITIDAS,
)
from modules.ocorrencia_73.parser import (
    CLIENTE_COLUNA,
    CIDADE_COLUNA,
    CTRC_COLUNA,
    OCORRENCIA_COLUNA,
    UNIDADE_COLUNA,
    UNIDADE_RECEPTORA_COLUNA,
    carregar_relatorio,
    encontrar_coluna,
    filtrar_registros,
    normalizar_codigo_ocorrencia,
    normalizar_sem_acento,
    normalizar_texto,
)


# ==========================================================
# ALTERE SOMENTE ESTE CAMINHO
# ==========================================================

ARQUIVO = Path(
    r"downloads\ocorrencia_73\2026-09-23\original\CSVROD00703333.sswweb"
)


def main() -> None:
    print()
    print("=" * 70)
    print("DIAGNOSTICO OFFLINE - OCORRENCIA 73")
    print("=" * 70)

    if not ARQUIVO.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {ARQUIVO}"
        )

    print(f"Arquivo: {ARQUIVO}")

    registros = carregar_relatorio(
        ARQUIVO
    )

    print(
        f"Registros OP455: {len(registros)}"
    )

    if not registros:
        print("Relatório vazio.")
        return

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

    coluna_ctrc = encontrar_coluna(
        exemplo,
        CTRC_COLUNA,
    )

    print()
    print("COLUNAS IDENTIFICADAS")
    print("-" * 70)
    print(
        f"Cliente................: {coluna_cliente}"
    )
    print(
        f"Cidade.................: {coluna_cidade}"
    )
    print(
        f"Unidade Emissora.......: {coluna_unidade}"
    )
    print(
        f"Unidade Receptora......: {coluna_receptora}"
    )
    print(
        f"Última Ocorrência......: {coluna_ocorrencia}"
    )
    print(
        f"CTRC....................: {coluna_ctrc}"
    )

    clientes_normalizados = {
        normalizar_sem_acento(cliente)
        for cliente in CLIENTES_PERMITIDOS
    }

    total_joi = 0
    total_joi_big = 0
    total_joi_big_64 = 0
    total_joi_big_64_cliente = 0

    candidatos_big = []

    ocorrencias_joi_big = {}

    for registro in registros:
        unidade = normalizar_sem_acento(
            registro.get(coluna_unidade)
        )

        receptora = normalizar_sem_acento(
            registro.get(coluna_receptora)
        )

        cliente = normalizar_sem_acento(
            registro.get(coluna_cliente)
        )

        ocorrencia = (
            normalizar_codigo_ocorrencia(
                registro.get(coluna_ocorrencia)
            )
        )

        if unidade != "JOI":
            continue

        total_joi += 1

        if receptora != "BIG":
            continue

        total_joi_big += 1

        ocorrencias_joi_big[ocorrencia] = (
            ocorrencias_joi_big.get(
                ocorrencia,
                0,
            )
            + 1
        )

        if ocorrencia != "64":
            continue

        total_joi_big_64 += 1

        if cliente not in clientes_normalizados:
            continue

        total_joi_big_64_cliente += 1

        candidatos_big.append({
            "ctrc": normalizar_texto(
                registro.get(coluna_ctrc)
            ),
            "emissora": normalizar_texto(
                registro.get(coluna_unidade)
            ),
            "receptora": normalizar_texto(
                registro.get(coluna_receptora)
            ),
            "cidade": normalizar_texto(
                registro.get(coluna_cidade)
            ),
            "ocorrencia": ocorrencia,
            "cliente": normalizar_texto(
                registro.get(coluna_cliente)
            ),
        })

    print()
    print("NOVA REGRA JOI -> BIG -> OC64")
    print("-" * 70)

    print(
        f"Registros emitidos por JOI..........: "
        f"{total_joi}"
    )

    print(
        f"JOI + Unidade Receptora BIG.........: "
        f"{total_joi_big}"
    )

    print()
    print("Ocorrências encontradas em JOI -> BIG:")

    for codigo, total in sorted(
        ocorrencias_joi_big.items()
    ):
        print(
            f"  OC {codigo or 'VAZIA':<5}: "
            f"{total}"
        )

    print()

    print(
        f"JOI + BIG + última OC64..............: "
        f"{total_joi_big_64}"
    )

    print(
        f"JOI + BIG + OC64 + cliente..........: "
        f"{total_joi_big_64_cliente}"
    )

    if candidatos_big:
        print()
        print("CTRCs DA NOVA REGRA")
        print("-" * 70)

        for item in candidatos_big:
            print(
                f"{item['ctrc']} | "
                f"{item['emissora']} -> "
                f"{item['receptora']} | "
                f"{item['cidade']} | "
                f"OC {item['ocorrencia']} | "
                f"{item['cliente']}"
            )

    else:
        print()
        print(
            "Nenhum CTRC atendeu integralmente "
            "à nova regra."
        )

    # ======================================================
    # AGORA TESTA O FILTRO REAL DA AUTOMAÇÃO
    # ======================================================

    filtrados = filtrar_registros(
        registros=registros,
        clientes_permitidos=(
            CLIENTES_PERMITIDOS
        ),
        rotas_permitidas=(
            ROTAS_PERMITIDAS
        ),
    )

    print()
    print("RESULTADO DO FILTRO REAL")
    print("-" * 70)

    print(
        f"Total aprovado pelo parser..........: "
        f"{len(filtrados)}"
    )

    rota_antiga = [
        item
        for item in filtrados
        if item.get("regra_filtro")
        == "rota_antiga"
    ]

    nova_regra = [
        item
        for item in filtrados
        if item.get("regra_filtro")
        == "joi_big_oc64"
    ]

    print(
        f"Rotas antigas.......................: "
        f"{len(rota_antiga)}"
    )

    print(
        f"Nova JOI/BIG/64.....................: "
        f"{len(nova_regra)}"
    )

    print()

    if (
        len(nova_regra)
        != total_joi_big_64_cliente
    ):
        print(
            "[ALERTA] Divergência encontrada!"
        )
        print(
            "Contagem manual JOI/BIG/64/cliente "
            "não bate com o parser."
        )
    else:
        print(
            "[OK] Nova regra confere com "
            "a contagem independente."
        )

    print("=" * 70)
    print()


if __name__ == "__main__":
    main()