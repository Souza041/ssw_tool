"""Homologacao manual segura da autenticacao Chromium do SSW.

Nao cadastra coletas, nao persiste em banco e nao envia notificacoes. O fluxo GCE
exercita somente os downloads SSW originais consumidos pelo pipeline.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.collectors.ssw import SSWDocumentsCollector
from operations.op001.coleta import OP001Coleta
from ssw.browser_transport import discard_all_browser_transports
from ssw.client import SSWClient


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use AAAA-MM-DD.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "flow", choices=("menu", "op001", "gce-ssw"),
        help="menu autentica; op001 apenas abre o formulario; gce-ssw baixa relatorios sem banco.",
    )
    parser.add_argument("--inicio", type=_date)
    parser.add_argument("--fim", type=_date)
    parser.add_argument("--output", type=Path, default=Path("downloads/homologacao-ssw"))
    args = parser.parse_args()

    try:
        if args.flow == "gce-ssw":
            end = args.fim or date.today()
            start = args.inicio or (end - timedelta(days=1))
            result = SSWDocumentsCollector().download(args.output, start, end)
            print(f"OP455: {result.op455}")
            print(f"OP930: {result.op930}")
            print("Concluido sem escrita no banco e sem notificacoes.")
            return

        client = SSWClient()
        client.login()
        client.open_menu()
        if args.flow == "op001":
            OP001Coleta(client).open(unidade=client.unidade)
            print("OP001 aberta sem envio de SAVECOL e sem cadastro.")
        else:
            print("Dominio, usuario e unidade confirmados no menu oficial.")
    finally:
        discard_all_browser_transports()


if __name__ == "__main__":
    main()
