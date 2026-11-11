"""Fail-closed isolation for a QA process on a shared host."""

import os
from pathlib import Path


def qa_enabled() -> bool:
    return os.getenv("SSW_QA_MODE", "").strip().casefold() in {
        "1", "true", "yes", "sim", "on",
    }


def qa_data_dir(project_root: Path) -> Path:
    configured = os.getenv("SSW_QA_DATA_DIR", "")
    if not configured or not Path(configured).is_absolute():
        raise RuntimeError("SSW_QA_DATA_DIR absoluto é obrigatório em QA.")
    root = Path(configured).resolve()
    project_root = project_root.resolve()
    if root == project_root or root in project_root.parents or project_root in root.parents:
        raise RuntimeError("SSW_QA_DATA_DIR deve ficar fora do código da aplicação.")
    whirlpool = os.getenv("WHIRLPOOL_DATA_DIR", "")
    if not whirlpool or not Path(whirlpool).is_absolute():
        raise RuntimeError("WHIRLPOOL_DATA_DIR absoluto é obrigatório em QA.")
    whirlpool_path = Path(whirlpool).resolve()
    if root not in whirlpool_path.parents:
        raise RuntimeError("WHIRLPOOL_DATA_DIR deve ficar dentro de SSW_QA_DATA_DIR.")
    return root
