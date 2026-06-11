from operations.op001.coleta import OP001Coleta
from ssw.client import SSWClient


def main() -> None:
    client = SSWClient()

    client.login()
    client.open_menu()

    op001 = OP001Coleta(client)
    op001.open(unidade="CWB")

    resultado = op001.salvar_coleta_reversa(
        solicitante="Eduardo",
        tipo_frete="F",
        cnpj_remetente="00001668693097",
        nota_fiscal="5321452",
        cnpj_destinatario="76487032004031",
        data_programada="080626",
        hora_limite="1800",
    )

    print(resultado)


if __name__ == "__main__":
    main()