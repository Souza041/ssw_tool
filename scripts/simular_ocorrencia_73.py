import os
import sys
from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env", override=True)

# Proteções exclusivas desta execução.
os.environ["OCORRENCIA_73_DRY_RUN"] = "true"
os.environ["OCORRENCIA_73_MAX_LANCAMENTOS"] = "0"

from modules.ocorrencia_73.config import DRY_RUN
from modules.ocorrencia_73.service import (
    MAX_LANCAMENTOS,
    Ocorrencia73Service,
)

if DRY_RUN is not True or MAX_LANCAMENTOS != 0:
    raise RuntimeError(
        "Simulação bloqueada: configurações inseguras."
    )

print("[SIMULACAO] DRY_RUN =", DRY_RUN, flush=True)
print(
    "[SIMULACAO] MAX_LANCAMENTOS =",
    MAX_LANCAMENTOS,
    flush=True,
)

if __name__ == "__main__":
    service = Ocorrencia73Service()

    resultado = service.executar(
        triggered_by="simulacao_manual",
    )

    pprint(resultado)

    print(
        "[SIMULACAO] Execução concluída.",
        flush=True,
    )
