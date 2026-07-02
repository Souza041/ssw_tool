from datetime import datetime, timedelta
from pathlib import Path

from operations.incidentes.workflow import executar_incidentes_op930


def log(msg: str):
    print(f"[INCIDENTES] {msg}", flush=True)


def main():
    hoje = datetime.now()
    inicio = hoje - timedelta(days=30)

    arquivo = executar_incidentes_op930(
        data_inicial=inicio.strftime("%d%m%y"),
        data_final=hoje.strftime("%d%m%y"),
        output_dir=Path("downloads"),
        log_func=log,
    )

    print(f"Arquivo final: {arquivo}")


if __name__ == "__main__":
    main()