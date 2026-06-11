from pathlib import Path

from operations.op488.report import OP488Report
from ssw.client import SSWClient


def main() -> None:
    client = SSWClient()

    client.login()
    client.open_menu()

    op488 = OP488Report(client)

    arquivo = op488.gerar_e_baixar(
        output_dir=Path("downloads"),
        unidade="CWB",
        cod_evento="5501",
        evento="INDENIZACAO DE MERCADORIAS",
        mes_comp="0526",
        sit_desp="X",
        sit_arq="T",
        timeout_seconds=300,
    )

    print(f"Arquivo baixado com sucesso: {arquivo}")


if __name__ == "__main__":
    main()