from pathlib import Path

from operations.op455.report import OP455Report
from ssw.client import SSWClient


def main() -> None:
    output_dir = Path("downloads")

    client = SSWClient()

    client.login()
    client.open_menu()

    op455 = OP455Report(client)

    arquivo = op455.gerar_e_baixar(
        output_dir=output_dir,
        dias_periodo=1,
        timeout_seconds=120,
    )

    print(f"Arquivo baixado com sucesso: {arquivo}")


if __name__ == "__main__":
    main()