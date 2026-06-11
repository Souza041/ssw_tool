import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable


@dataclass
class Job:
    id: str
    status: str = "running"
    logs: queue.Queue = field(default_factory=queue.Queue)
    result_file: Path | None = None
    result_files: list[dict] = field(default_factory=list)
    error: str | None = None


JOBS: dict[str, Job] = {}


def criar_job() -> Job:
    job = Job(id=str(uuid.uuid4()))
    JOBS[job.id] = job
    return job


def add_log(job: Job, mensagem: str) -> None:
    agora = datetime.now().strftime("%H:%M:%S")
    job.logs.put(f"[{agora}] {mensagem}")


def executar_job(job: Job, func: Callable, *args, **kwargs) -> None:
    def runner():
        try:
            add_log(job, "Thread iniciada.")
            add_log(job, "Processamento iniciado.")

            result = func(job, *args, **kwargs)

            job.result_file = result
            job.status = "done"

            add_log(job, "Processamento finalizado com sucesso.")

        except Exception as exc:
            job.status = "error"
            job.error = str(exc)

            add_log(job, f"Erro: {exc}")

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()