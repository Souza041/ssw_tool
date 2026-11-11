import queue
import uuid
from dataclasses import dataclass, field

from modules.whirlpool.portal_service import WhirlpoolPortalDryRunService
from web.jobs import Job, add_log, executar_job, set_progress


@dataclass
class PortalJobRun:
    owner_hash: str
    run_id: str
    job: Job
    logs: list[str] = field(default_factory=list)


PORTAL_JOBS: dict[str, PortalJobRun] = {}


def criar_portal_job(owner_hash: str, run_id: str) -> PortalJobRun:
    job = Job(id=uuid.uuid4().hex)
    run = PortalJobRun(owner_hash=owner_hash, run_id=run_id, job=job)
    PORTAL_JOBS[job.id] = run
    return run


def executar_portal_job(
    run: PortalJobRun,
    usuario: str,
    service: WhirlpoolPortalDryRunService | None = None,
) -> None:
    portal_service = service or WhirlpoolPortalDryRunService()

    def worker(job: Job):
        add_log(job, "Homologação PORTAL_DRY_RUN iniciada.")
        try:
            results = portal_service.executar(
                run_id=run.run_id,
                owner=run.owner_hash,
                usuario=usuario,
                job_id=job.id,
                autorizado=True,
                log=lambda message: add_log(job, message),
                progress=lambda done, total: set_progress(job, done, total),
            )
        except Exception as exc:
            add_log(job, "Homologação interrompida com falha segura.")
            raise RuntimeError(
                "Homologação Whirlpool interrompida; nenhum e-mail foi enviado."
            ) from exc
        job.result_files = [
            {
                "name": path.name,
                "path": str(path),
                "transporte": result.transporte,
            }
            for result in results
            for path in result.documentos
        ]
        return None

    executar_job(run.job, worker)


def consumir_logs(run: PortalJobRun) -> list[str]:
    while True:
        try:
            run.logs.append(run.job.logs.get_nowait())
        except queue.Empty:
            return list(run.logs)
