import hashlib
import os
import uuid
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from modules.whirlpool.config import WhirlpoolSettings
from modules.whirlpool.excel import PlanilhaWhirlpoolInvalida, simular_planilha
from modules.whirlpool.repository import WhirlpoolRepository
from modules.whirlpool.schemas import EstadoExecucao


class UploadWhirlpoolInvalido(ValueError):
    def __init__(self, message: str, run_id: str | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id


def owner_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


class WhirlpoolService:
    def __init__(
        self,
        settings: WhirlpoolSettings | None = None,
        repository: WhirlpoolRepository | None = None,
    ) -> None:
        self.settings = settings or WhirlpoolSettings.from_env()
        self.repository = repository or WhirlpoolRepository(self.settings.data_dir)

    def _validate_upload(self, content: bytes, filename: str) -> str:
        safe_name = Path(filename or "whirlpool.xlsx").name[:180]
        if Path(safe_name).suffix.casefold() != ".xlsx":
            raise UploadWhirlpoolInvalido("Envie um arquivo com extensão .xlsx.")
        if not content:
            raise UploadWhirlpoolInvalido("O arquivo enviado está vazio.")
        if len(content) > self.settings.max_upload_bytes:
            raise UploadWhirlpoolInvalido("O arquivo excede o limite permitido.")
        if not content.startswith(b"PK"):
            raise UploadWhirlpoolInvalido("O conteúdo enviado não é um XLSX válido.")
        try:
            with ZipFile(BytesIO(content)) as archive:
                members = archive.infolist()
                total_uncompressed = sum(item.file_size for item in members)
                if (
                    len(members) > 2_000
                    or total_uncompressed > self.settings.max_upload_bytes * 10
                    or "[Content_Types].xml" not in archive.namelist()
                ):
                    raise UploadWhirlpoolInvalido(
                        "O XLSX excede os limites seguros de processamento."
                    )
        except BadZipFile as exc:
            raise UploadWhirlpoolInvalido("O conteúdo enviado não é um XLSX válido.") from exc
        return safe_name

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".whp-{uuid.uuid4().hex[:8]}.tmp"
        with temporary.open("xb") as handle:
            handle.write(content)
        os.replace(temporary, path)

    def simular(self, content: bytes, filename: str, owner: str) -> dict:
        safe_name = self._validate_upload(content, filename)
        run_id = uuid.uuid4().hex
        run_dir = self.settings.data_dir / "files" / run_id
        input_path = run_dir / "entrada.xlsx"
        result_path = run_dir / "resultado.xlsx"
        self._write_private(input_path, content)

        record = self.repository.criar_execucao({
            "id": run_id,
            "owner_hash": owner,
            "arquivo_original": safe_name,
            "status": EstadoExecucao.PROCESSANDO.value,
            "modo": "SIMULACAO",
            "progresso": 0,
            "total": 0,
            "input_path": str(input_path),
            "result_path": None,
            "resumo": None,
            "erro": None,
            "envio_real": False,
        })
        try:
            summary = simular_planilha(input_path, result_path)
            return self.repository.atualizar_execucao(
                run_id,
                status=EstadoExecucao.SIMULADO.value,
                progresso=summary["total_linhas"],
                total=summary["total_linhas"],
                result_path=str(result_path),
                resumo=summary,
                envio_real=False,
            )
        except (PlanilhaWhirlpoolInvalida, ValueError) as exc:
            self.repository.atualizar_execucao(
                run_id,
                status=EstadoExecucao.ERRO.value,
                erro=str(exc),
                envio_real=False,
            )
            raise UploadWhirlpoolInvalido(str(exc), run_id=run_id) from exc
        except Exception as exc:
            message = "Falha interna ao processar a planilha Whirlpool."
            self.repository.atualizar_execucao(
                run_id,
                status=EstadoExecucao.ERRO.value,
                erro=message,
                envio_real=False,
            )
            raise UploadWhirlpoolInvalido(message, run_id=run_id) from exc

    def obter_do_usuario(self, run_id: str, owner: str) -> dict | None:
        record = self.repository.obter_execucao(run_id)
        if record is None or record.get("owner_hash") != owner:
            return None
        return record

    def listar_do_usuario(self, owner: str) -> list[dict]:
        return self.repository.listar_execucoes(owner)

    def resultado_do_usuario(self, run_id: str, owner: str) -> Path | None:
        record = self.obter_do_usuario(run_id, owner)
        if not record or not record.get("result_path"):
            return None
        result = Path(record["result_path"]).resolve()
        allowed = (self.settings.data_dir / "files").resolve()
        if allowed not in result.parents or not result.is_file():
            return None
        return result
