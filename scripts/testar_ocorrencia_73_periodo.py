from datetime import date, timedelta

from modules.ocorrencia_73.service import Ocorrencia73Service


DATA_INICIAL = date(2026, 9, 14)
DATA_FINAL = date(2026, 9, 21)


def main():
    service = Ocorrencia73Service()

    data_atual = DATA_INICIAL

    while data_atual <= DATA_FINAL:
        print()
        print("=" * 70)
        print(
            "[TESTE PERIODO] "
            f"Executando {data_atual.strftime('%d/%m/%Y')}"
        )
        print("=" * 70)

        try:
            resultado = service.executar(
                data_referencia=data_atual,
                triggered_by="teste_periodo",
            )

            print(
                "[TESTE PERIODO] "
                f"{data_atual.strftime('%d/%m/%Y')} | "
                f"OP455={resultado.get('total_relatorio', 0)} | "
                f"FILTRADOS={resultado.get('total_filtrado', 0)}"
            )

        except Exception as erro:
            print(
                "[TESTE PERIODO] "
                f"ERRO em "
                f"{data_atual.strftime('%d/%m/%Y')}: "
                f"{erro}"
            )

        data_atual += timedelta(days=1)


if __name__ == "__main__":
    main()