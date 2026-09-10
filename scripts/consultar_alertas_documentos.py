from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.services.dashboard import dashboard_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Consulta o resumo dos alertas documentais.")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(dashboard_snapshot(limit=args.limit), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
