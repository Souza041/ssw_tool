import argparse
import os
import sys

from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from modules.ocorrencia_73.service import Ocorrencia73Service
from operations.op455.report import OP455Report


def validar_travas():
    if os.getenv("OCORRENCIA_73_DRY_RUN", "").lower() != "true":
        raise RuntimeError("Homologação exige DRY_RUN=true.")

    if os.getenv("OCORRENCIA_73_ENABLED", "").lower() != "false":
        raise RuntimeError("Homologação exige ENABLED=false.")

    if os.getenv("OCORRENCIA_73_MAX_LANCAMENTOS", "") != "0":
        raise RuntimeError("Homologação exige MAX_LANCAMENTOS=0.")

    if os.getenv("OC73_ENVIO_HABILITADO", "").upper() == "SIM":
        raise RuntimeError("Envio OC73 deve permanecer bloqueado.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fluxo",
        choices=["CWB", "BIG"],
        required=True,
    )
    parser.add_argument(
        "--data",
        required=True,
        help="Data de referência no formato AAAA-MM-DD",
    )
    args = parser.parse_args()

    validar_travas()

    data_referencia = date.fromisoformat(args.data)

    service = Ocorrencia73Service()
    client = service.criar_client_logado()
    op455 = OP455Report(client)

    output_dir = (
        ROOT
        / "downloads"
        / "ocorrencia_73"
        / "homologacao"
        / data_referencia.isoformat()
    )

    resultado = service.preparar_lotes_oc73(
        op455=op455,
        data_referencia=data_referencia,
        diretorio_original=output_dir,
        fluxo=args.fluxo,
    )

    print("\n=== HOMOLOGAÇÃO OC73 ===")
    print("Fluxo:", args.fluxo)
    print("Data:", data_referencia.isoformat())
    print("Total OP455:", resultado["total_relatorio"])
    print("Total elegíveis:", resultado["total_filtrado"])
    print("Quantidade de lotes:", len(resultado["lotes"]))

    for lote in resultado["lotes"]:
        print(
            f"{lote['fluxo']}: "
            f"{lote['total_relatorio']} registros / "
            f"{lote['total_filtrado']} filtrados"
        )

    print("\nNenhum lançamento OC73 foi solicitado.")


if __name__ == "__main__":
    main()