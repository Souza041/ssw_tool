import shutil
import tempfile
from pathlib import Path
from typing import Callable

from modules.whirlpool.config import WhirlpoolPortalSettings, WhirlpoolSettings
from modules.whirlpool.excel import carregar_transportes_portal
from modules.whirlpool.portal import (
    PortalTransportLocks,
    ResultadoTransportePortal,
    TransportePortal,
    WhirlpoolPortal,
)
from modules.whirlpool.repository import WhirlpoolRepository


class HomologacaoPortalInvalida(RuntimeError):
    pass


class WhirlpoolPortalDryRunService:
    def __init__(
        self,
        settings: WhirlpoolSettings | None = None,
        portal_settings: WhirlpoolPortalSettings | None = None,
        repository: WhirlpoolRepository | None = None,
        portal: WhirlpoolPortal | None = None,
    ) -> None:
        self.settings = settings or WhirlpoolSettings.from_env()
        self.portal_settings = portal_settings or WhirlpoolPortalSettings.from_env()
        self.repository = repository or WhirlpoolRepository(self.settings.data_dir)
        self.portal = portal or WhirlpoolPortal(self.portal_settings)

    def executar(
        self,
        run_id: str,
        owner: str,
        usuario: str,
        job_id: str,
        autorizado: bool,
        log: Callable[[str], None] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[ResultadoTransportePortal]:
        if not autorizado:
            raise HomologacaoPortalInvalida(
                "Confirmação explícita de homologação não fornecida."
            )
        self.portal_settings.validar_homologacao(usuario)
        record = self.repository.obter_execucao(run_id)
        if record is None or record.get("owner_hash") != owner:
            raise HomologacaoPortalInvalida("Execução Whirlpool não encontrada.")
        if record.get("status") != "SIMULADO":
            raise HomologacaoPortalInvalida(
                "Somente uma simulação concluída pode consultar o portal."
            )

        input_path = Path(record.get("input_path") or "").resolve()
        private_files = (self.settings.data_dir / "files").resolve()
        if private_files not in input_path.parents or not input_path.is_file():
            raise HomologacaoPortalInvalida("Planilha privada não encontrada.")

        pairs = carregar_transportes_portal(input_path)
        if len(pairs) > self.portal_settings.max_transports:
            raise HomologacaoPortalInvalida(
                "A planilha excede o limite de transportes da homologação."
            )
        items = [TransportePortal(planta, transporte) for planta, transporte in pairs]
        final_dir = (
            self.settings.data_dir / "files" / run_id / "portal" / job_id
        ).resolve()
        expected_parent = (self.settings.data_dir / "files" / run_id / "portal").resolve()
        if expected_parent not in final_dir.parents:
            raise HomologacaoPortalInvalida("Diretório de homologação inválido.")

        temp_root = self.settings.data_dir / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        logger = log or (lambda _message: None)
        progress_callback = progress or (lambda _done, _total: None)
        with PortalTransportLocks(
            self.settings.data_dir / "locks" / "portal",
            (item.chave for item in items),
        ):
            with tempfile.TemporaryDirectory(
                prefix=f"portal-{job_id[:8]}-", dir=temp_root,
            ) as temporary:
                results = self.portal.baixar_documentos(
                    items, Path(temporary), logger, progress_callback,
                )
                final_dir.mkdir(parents=True, exist_ok=False)
                try:
                    final_dir.chmod(0o700)
                except OSError:
                    pass
                for result in results:
                    saved = []
                    for document in result.documentos:
                        source = Path(document).resolve()
                        if Path(temporary).resolve() not in source.parents:
                            raise HomologacaoPortalInvalida(
                                "Documento fora do diretório temporário."
                            )
                        destination = (final_dir / source.name).resolve()
                        if final_dir not in destination.parents:
                            raise HomologacaoPortalInvalida(
                                "Nome de documento inválido."
                            )
                        shutil.move(str(source), destination)
                        try:
                            destination.chmod(0o600)
                        except OSError:
                            pass
                        saved.append(destination)
                    result.documentos = saved

        audit = {
            "job_id": job_id,
            "modo": "PORTAL_DRY_RUN",
            "total_transportes": len(items),
            "documentos_baixados": sum(len(item.documentos) for item in results),
            "resultados": [
                {
                    "planta": item.planta,
                    "transporte": item.transporte,
                    "status": item.status,
                    "documentos": [path.name for path in item.documentos],
                    "erro": item.erro,
                }
                for item in results
            ],
            "smtp_executado": False,
            "email_enviado_alterado": False,
            "status_envio_alterado": False,
        }
        history = list(record.get("portal_homologacoes") or [])
        history.append(audit)
        self.repository.atualizar_execucao(run_id, portal_homologacoes=history)
        logger("Homologação do portal concluída sem envio de e-mails.")
        return results
