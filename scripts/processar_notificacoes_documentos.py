from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.services.notifications import process_notifications


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa notificações do Documentos GCE.")
    parser.add_argument("--enviar", action="store_true", help="Dispara os e-mails; sem isso executa apenas prévia.")
    parser.add_argument("--limite", type=int, default=None, help="Limita a quantidade de alertas processados.")
    args = parser.parse_args()
    print(json.dumps(process_notifications(send=args.enviar, limit=args.limite), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
