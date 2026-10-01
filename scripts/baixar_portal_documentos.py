from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.collectors.portal import GCEPortalCollector


def _print_files(
    label: str,
    paths: tuple[Path, ...] | list[Path],
) -> None:
    print(
        f"\n{label}="
        f"{len(paths)} arquivo(s)"
    )

    for path in paths:
        print(f"  - {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Baixa os relatórios diários "
            "do Portal GCE."
        )
    )

    parser.add_argument(
        "--data-referencia",
        type=lambda value: datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date(),
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/documentos"),
    )

    args = parser.parse_args()

    files = GCEPortalCollector().download_daily(
        args.output_root,
        args.data_referencia,
    )

    print("\n=== ARQUIVOS COLETADOS ===")

    _print_files(
        "PORTAL_SOLUCIONAR",
        files.solucionar_files,
    )

    _print_files(
        "PORTAL_AGUARDANDO_SOLUCAO",
        files.aguardando_solucao_files,
    )

    _print_files(
        "PORTAL_FINALIZADOS",
        files.finalizados_files,
    )


if __name__ == "__main__":
    main()