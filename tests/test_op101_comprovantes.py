from pathlib import Path

from operations.op101.batch_comprovantes import processar_planilha_comprovantes
from operations.op101.comprovantes import OP101Comprovantes
from ssw.client import SSWClient


def main():
    client = SSWClient()
    client.login()
    client.open_menu()

    op101 = OP101Comprovantes(client)

    arquivo = processar_planilha_comprovantes(
        op101=op101,
        input_file=Path("data/teste_comprovantes.xlsx"),
        output_file=Path("downloads/teste_comprovantes_processado.xlsx"),
        data_ini="280326",
        data_fin="260626",
    )

    print(f"Arquivo processado: {arquivo}")


if __name__ == "__main__":
    main()