"""Carregamento explícito de ambiente privado, sem expor valores."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from dotenv import dotenv_values, load_dotenv


class PrivateEnvironmentError(RuntimeError):
    pass


SSW_RUNTIME_PREFIXES = (
    "SSW_", "DOCUMENTOS_", "GCE_", "AUTOMATION_DB_", "HEALTH_",
    "METRICAS_", "OCORRENCIA_73_", "OC73_", "SMTP_",
    "PROXY_",
)


def load_private_environment(
    path: Path,
    *,
    project_root: Path | None = None,
    allowed_prefixes: tuple[str, ...] = (
        "NEXUS_LOCAL_AGENT_", "NEXUS_SSW_", "NEXUS_OP001_",
    ),
) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_absolute() or not resolved.is_file():
        raise PrivateEnvironmentError("Arquivo privado de ambiente não encontrado.")
    if project_root is not None:
        root = Path(project_root).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            pass
        else:
            raise PrivateEnvironmentError(
                "O arquivo privado de ambiente deve ficar fora do repositório."
            )
    if os.name != "nt":
        mode = stat.S_IMODE(resolved.stat().st_mode)
        if mode & 0o077:
            raise PrivateEnvironmentError(
                "Permissões inseguras no arquivo privado; esperado 0600."
            )
        if hasattr(os, "geteuid") and resolved.stat().st_uid != os.geteuid():
            raise PrivateEnvironmentError(
                "O arquivo privado não pertence ao usuário efetivo do processo."
            )
    values = dotenv_values(resolved)
    if not values:
        raise PrivateEnvironmentError("Arquivo privado de ambiente vazio ou inválido.")
    if any(not str(key).startswith(allowed_prefixes) for key in values):
        raise PrivateEnvironmentError(
            "O arquivo privado contém variáveis fora da allowlist permitida."
        )
    if not load_dotenv(resolved, override=True):
        raise PrivateEnvironmentError("Falha ao carregar o arquivo privado de ambiente.")
    return resolved
