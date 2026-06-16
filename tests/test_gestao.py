from pathlib import Path

from operations.gestao.workflow import executar_gestao_op455


def log_console(msg: str):
    print(f"[GESTAO] {msg}")


def main() -> None:
    arquivo = executar_gestao_op455(
        data_inicial="010626",
        data_final="110626",
        output_dir=Path("downloads"),
        log_func=log_console,
    )

    print(f"[GESTAO] Fluxo finalizado. Arquivo base: {arquivo}")


if __name__ == "__main__":
    main()