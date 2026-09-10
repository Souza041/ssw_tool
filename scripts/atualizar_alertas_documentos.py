from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.services.alerts import refresh_alerts


def main() -> None:
    parser = argparse.ArgumentParser(description="Atualiza alertas do portal de documentos.")
    parser.add_argument(
        "--data-referencia",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        help="Data usada no cálculo; por padrão usa a data atual.",
    )
    args = parser.parse_args()
    stats = refresh_alerts(args.data_referencia)
    print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
