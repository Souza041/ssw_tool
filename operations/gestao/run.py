import argparse
from datetime import datetime, timedelta
from pathlib import Path

import os

from operations.gestao.workflow import executar_gestao_op455


def log_console(msg: str) -> None:
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{agora} | GESTAO | {msg}", flush=True)


def periodo_padrao(dias: int) -> tuple[str, str]:
    hoje = datetime.now()
    inicio = hoje - timedelta(days=dias)

    return (
        inicio.strftime("%d%m%y"),
        hoje.strftime("%d%m%y"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Executa fluxo Gestão OP455: baixa relatório, trata, envia e-mails e lança OP101."
    )

    parser.add_argument("--data-inicial")
    parser.add_argument("--data-final")
    parser.add_argument("--dias", type=int, default=int(os.getenv("OP455_DIAS_PERIODO", "30")))
    parser.add_argument("--output", default="downloads")

    args = parser.parse_args()

    if args.data_inicial and args.data_final:
        data_inicial = args.data_inicial
        data_final = args.data_final
    else:
        data_inicial, data_final = periodo_padrao(args.dias)

    log_console(f"Período: {data_inicial} até {data_final}")

    arquivo = executar_gestao_op455(
        data_inicial=data_inicial,
        data_final=data_final,
        output_dir=Path(args.output),
        log_func=log_console,
    )

    log_console(f"Fluxo finalizado. Arquivo base: {arquivo}")


if __name__ == "__main__":
    main()