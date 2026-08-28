from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Dict, List, Optional

import pandas as pd


ABAS_ANALYTICS = {
    "AUDITORIA",
    "PRODUTOS",
    "RESUMO",
    "INDICADORES",
    "RANKING_CLIENTES",
    "RANKING_GRUPOS_CLIENTES",
    "RANKING_UNIDADES",
    "RANKING_OCORRENCIAS",
    "RANKING_PRODUTOS",
    "STATUS_DEBITOS",
    "REGRAS_DEBITO",
    "EVOLUCAO_MENSAL",
    "EVOLUCAO_DIARIA",
}


@dataclass(frozen=True)
class SnapshotInfo:
    path: Path
    nome: str
    tamanho: int
    atualizado_em: float


class IncidentesRepository:
    """
    Repositório responsável por localizar e carregar
    o snapshot atual dos incidentes.

    O workbook é mantido em memória enquanto o arquivo
    não for alterado.
    """

    def __init__(
        self,
        snapshots_dir: str = "data/incidentes/current",
    ):
        self.snapshots_dir = Path(snapshots_dir)

        self.snapshots_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._cache_workbook: Optional[
            Dict[str, pd.DataFrame]
        ] = None

        self._cache_path: Optional[Path] = None
        self._cache_mtime: Optional[float] = None
        self._cache_size: Optional[int] = None

        self._lock = RLock()

    # ----------------------------------------------------
    # Snapshots
    # ----------------------------------------------------

    def listar_snapshots(
        self,
    ) -> List[SnapshotInfo]:
        arquivos = sorted(
            self.snapshots_dir.glob("*.xlsx"),
            key=lambda arquivo: arquivo.stat().st_mtime,
            reverse=True,
        )

        retorno: List[SnapshotInfo] = []

        for arquivo in arquivos:
            stat = arquivo.stat()

            retorno.append(
                SnapshotInfo(
                    path=arquivo,
                    nome=arquivo.name,
                    tamanho=stat.st_size,
                    atualizado_em=stat.st_mtime,
                )
            )

        return retorno

    def snapshot_atual(
        self,
    ) -> Optional[SnapshotInfo]:
        snapshots = self.listar_snapshots()

        if not snapshots:
            return None

        return snapshots[0]

    # ----------------------------------------------------
    # Cache
    # ----------------------------------------------------

    def limpar_cache(
        self,
    ) -> None:
        with self._lock:
            self._cache_workbook = None
            self._cache_path = None
            self._cache_mtime = None
            self._cache_size = None

    def _cache_valido(
        self,
        arquivo: Path,
    ) -> bool:
        if self._cache_workbook is None:
            return False

        if self._cache_path is None:
            return False

        if self._cache_mtime is None:
            return False

        if self._cache_size is None:
            return False

        try:
            stat = arquivo.stat()
        except FileNotFoundError:
            return False

        return (
            self._cache_path.resolve()
            == arquivo.resolve()
            and self._cache_mtime
            == stat.st_mtime
            and self._cache_size
            == stat.st_size
        )

    def cache_info(
        self,
    ) -> Dict[str, object]:
        return {
            "ativo": self._cache_workbook is not None,
            "arquivo": (
                str(self._cache_path)
                if self._cache_path is not None
                else None
            ),
            "atualizado_em": self._cache_mtime,
            "tamanho": self._cache_size,
            "abas": (
                list(self._cache_workbook.keys())
                if self._cache_workbook is not None
                else []
            ),
        }

    # ----------------------------------------------------
    # Carregamento
    # ----------------------------------------------------

    def carregar_planilha(
        self,
        arquivo: Optional[Path] = None,
        forcar_recarregamento: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        if arquivo is None:
            snapshot = self.snapshot_atual()

            if snapshot is None:
                raise FileNotFoundError(
                    "Nenhum snapshot de incidentes "
                    "foi encontrado em "
                    f"{self.snapshots_dir}."
                )

            arquivo = snapshot.path

        arquivo = Path(arquivo)

        if not arquivo.exists():
            raise FileNotFoundError(
                f"Snapshot não encontrado: {arquivo}"
            )

        with self._lock:
            if (
                not forcar_recarregamento
                and self._cache_valido(arquivo)
            ):
                return self._copiar_workbook(
                    self._cache_workbook
                )

            workbook_bruto = pd.read_excel(
                arquivo,
                sheet_name=None,
            )

            workbook: Dict[str, pd.DataFrame] = {}

            for nome_aba, dataframe in (
                workbook_bruto.items()
            ):
                nome_normalizado = (
                    str(nome_aba)
                    .strip()
                    .upper()
                )

                base = dataframe.copy()

                base.columns = [
                    str(coluna).strip().upper()
                    for coluna in base.columns
                ]

                workbook[nome_normalizado] = base

            stat = arquivo.stat()

            self._cache_workbook = workbook
            self._cache_path = arquivo
            self._cache_mtime = stat.st_mtime
            self._cache_size = stat.st_size

            return self._copiar_workbook(workbook)

    @staticmethod
    def _copiar_workbook(
        workbook: Optional[
            Dict[str, pd.DataFrame]
        ],
    ) -> Dict[str, pd.DataFrame]:
        if workbook is None:
            return {}

        return {
            nome_aba: dataframe.copy()
            for nome_aba, dataframe
            in workbook.items()
        }

    def carregar_aba(
        self,
        nome_aba: str,
    ) -> pd.DataFrame:
        workbook = self.carregar_planilha()

        nome_normalizado = (
            str(nome_aba)
            .strip()
            .upper()
        )

        dataframe = workbook.get(nome_normalizado)

        if dataframe is None:
            return pd.DataFrame()

        return dataframe.copy()

    def validar_estrutura(
        self,
    ) -> Dict[str, object]:
        workbook = self.carregar_planilha()

        abas_encontradas = set(workbook.keys())

        abas_ausentes = sorted(
            ABAS_ANALYTICS - abas_encontradas
        )

        abas_extras = sorted(
            abas_encontradas - ABAS_ANALYTICS
        )

        return {
            "valido": len(abas_ausentes) == 0,
            "abas_encontradas": sorted(
                abas_encontradas
            ),
            "abas_ausentes": abas_ausentes,
            "abas_extras": abas_extras,
        }

    # ----------------------------------------------------
    # Abas
    # ----------------------------------------------------

    def auditoria(self) -> pd.DataFrame:
        return self.carregar_aba("AUDITORIA")

    def produtos(self) -> pd.DataFrame:
        return self.carregar_aba("PRODUTOS")

    def resumo(self) -> pd.DataFrame:
        return self.carregar_aba("RESUMO")

    def indicadores(self) -> pd.DataFrame:
        return self.carregar_aba("INDICADORES")

    def ranking_clientes(self) -> pd.DataFrame:
        return self.carregar_aba(
            "RANKING_CLIENTES"
        )

    def ranking_grupos(self) -> pd.DataFrame:
        return self.carregar_aba(
            "RANKING_GRUPOS_CLIENTES"
        )

    def ranking_unidades(self) -> pd.DataFrame:
        return self.carregar_aba(
            "RANKING_UNIDADES"
        )

    def ranking_ocorrencias(self) -> pd.DataFrame:
        return self.carregar_aba(
            "RANKING_OCORRENCIAS"
        )

    def ranking_produtos(self) -> pd.DataFrame:
        return self.carregar_aba(
            "RANKING_PRODUTOS"
        )

    def status_debitos(self) -> pd.DataFrame:
        return self.carregar_aba(
            "STATUS_DEBITOS"
        )

    def regras_debitos(self) -> pd.DataFrame:
        return self.carregar_aba(
            "REGRAS_DEBITO"
        )

    def evolucao_mensal(self) -> pd.DataFrame:
        return self.carregar_aba(
            "EVOLUCAO_MENSAL"
        )

    def evolucao_diaria(self) -> pd.DataFrame:
        return self.carregar_aba(
            "EVOLUCAO_DIARIA"
        )

    def carregar_volume(
        self,
    ) -> pd.DataFrame:
        arquivo = Path(
            "data/incidentes/volume/current/"
            "volume_atual.xlsx"
        )

        if not arquivo.exists():
            return pd.DataFrame()

        base = pd.read_excel(
            arquivo,
        )

        base.columns = [
            str(coluna)
            .strip()
            .upper()
            for coluna
            in base.columns
        ]

        return base