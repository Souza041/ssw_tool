from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class VolumePublisher:
    def __init__(
        self,
        base_dir: Optional[str | Path] = None,
        current_filename: Optional[str] = None,
        history_limit: Optional[int] = None,
    ) -> None:
        configured_base_dir = os.getenv(
            "INCIDENTES_VOLUME_DATA_DIR",
            "data/incidentes/volume",
        )

        configured_filename = os.getenv(
            "INCIDENTES_VOLUME_CURRENT_FILENAME",
            "volume_atual.xlsx",
        )

        configured_history_limit = os.getenv(
            "INCIDENTES_VOLUME_HISTORY_LIMIT",
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
            self.history_limit = int(
                history_limit
            )
        else:
            try:
                self.history_limit = int(
                    configured_history_limit
                )
            except (
                TypeError,
                ValueError,
            ):
                self.history_limit = 30

    def publish(
        self,
        source_file: str | Path,
    ) -> dict:
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

        history_path = (
            self.history_dir
            / f"volume_{timestamp}{extension}"
        )

        current_path = (
            self.current_dir
            / self.current_filename
        )

        temporary_path = (
            self.current_dir
            / f".{self.current_filename}.tmp"
        )

        shutil.copy2(
            source_path,
            history_path,
        )

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

        removed_count = (
            self.cleanup_history()
        )

        return {
            "source": str(source_path),
            "current": str(current_path),
            "history": str(history_path),
            "history_removed": removed_count,
        }

    def cleanup_history(self) -> int:
        if (
            self.history_limit <= 0
            or not self.history_dir.exists()
        ):
            return 0

        history_files = sorted(
            (
                file
                for file
                in self.history_dir.iterdir()
                if (
                    file.is_file()
                    and file.name.startswith(
                        "volume_"
                    )
                )
            ),
            key=lambda file:
                file.stat().st_mtime,
            reverse=True,
        )

        removed = 0

        for old_file in history_files[
            self.history_limit:
        ]:
            try:
                old_file.unlink()
                removed += 1
            except OSError:
                logger.exception(
                    "Não foi possível remover "
                    "volume antigo: %s",
                    old_file,
                )

        return removed

    def _validate_source(
        self,
        source_file: str | Path,
    ) -> Path:
        source_path = Path(
            source_file
        ).expanduser()

        if not source_path.exists():
            raise FileNotFoundError(
                "Arquivo de volume não "
                f"encontrado: {source_path}"
            )

        if not source_path.is_file():
            raise ValueError(
                "Caminho de volume não é "
                f"arquivo: {source_path}"
            )

        if source_path.stat().st_size <= 0:
            raise ValueError(
                "Arquivo de volume vazio: "
                f"{source_path}"
            )

        return source_path.resolve()

    def _prepare_directories(
        self,
    ) -> None:
        self.current_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.history_dir.mkdir(
            parents=True,
            exist_ok=True,
        )