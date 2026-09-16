from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from modules.documentos.config import DocumentosSettings, settings
from operations.op455.report import OP455Report
from operations.op930.report import OP930Report
from ssw.client import SSWClient


@dataclass(frozen=True)
class SSWDownloads:
    op455: Path
    op930: Path


def _ddmmaa(value: date) -> str:
    return value.strftime("%d%m%y")


class SSWDocumentsCollector:
    """Baixa as bases incrementais usadas pelo módulo Documentos GCE."""

    def __init__(self, config: DocumentosSettings = settings) -> None:
        self.config = config

    def download(
        self,
        output_dir: Path,
        period_start: date,
        period_end: date,
    ) -> SSWDownloads:
        if period_end < period_start:
            raise ValueError("A data final do SSW não pode ser anterior à inicial.")

        output_dir = Path(output_dir)
        op455_dir = output_dir / "op455"
        op930_dir = output_dir / "op930"
        op455_dir.mkdir(parents=True, exist_ok=True)
        op930_dir.mkdir(parents=True, exist_ok=True)

        self.config.validate_ssw()

        client = SSWClient(
            dominio=self.config.ssw_dominio,
            cpf=self.config.ssw_cpf,
            usuario=self.config.ssw_usuario,
            senha=self.config.ssw_senha,
            unidade=self.config.ssw_unidade,
        )
        try:
            client.login()
            client.open_menu()

            op455 = OP455Report(client).gerar_e_baixar_documentos(
                output_dir=op455_dir,
                data_inicial=_ddmmaa(period_start),
                data_final=_ddmmaa(period_end),
                timeout_seconds=self.config.ssw_report_timeout,
            )

            op930 = OP930Report(client).gerar_e_baixar_por_cnpj(
                output_dir=op930_dir,
                data_inicial=_ddmmaa(period_start),
                data_final=_ddmmaa(period_end),
                cnpj=self.config.op930_cnpj,
                grupo=self.config.op930_group,
                timeout_seconds=self.config.ssw_report_timeout,
            )
            return SSWDownloads(Path(op455), Path(op930))
        finally:
            client.session.close()
