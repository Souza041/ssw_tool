from pathlib import Path

from operations.op150.report import OP150Report
from ssw.client import SSWClient


def main() -> None:
    client = SSWClient()

    client.login()
    client.open_menu()

    op150 = OP150Report(client)

    arquivo = op150.gerar_e_baixar(
        output_dir=Path("downloads"),
        data_inicial="010526",
        data_final="010626",
        unidade="CWB",
        nome_unidade="RODOBRAS TRANSP RODOVIARIOS",
    )

    print(f"Arquivo baixado com sucesso: {arquivo}")


if __name__ == "__main__":
    main()