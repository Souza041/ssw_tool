from ssw.client import SSWClient
from operations.op101.instructions import OP101Instructions


def main() -> None:
    client = SSWClient()

    client.login()
    client.open_menu()

    op101 = OP101Instructions(client)
    op101.open()

    op101.lancar_instrucao(
        serie_numero_ctrc="APU396103-6",
        texto="Teste via ssw_tool HTTP",
    )

    print("Instrução lançada com sucesso.")


if __name__ == "__main__":
    main()