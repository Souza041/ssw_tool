import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from modules.whirlpool.schemas import EstadoEnvio


_PROCESS_LOCK = threading.RLock()
_ESTADOS_BLOQUEADOS = {
    EstadoEnvio.PROCESSANDO.value,
    EstadoEnvio.ENVIADO.value,
    EstadoEnvio.VERIFICAR_ENVIO.value,
}


class WhirlpoolRepository:
    """Auditoria privada em arquivos, sem tocar no banco operacional."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.root / ".repository.lock"
        self._idempotency_path = self.root / "idempotency.json"

    @contextmanager
    def _locked(self):
        with _PROCESS_LOCK:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock_path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def criar_execucao(self, record: dict) -> dict:
        record = dict(record)
        record.setdefault("id", uuid.uuid4().hex)
        record.setdefault("criado_em", self._now())
        record["atualizado_em"] = self._now()
        path = self.runs_dir / f"{record['id']}.json"
        with self._locked():
            if path.exists():
                raise ValueError("Execução Whirlpool já existe.")
            self._write_json(path, record)
        return record

    def atualizar_execucao(self, run_id: str, **changes) -> dict:
        path = self.runs_dir / f"{run_id}.json"
        with self._locked():
            record = self._read_json(path, None)
            if record is None:
                raise KeyError("Execução Whirlpool não encontrada.")
            record.update(changes)
            record["atualizado_em"] = self._now()
            self._write_json(path, record)
        return record

    def obter_execucao(self, run_id: str) -> dict | None:
        if not run_id or not run_id.isalnum():
            return None
        with self._locked():
            return self._read_json(self.runs_dir / f"{run_id}.json", None)

    def listar_execucoes(self, owner_hash: str, limit: int = 30) -> list[dict]:
        records = []
        with self._locked():
            for path in self.runs_dir.glob("*.json"):
                record = self._read_json(path, None)
                if record and record.get("owner_hash") == owner_hash:
                    records.append(record)
        records.sort(key=lambda item: item.get("criado_em", ""), reverse=True)
        return records[: max(1, min(limit, 100))]

    def reservar_envio(self, chave: str, run_id: str) -> bool:
        """Reserva atômica para o futuro envio real; simulações não chamam isto."""
        with self._locked():
            index = self._read_json(self._idempotency_path, {})
            existente = index.get(chave)
            if existente and existente.get("estado") in _ESTADOS_BLOQUEADOS:
                return False
            index[chave] = {
                "run_id": run_id,
                "estado": EstadoEnvio.PROCESSANDO.value,
                "atualizado_em": self._now(),
            }
            self._write_json(self._idempotency_path, index)
            return True

    def atualizar_estado_envio(self, chave: str, estado: EstadoEnvio) -> None:
        with self._locked():
            index = self._read_json(self._idempotency_path, {})
            if chave not in index:
                raise KeyError("Reserva de envio Whirlpool não encontrada.")
            index[chave]["estado"] = estado.value
            index[chave]["atualizado_em"] = self._now()
            self._write_json(self._idempotency_path, index)
