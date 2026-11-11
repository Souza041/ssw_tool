import argparse
import hashlib
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


PERFIS = {
    "OP455": {
        "metodo": "gerar_e_baixar_documentos",
        "unidade": "MTZ",
        "versao": 1,
    },
    "OP930": {
        "metodo": "gerar_e_baixar_por_cnpj",
        "unidade": "CWB",
        "grupo": "LG",
        "cnpj": "01166372000155",
        "versao": 1,
    },
}


def periodos_mensais(inicio, fim):
    if inicio > fim:
        raise ValueError(
            "O início deve ser anterior ou igual ao fim."
        )

    atual = inicio

    while atual <= fim:
        proximo = (
            date(atual.year + 1, 1, 1)
            if atual.month == 12
            else date(atual.year, atual.month + 1, 1)
        )

        ultimo = min(
            fim,
            proximo - timedelta(days=1),
        )

        yield atual, ultimo
        atual = ultimo + timedelta(days=1)


def preparar(inicio, fim):
    tarefas = []

    for ini, final in periodos_mensais(inicio, fim):
        for relatorio, perfil in PERFIS.items():
            texto = json.dumps(
                perfil,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

            digest = hashlib.sha256(
                texto.encode("utf-8")
            ).hexdigest()

            tarefas.append((
                relatorio,
                digest,
                ini,
                final,
                texto,
            ))

    return tarefas


def gravar(tarefas):
    from .database import conectar

    conexao = conectar()

    try:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS banco")

            if cursor.fetchone()["banco"] != "documentos_lg":
                raise RuntimeError(
                    "O banco conectado não é documentos_lg."
                )

            for tarefa in tarefas:
                cursor.execute(
                    """
                    INSERT INTO lg_carga_periodos (
                        relatorio,
                        perfil_sha256,
                        inicio_periodo,
                        fim_periodo,
                        perfil_json
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        id = LAST_INSERT_ID(id)
                    """,
                    tarefa,
                )

        conexao.commit()

    except Exception:
        conexao.rollback()
        raise

    finally:
        conexao.close()


def main():
    ontem = (
        datetime.now(
            ZoneInfo("America/Sao_Paulo")
        ).date()
        - timedelta(days=1)
    )

    parser = argparse.ArgumentParser(
        description=(
            "Planeja a carga histórica LG. "
            "Não acessa o SSW."
        )
    )

    parser.add_argument(
        "--inicio",
        type=date.fromisoformat,
        default=date(2024, 1, 1),
    )

    parser.add_argument(
        "--fim",
        type=date.fromisoformat,
        default=ontem,
    )

    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Grava as tarefas pendentes no MySQL.",
    )

    args = parser.parse_args()

    if args.inicio > args.fim or args.fim > ontem:
        parser.error(
            "Use início até o fim, "
            "e data final até ontem."
        )

    tarefas = preparar(args.inicio, args.fim)

    print(
        "Períodos mensais por relatório: "
        f"{len(tarefas) // len(PERFIS)}"
    )
    print(f"Tarefas OP455 + OP930: {len(tarefas)}")

    print(
        f"Primeiro período: "
        f"{tarefas[0][2]} a {tarefas[0][3]}"
    )
    print(
        f"Último período: "
        f"{tarefas[-1][2]} a {tarefas[-1][3]}"
    )

    if args.aplicar:
        gravar(tarefas)
        print(
            "Plano gravado. O estado das tarefas "
            "existentes foi preservado."
        )
    else:
        print("Simulação: nenhuma gravação no banco.")

    print("Nenhum relatório foi solicitado ao SSW.")


if __name__ == "__main__":
    main()
