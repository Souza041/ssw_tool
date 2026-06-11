from pathlib import Path

from operations.op103.report import OP103Report
from ssw.client import SSWClient


def main() -> None:
    client = SSWClient()

    client.login()
    client.open_menu()

    op103 = OP103Report(client)

    arquivo = op103.gerar_e_baixar_devolucao(
        output_dir=Path("downloads"),
        data_inicial="010526",
        data_final="010626",
        unidade_base="CWB",
        unidade_coleta="CWB",
        unidade_destinataria="CWB",
        tipo_consulta="coleta",  # coleta ou destinataria
    )

    print(f"Arquivo baixado com sucesso: {arquivo}")


if __name__ == "__main__":
    main()