"""Diagnóstico local sanitizado; não realiza chamadas de rede."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.config import load_executor_settings, load_server_settings
from modules.local_agent.private_env import load_private_environment


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico offline do Local Agent.")
    parser.add_argument("--role", choices=("server", "executor"), required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    load_private_environment(args.env_file, project_root=PROJECT_ROOT)
    if args.role == "executor":
        handlers = os.getenv(
            "NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "false"
        ).strip().lower() in {"1", "true", "yes", "sim", "on"}
        if handlers:
            from modules.local_agent.private_env import SSW_RUNTIME_PREFIXES
            from modules.local_agent.ssw_tasks import validate_ssw_runtime_environment

            runtime_value = os.getenv("NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE", "").strip()
            if not runtime_value:
                raise RuntimeError("Arquivo de runtime SSW não configurado.")
            load_private_environment(
                Path(runtime_value),
                project_root=PROJECT_ROOT,
                allowed_prefixes=SSW_RUNTIME_PREFIXES,
            )
            validate_ssw_runtime_environment()
        settings = load_executor_settings()
        host = urlparse(settings.server_url).hostname or "indefinido"
        print(f"OK: executor {settings.agent_id}; servidor HTTPS em {host}; token configurado.")
        print(
            "Capacidades validadas: system.ping"
            + (" e handlers SSW permitidos" if handlers else "")
        )
        return 0

    settings = load_server_settings()
    if settings.database_path != Path("/var/lib/nexus/local-agent.sqlite3"):
        raise RuntimeError("Caminho de produção do SQLite fora do padrão aprovado.")
    print(
        "OK: servidor configurado; Local Agent="
        f"{'ativo' if settings.enabled else 'inativo'}; bootstrap="
        f"{'ativo' if settings.bootstrap_enabled else 'inativo'}."
    )
    print(f"Allowlist global: {','.join(sorted(settings.allowed_tasks))}")
    if settings.database_path.exists():
        if os.name != "nt" and (settings.database_path.stat().st_mode & 0o077):
            raise RuntimeError("Permissões do SQLite diferentes de 0600.")
        with sqlite3.connect(str(settings.database_path)) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("Falha na integridade do SQLite.")
        print("OK: SQLite íntegro e acessível pelo usuário efetivo.")
    else:
        print("PENDENTE: SQLite ainda não criado; será inicializado após ativação aprovada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
