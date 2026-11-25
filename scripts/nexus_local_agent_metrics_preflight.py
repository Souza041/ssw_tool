"""Preflight Windows de Métricas; não acessa o SSW e não escreve no MySQL."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.private_env import (
    SSW_RUNTIME_PREFIXES,
    load_private_environment,
)
from modules.local_agent.ssw_tasks import (
    TASK_METRICAS,
    selected_ssw_tasks,
    validate_ssw_runtime_environment,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-env", type=Path, required=True)
    parser.add_argument("--check-mysql", action="store_true")
    args = parser.parse_args()
    load_private_environment(args.agent_env, project_root=PROJECT_ROOT)
    if os.getenv("NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "false").lower() != "true":
        parser.error("Handlers SSW não estão habilitados no executor.")
    selected = selected_ssw_tasks()
    if selected != frozenset({TASK_METRICAS}):
        parser.error("Homologação exige allowlist Windows contendo somente ssw.metricas_op455.")
    runtime_value = os.getenv("NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE", "").strip()
    if not runtime_value:
        parser.error("Arquivo privado de runtime não configurado.")
    load_private_environment(
        Path(runtime_value), project_root=PROJECT_ROOT,
        allowed_prefixes=SSW_RUNTIME_PREFIXES,
    )
    validate_ssw_runtime_environment(selected)

    if platform.architecture()[0] != "64bit":
        parser.error("Handlers OP455 exigem Python Windows 64 bits.")
    for module_name in ("requests", "pymysql", "pandas", "openpyxl"):
        __import__(module_name)
    print("OK: runtime OP455 isolado; somente ssw.metricas_op455 registrado.")
    print("OK: dependências 64 bits e configuração presentes; nenhum segredo exibido.")

    if args.check_mysql:
        from modules.metricas.database import get_connection

        connection = get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                row = cursor.fetchone()
            if int(row["ok"]) != 1:
                raise RuntimeError("Resposta inesperada do MySQL.")
        finally:
            connection.close()
        print("OK: MySQL de Métricas acessível em consulta somente leitura.")
    else:
        print("NÃO EXECUTADO: conexão MySQL; use --check-mysql na janela aprovada.")
    print("NÃO EXECUTADO: login, relatório ou download no SSW.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
