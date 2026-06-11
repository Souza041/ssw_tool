import argparse
from pathlib import Path

from operations.op101.instructions import OP101Instructions
from operations.op150.report import OP150Report
from operations.op103.report import OP103Report
from operations.op455.report import OP455Report
from operations.op488.report import OP488Report
from ssw.client import SSWClient
from ssw.logger import setup_logger


def criar_client_logado(logger):
    client = SSWClient()

    logger.info("Executando login HTTP...")
    client.login()
    client.open_menu()
    logger.info("Login concluído.")

    return client


def run_op101(args) -> None:
    logger = setup_logger("op101")

    client = criar_client_logado(logger)

    op101 = OP101Instructions(client)
    op101.open()

    logger.info("Lançando instrução no CTRC: %s", args.ctrc)

    op101.lancar_instrucao(
        serie_numero_ctrc=args.ctrc,
        texto=args.texto,
    )

    logger.info("Instrução lançada com sucesso.")

def run_op150(args) -> None:
    logger = setup_logger("op150")

    client = criar_client_logado(logger)

    op150 = OP150Report(client)

    arquivo = op150.gerar_e_baixar(
        output_dir=Path(args.output),
        data_inicial=args.data_inicial,
        data_final=args.data_final,
        unidade=args.unidade,
        nome_unidade=args.nome_unidade,
        f7=args.f7,
        f8=args.f8,
        f9=args.f9,
    )

    logger.info("Relatório OP150 baixado com sucesso: %s", arquivo)

def run_op103(args) -> None:
    logger = setup_logger("op103")

    client = criar_client_logado(logger)

    op103 = OP103Report(client)

    arquivo = op103.gerar_e_baixar_devolucao(
        output_dir=Path(args.output),
        data_inicial=args.data_inicial,
        data_final=args.data_final,
        unidade_base=args.unidade_base,
        unidade_coleta=args.unidade_coleta,
        unidade_destinataria=args.unidade_destinataria,
        tipo_consulta=args.tipo_consulta,
    )

    logger.info("Relatório OP103 baixado com sucesso: %s", arquivo)


def run_op455(args) -> None:
    logger = setup_logger("op455")

    client = criar_client_logado(logger)

    op455 = OP455Report(client)

    arquivo = op455.gerar_e_baixar(
        output_dir=Path(args.output),
        dias_periodo=args.dias,
        timeout_seconds=args.timeout,
    )

    logger.info("Relatório OP455 baixado com sucesso: %s", arquivo)

def run_op488(args) -> None:
    logger = setup_logger("op488")

    client = criar_client_logado(logger)

    op488 = OP488Report(client)

    arquivo = op488.gerar_e_baixar(
        output_dir=Path(args.output),
        unidade=args.unidade,
        cod_evento=args.cod_evento,
        evento=args.evento,
        mes_comp=args.mes_comp,
        sit_desp=args.sit_desp,
        sit_arq=args.sit_arq,
        timeout_seconds=args.timeout,
    )

    logger.info("Relatório OP488 baixado com sucesso: %s", arquivo)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SSW Tool - automações HTTP para SSW"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    op101 = subparsers.add_parser("op101-instrucao")
    op101.add_argument("--ctrc", required=True)
    op101.add_argument(
        "--texto",
        default="Pré-alerta enviado via e-mail",
    )
    op101.set_defaults(func=run_op101)

    op150 = subparsers.add_parser("op150-relatorio")
    op150.add_argument("--data-inicial", required=True)
    op150.add_argument("--data-final", required=True)
    op150.add_argument("--unidade", default="CWB")
    op150.add_argument("--nome-unidade", default="RODOBRAS TRANSP RODOVIARIOS")
    op150.add_argument("--f7", default="R")
    op150.add_argument("--f8", default="s")
    op150.add_argument("--f9", default="N")
    op150.add_argument("--output", default="downloads")
    op150.set_defaults(func=run_op150)

    op103 = subparsers.add_parser("op103-devolucao")

    op103.add_argument("--data-inicial", required=True)
    op103.add_argument("--data-final", required=True)

    op103.add_argument("--unidade-base", default="CWB")
    op103.add_argument("--unidade-coleta", default="CWB")
    op103.add_argument("--unidade-destinataria", default="CWB")

    op103.add_argument(
        "--tipo-consulta",
        choices=["coleta", "destinataria"],
        default="coleta",
    )

    op103.add_argument("--output", default="downloads")

    op103.set_defaults(func=run_op103)

    op455 = subparsers.add_parser("op455-relatorio")
    op455.add_argument("--dias", type=int, default=7)
    op455.add_argument("--timeout", type=int, default=300)
    op455.add_argument("--output", default="downloads")
    op455.set_defaults(func=run_op455)

    op488 = subparsers.add_parser("op488-relatorio")
    op488.add_argument("--unidade", default="CWB")
    op488.add_argument("--cod-evento", required=True)
    op488.add_argument("--evento", required=True)
    op488.add_argument("--mes-comp", required=True)
    op488.add_argument("--sit-desp", default="X")
    op488.add_argument("--sit-arq", default="T")
    op488.add_argument("--timeout", type=int, default=300)
    op488.add_argument("--output", default="downloads")
    op488.set_defaults(func=run_op488)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()