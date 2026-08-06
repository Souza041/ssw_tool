from __future__ import annotations

import os

from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"

DIAS_RETENCAO = int(
    os.getenv(
        "LIMPEZA_CARRIER_DIAS",
        "15",
    )
)

SIMULACAO = (
    os.getenv(
        "LIMPEZA_DRY_RUN",
        "true",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "sim",
        "on",
    }
)


def formatar_tamanho(total_bytes: int) -> str:
    tamanho = float(total_bytes)

    for unidade in (
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
    ):
        if tamanho < 1024 or unidade == "TB":
            return f"{tamanho:.2f} {unidade}"

        tamanho /= 1024

    return f"{total_bytes} B"


def main() -> int:
    if not DOWNLOADS_DIR.exists():
        print(
            "[LIMPEZA] Pasta downloads não encontrada: "
            f"{DOWNLOADS_DIR}"
        )
        return 0

    limite = (
        datetime.now()
        - timedelta(days=DIAS_RETENCAO)
    ).timestamp()

    encontrados = 0
    removidos = 0
    espaco_liberado = 0
    erros = 0

    print()
    print("=" * 60)
    print("LIMPEZA DE ARQUIVOS CARRIER LG")
    print("=" * 60)
    print(
        f"Pasta........................: {DOWNLOADS_DIR}"
    )
    print(
        f"Retenção.....................: {DIAS_RETENCAO} dias"
    )
    print(
        "Modo.........................: "
        + (
            "SIMULAÇÃO"
            if SIMULACAO
            else "EXCLUSÃO REAL"
        )
    )
    print("-" * 60)

    for arquivo in DOWNLOADS_DIR.glob(
        "carrier_lg_*"
    ):
        if not arquivo.is_file():
            continue

        try:
            estatistica = arquivo.stat()
        except OSError as erro:
            erros += 1
            print(
                f"[ERRO] Não foi possível consultar "
                f"{arquivo.name}: {erro}"
            )
            continue

        if estatistica.st_mtime >= limite:
            continue

        encontrados += 1
        tamanho = estatistica.st_size

        if SIMULACAO:
            print(
                "[SIMULAÇÃO] Removeria: "
                f"{arquivo.name} "
                f"({formatar_tamanho(tamanho)})"
            )
            continue

        try:
            arquivo.unlink()

            removidos += 1
            espaco_liberado += tamanho

            print(
                "[REMOVIDO] "
                f"{arquivo.name} "
                f"({formatar_tamanho(tamanho)})"
            )

        except OSError as erro:
            erros += 1

            print(
                f"[ERRO] Falha ao remover "
                f"{arquivo.name}: {erro}"
            )

    print("-" * 60)
    print(
        f"Arquivos antigos encontrados.: {encontrados}"
    )
    print(
        f"Arquivos removidos............: {removidos}"
    )
    print(
        f"Espaço liberado...............: "
        f"{formatar_tamanho(espaco_liberado)}"
    )
    print(
        f"Erros.........................: {erros}"
    )
    print("=" * 60)
    print()

    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())