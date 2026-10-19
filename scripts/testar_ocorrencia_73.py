import argparse
import os
import re
import sys

from dataclasses import dataclass
from typing import Callable

from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(
    PROJECT_ROOT / ".env",
    override=True,
)

PADRAO_CTRC = re.compile(r"[A-Z]{2,4}[0-9]{1,20}\Z")
VAR_AUTORIZACAO_MANUAL = "OC73_HOMOLOGACAO_MANUAL_AUTORIZADA"


@dataclass(frozen=True)
class FluxoManual:
    service_factory: Callable
    dry_run: bool
    envio_habilitado: bool
    max_lancamentos: int
    ctrc_teste: str


def _env_sim(nome: str) -> bool:
    return str(os.getenv(nome, "")).strip().upper() == "SIM"


def _criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Executa o fluxo existente da OC73 para um único CTRC "
            "explicitamente selecionado. Não inicia o scheduler."
        )
    )
    parser.add_argument(
        "--ctrc",
        action="append",
        required=True,
        metavar="SERIENUMERO",
        help="CTRC único, sem dígito, espaço ou separador (ex.: CWB123456).",
    )
    return parser


def _normalizar_ctrc(parser: argparse.ArgumentParser, valores: list[str]) -> str:
    if len(valores) != 1:
        parser.error("Informe exatamente um --ctrc.")

    ctrc = str(valores[0] or "").strip().upper()
    if PADRAO_CTRC.fullmatch(ctrc) is None:
        parser.error(
            "CTRC inválido; use série de 2 a 4 letras seguida do número."
        )
    return ctrc


def _carregar_fluxo_manual() -> FluxoManual:
    # Importação tardia: CTRC e limite são fixados antes de o módulo ler o env.
    from modules.ocorrencia_73.config import DRY_RUN
    from modules.ocorrencia_73.service import (
        CTRC_TESTE,
        MAX_LANCAMENTOS,
        OC73_ENVIO_HABILITADO,
        Ocorrencia73Service,
    )

    return FluxoManual(
        service_factory=Ocorrencia73Service,
        dry_run=DRY_RUN,
        envio_habilitado=OC73_ENVIO_HABILITADO,
        max_lancamentos=MAX_LANCAMENTOS,
        ctrc_teste=CTRC_TESTE,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _criar_parser()
    argumentos = parser.parse_args(argv)
    ctrc = _normalizar_ctrc(parser, argumentos.ctrc)

    # O argumento escolhe somente o CTRC. Não altera DRY_RUN, gate ou scheduler.
    os.environ["OCORRENCIA_73_CTRC_TESTE"] = ctrc
    os.environ["OCORRENCIA_73_MAX_LANCAMENTOS"] = "1"

    fluxo = _carregar_fluxo_manual()
    if fluxo.ctrc_teste != ctrc or fluxo.max_lancamentos != 1:
        raise RuntimeError(
            "Configuração manual não foi aplicada antes da carga do serviço."
        )

    if (
        not fluxo.dry_run
        and fluxo.envio_habilitado
        and not _env_sim(VAR_AUTORIZACAO_MANUAL)
    ):
        raise RuntimeError(
            "Homologação manual real não autorizada. "
            f"{VAR_AUTORIZACAO_MANUAL} deve ser SIM após aprovação separada."
        )

    print(
        "[OCORRENCIA 73] Iniciando fluxo manual para um único CTRC.",
        flush=True,
    )
    print(
        f"[OCORRENCIA 73] CTRC selecionado: {ctrc}",
        flush=True,
    )
    print(
        "[OCORRENCIA 73] Scheduler não é iniciado por este comando.",
        flush=True,
    )

    service = fluxo.service_factory()

    resultado = service.executar(
        triggered_by="homologacao_manual",
    )

    pprint(resultado)

    print(
        "[OCORRENCIA 73] Execução finalizada.",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as erro:
        print(
            f"[OCORRENCIA 73] Falha: {erro}",
            file=sys.stderr,
            flush=True,
        )
        raise
