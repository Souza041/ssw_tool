from pathlib import Path

from operations.op001.batch_transporte import processar_planilha_transporte
from operations.op001.coleta import OP001Coleta
from ssw.client import SSWClient


def main() -> None:
    client = SSWClient()

    client.login()
    client.open_menu()

    op001 = OP001Coleta(client)
    op001.open(unidade="CWB")

    arquivo_saida = processar_planilha_transporte(
        op001=op001,
        input_file=Path("data/coletas_transporte_teste.xlsx"),
        output_file=Path("downloads/coletas_transporte_processadas.xlsx"),
    )

    print(f"Planilha processada: {arquivo_saida}")


if __name__ == "__main__":
    main()