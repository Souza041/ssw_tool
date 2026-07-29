from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class IncidentPublisher:
    """
    Responsável por publicar o resultado final da auditoria de incidentes.

    Estrutura gerada:

    data/incidentes/
    ├── current/
    │   └── incidentes_atual.xlsx
    └── history/
        └── incidentes_20260729_121500.xlsx
    """

    def __init__(
        self,
        base_dir: Optional[str | Path] = None,
        current_filename: Optional[str] = None,
        history_limit: Optional[int] = None,
    ) -> None:
        configured_base_dir = os.getenv(
            "INCIDENTES_DATA_DIR",
            "data/incidentes",
        )

        configured_filename = os.getenv(
            "INCIDENTES_CURRENT_FILENAME",
            "incidentes_atual.xlsx",
        )

        configured_history_limit = os.getenv(
            "INCIDENTES_HISTORY_LIMIT",
            "30",
        )

        self.base_dir = Path(
            base_dir or configured_base_dir
        )

        self.current_dir = (
            self.base_dir / "current"
        )

        self.history_dir = (
            self.base_dir / "history"
        )

        self.current_filename = (
            current_filename
            or configured_filename
        )

        if history_limit is not None:
            self.history_limit = int(history_limit)
        else:
            try:
                self.history_limit = int(
                    configured_history_limit
                )
            except (TypeError, ValueError):
                self.history_limit = 30

    def publish(
        self,
        source_file: str | Path,
    ) -> dict:
        """
        Publica o arquivo final para uso do dashboard.

        Retorna:

        {
            "source": "...",
            "current": "...",
            "history": "...",
            "history_removed": 0
        }
        """

        source_path = self._validate_source(
            source_file
        )

        self._prepare_directories()

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        extension = (
            source_path.suffix.lower()
            or ".xlsx"
        )

        history_filename = (
            f"incidentes_{timestamp}{extension}"
        )

        history_path = (
            self.history_dir / history_filename
        )

        current_path = (
            self.current_dir
            / self.current_filename
        )

        temporary_path = (
            self.current_dir
            / f".{self.current_filename}.tmp"
        )

        logger.info(
            "Publicando resultado de incidentes. "
            "Origem=%s",
            source_path,
        )

        self._copy_to_history(
            source_path=source_path,
            history_path=history_path,
        )

        self._publish_current(
            source_path=source_path,
            temporary_path=temporary_path,
            current_path=current_path,
        )

        removed_count = self.cleanup_history()

        result = {
            "source": str(source_path),
            "current": str(current_path),
            "history": str(history_path),
            "history_removed": removed_count,
        }

        logger.info(
            "Resultado de incidentes publicado. "
            "Atual=%s Histórico=%s Removidos=%s",
            current_path,
            history_path,
            removed_count,
        )

        return result

    def cleanup_history(self) -> int:
        """
        Mantém somente os arquivos históricos mais recentes.
        """

        if self.history_limit <= 0:
            return 0

        if not self.history_dir.exists():
            return 0

        history_files = sorted(
            (
                file
                for file in self.history_dir.iterdir()
                if file.is_file()
                and file.name.startswith(
                    "incidentes_"
                )
            ),
            key=lambda file: file.stat().st_mtime,
            reverse=True,
        )

        files_to_remove = history_files[
            self.history_limit:
        ]

        removed_count = 0

        for old_file in files_to_remove:
            try:
                old_file.unlink()
                removed_count += 1

                logger.info(
                    "Histórico antigo removido: %s",
                    old_file,
                )
            except OSError:
                logger.exception(
                    "Não foi possível remover "
                    "o histórico antigo: %s",
                    old_file,
                )

        return removed_count

    def _validate_source(
        self,
        source_file: str | Path,
    ) -> Path:
        source_path = Path(
            source_file
        ).expanduser()

        if not source_path.exists():
            raise FileNotFoundError(
                "O arquivo gerado para publicação "
                f"não foi encontrado: {source_path}"
            )

        if not source_path.is_file():
            raise ValueError(
                "O caminho informado para publicação "
                f"não é um arquivo: {source_path}"
            )

        if source_path.stat().st_size <= 0:
            raise ValueError(
                "O arquivo gerado está vazio e não "
                f"pode ser publicado: {source_path}"
            )

        return source_path.resolve()

    def _prepare_directories(self) -> None:
        self.current_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.history_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    @staticmethod
    def _copy_to_history(
        source_path: Path,
        history_path: Path,
    ) -> None:
        shutil.copy2(
            source_path,
            history_path,
        )

    @staticmethod
    def _publish_current(
        source_path: Path,
        temporary_path: Path,
        current_path: Path,
    ) -> None:
        try:
            shutil.copy2(
                source_path,
                temporary_path,
            )

            temporary_path.replace(
                current_path
            )

        except Exception:
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

            raise