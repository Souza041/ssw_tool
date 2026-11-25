import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
import logging
from web import automation_metrics
logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    module: str = "SSW Tool"
    status: str = "running"
    logs: queue.Queue = field(default_factory=queue.Queue)
    result_file: Path | None = None
    result_files: list[dict] = field(default_factory=list)
    progress: int = 0
    total: int = 0
    error: str | None = None


JOBS: dict[str, Job] = {}


def criar_job(module: str = "SSW Tool") -> Job:
    job = Job(id=str(uuid.uuid4()), module=module)
    JOBS[job.id] = job
    try:
        automation_metrics.started(job.id, module)
    except Exception:
        logger.exception("Falha ao registrar inicio de job em metricas")
    return job


def add_log(job: Job, mensagem: str) -> None:
    agora = datetime.now().strftime("%H:%M:%S")
    job.logs.put(f"[{agora}] {mensagem}")

def set_progress(job: Job, progress: int, total: int) -> None:
    job.progress = progress
    job.total = total


def executar_job(job: Job, func: Callable, *args, **kwargs) -> None:
    leases = []
    resources = set()
    try:
        for value in (*args, *kwargs.values()):
            acquire = getattr(value, "acquire_operation", None)
            session = getattr(value, "session", None)
            resource = id(session) if session is not None else id(value)
            if not callable(acquire) or resource in resources:
                continue
            lease = acquire(job.module)
            resources.add(resource)
            if lease is not None:
                leases.append(lease)
    except Exception as exc:
        for lease in reversed(leases):
            lease.release()
        job.status = "error"
        job.error = str(exc)
        add_log(job, f"Execução não iniciada: {exc}")
        try:
            automation_metrics.finished(job.id, job.status)
        except Exception:
            logger.exception("Falha ao registrar termino de job em metricas")
        return

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
        finally:
            for lease in reversed(leases):
                lease.release()
            try:
                automation_metrics.finished(job.id, job.status)
            except Exception:
                logger.exception("Falha ao registrar termino de job em metricas")

    thread = threading.Thread(target=runner, daemon=True)
    try:
        thread.start()
    except BaseException:
        for lease in reversed(leases):
            lease.release()
        raise
