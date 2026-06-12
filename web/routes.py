import json
import time

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi import UploadFile, File

from operations.op103.report import OP103Report
from operations.op150.report import OP150Report
from operations.op455.report import OP455Report
from operations.op488.report import OP488Report
from operations.op001.coleta import OP001Coleta
from operations.op001.batch import processar_planilha_nfd
from ssw.client import SSWClient

from operations.op001.batch_transporte import processar_planilha_transporte

from web.jobs import JOBS, add_log, criar_job, executar_job, set_progress

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


def criar_client_logado() -> SSWClient:
    client = SSWClient()
    client.login()
    client.open_menu()
    return client

def executar_op455_job(job, periodos: str, timeout: int) -> list[dict]:
    add_log(job, "Executando login no SSW...")
    client = criar_client_logado()
    add_log(job, "Login concluído.")

    op455 = OP455Report(client)

    arquivos = []

    linhas = [
        linha.strip()
        for linha in periodos.splitlines()
        if linha.strip()
    ]

    add_log(job, f"{len(linhas)} período(s) informado(s).")

    for idx, linha in enumerate(linhas, start=1):
        partes = [
            parte.strip()
            for parte in linha.replace(",", ";").split(";")
            if parte.strip()
        ]

        if len(partes) != 2:
            raise ValueError(
                f"Período inválido: {linha}. Use o formato: 010526;010626"
            )

        data_inicial, data_final = partes

        add_log(
            job,
            f"Gerando relatório {idx}/{len(linhas)}: {data_inicial} até {data_final}",
        )

        arquivo = op455.gerar_e_baixar_por_datas(
            output_dir=Path("downloads"),
            data_inicial=data_inicial,
            data_final=data_final,
            timeout_seconds=timeout,
        )

        add_log(job, f"Arquivo gerado: {arquivo.name}")

        arquivos.append({
            "name": arquivo.name,
            "url": f"/downloads/{arquivo.name}",
            "periodo": f"{data_inicial} até {data_final}",
        })

    job.result_file = None
    job.result_files = arquivos

    return arquivos

def executar_op488_job(
    job,
    unidade: str,
    cod_evento: str,
    evento: str,
    mes_comp: str,
    sit_desp: str,
    sit_arq: str,
    timeout: int,
) -> list[dict]:
    set_progress(job, 0, 1)

    add_log(job, "Executando login no SSW...")
    client = criar_client_logado()
    add_log(job, "Login concluído.")

    add_log(job, "Abrindo OP488...")
    op488 = OP488Report(client)

    add_log(
        job,
        f"Gerando relatório OP488 | evento={cod_evento} | competência={mes_comp}",
    )

    arquivo = op488.gerar_e_baixar(
        output_dir=Path("downloads"),
        unidade=unidade,
        cod_evento=cod_evento,
        evento=evento,
        mes_comp=mes_comp,
        sit_desp=sit_desp,
        sit_arq=sit_arq,
        timeout_seconds=timeout,
    )

    set_progress(job, 1, 1)

    add_log(job, f"Arquivo gerado: {arquivo.name}")

    arquivos = [
        {
            "name": arquivo.name,
            "url": f"/downloads/{arquivo.name}",
            "periodo": f"OP488 - {cod_evento} / {mes_comp}",
        }
    ]

    job.result_files = arquivos

    return arquivos

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/op455", response_class=HTMLResponse)
def op455_form(request: Request):
    return templates.TemplateResponse("op455.html", {"request": request})

@router.post("/op455")
def op455_run(
    periodos: str = Form(...),
    timeout: int = Form(300),
):
    job = criar_job()

    add_log(job, "Job criado.")

    executar_job(
        job,
        executar_op455_job,
        periodos,
        timeout,
    )

    return RedirectResponse(
        url=f"/jobs/{job.id}",
        status_code=303,
    )

@router.get("/op150", response_class=HTMLResponse)
def op150_form(request: Request):
    return templates.TemplateResponse("op150.html", {"request": request})


@router.post("/op150")
def op150_run(
    data_inicial: str = Form(...),
    data_final: str = Form(...),
    unidade: str = Form("CWB"),
    nome_unidade: str = Form("RODOBRAS TRANSP RODOVIARIOS"),
):
    job = criar_job()
    add_log(job, "Job criado.")

    executar_job(
        job,
        executar_op150_job,
        data_inicial,
        data_final,
        unidade,
        nome_unidade,
    )

    return RedirectResponse(
        url=f"/jobs/{job.id}",
        status_code=303,
    )


@router.get("/op103", response_class=HTMLResponse)
def op103_form(request: Request):
    return templates.TemplateResponse("op103.html", {"request": request})


@router.post("/op103", response_class=HTMLResponse)
def op103_run(
    request: Request,
    data_inicial: str = Form(...),
    data_final: str = Form(...),
    unidade_base: str = Form("CWB"),
    unidade_coleta: str = Form("CWB"),
    unidade_destinataria: str = Form("CWB"),
    tipo_consulta: str = Form("coleta"),
):
    client = criar_client_logado()
    op103 = OP103Report(client)

    arquivo = op103.gerar_e_baixar_devolucao(
        output_dir=Path("downloads"),
        data_inicial=data_inicial,
        data_final=data_final,
        unidade_base=unidade_base,
        unidade_coleta=unidade_coleta,
        unidade_destinataria=unidade_destinataria,
        tipo_consulta=tipo_consulta,
    )

    return templates.TemplateResponse(
        "op103.html",
        {
            "request": request,
            "success": True,
            "arquivo": arquivo,
            "download_url": f"/downloads/{arquivo.name}",
        },
    )


@router.get("/op488", response_class=HTMLResponse)
def op488_form(request: Request):
    return templates.TemplateResponse("op488.html", {"request": request})

@router.post("/op488")
def op488_run(
    unidade: str = Form("CWB"),
    cod_evento: str = Form(...),
    evento: str = Form(...),
    mes_comp: str = Form(...),
    sit_desp: str = Form("X"),
    sit_arq: str = Form("T"),
    timeout: int = Form(300),
):
    job = criar_job()
    add_log(job, "Job criado.")

    executar_job(
        job,
        executar_op488_job,
        unidade,
        cod_evento,
        evento,
        mes_comp,
        sit_desp,
        sit_arq,
        timeout,
    )

    return RedirectResponse(
        url=f"/jobs/{job.id}",
        status_code=303,
    )

@router.get("/op001", response_class=HTMLResponse)
def op001_form(request: Request):
    return templates.TemplateResponse("op001.html", {"request": request})


@router.post("/op001")
async def op001_run(
    arquivo_excel: UploadFile = File(...),
):
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)

    input_file = uploads_dir / arquivo_excel.filename

    with input_file.open("wb") as f:
        f.write(await arquivo_excel.read())

    output_file = Path("downloads") / f"processado_{arquivo_excel.filename}"

    job = criar_job()
    add_log(job, "Job criado.")

    executar_job(
        job,
        executar_op001_nfd_job,
        input_file,
        output_file,
    )

    return RedirectResponse(
        url=f"/jobs/{job.id}",
        status_code=303,
    )

@router.get("/op001-transporte", response_class=HTMLResponse)
def op001_transporte_form(request: Request):
    return templates.TemplateResponse(
        "op001_transporte.html",
        {"request": request},
    )


@router.post("/op001-transporte")
async def op001_transporte_run(
    arquivo_excel: UploadFile = File(...),
):
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)

    input_file = uploads_dir / arquivo_excel.filename

    with input_file.open("wb") as f:
        f.write(await arquivo_excel.read())

    output_file = Path("downloads") / f"transporte_processado_{arquivo_excel.filename}"

    job = criar_job()
    add_log(job, "Job criado.")

    executar_job(
        job,
        executar_op001_transporte_job,
        input_file,
        output_file,
    )

    return RedirectResponse(
        url=f"/jobs/{job.id}",
        status_code=303,
    )

@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    return templates.TemplateResponse(
        "job_status.html",
        {
            "request": request,
            "job_id": job_id,
        },
    )

@router.get("/jobs/{job_id}/stream")
def job_stream(job_id: str):
    def event_generator():
        job = JOBS.get(job_id)

        if not job:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Job não encontrado.'})}\n\n"
            return
        
        yield f"data: {json.dumps({'type':'log','message':'Conectado ao monitor.'})}\n\n"

        while True:
            while not job.logs.empty():
                mensagem = job.logs.get()
                yield f"data: {json.dumps({'type': 'log', 'message': mensagem})}\n\n"

                yield f"data: {json.dumps({'type': 'progress', 'progress': job.progress, 'total': job.total})}\n\n"

            if job.status == "done":
                payload = {
                    "type": "done",
                    "files": job.result_files,
                }

                yield f"data: {json.dumps(payload)}\n\n"
                return

            if job.status == "error":
                yield f"data: {json.dumps({'type': 'error', 'message': job.error or 'Erro desconhecido.'})}\n\n"
                return

            time.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def executar_op150_job(
    job,
    data_inicial: str,
    data_final: str,
    unidade: str,
    nome_unidade: str,
) -> list[dict]:
    set_progress(job, 0, 1)

    add_log(job, "Executando login no SSW...")
    client = criar_client_logado()
    add_log(job, "Login concluído.")

    add_log(job, "Abrindo OP150...")
    op150 = OP150Report(client)

    add_log(job, f"Gerando relatório OP150 | {data_inicial} até {data_final}")

    arquivo = op150.gerar_e_baixar(
        output_dir=Path("downloads"),
        data_inicial=data_inicial,
        data_final=data_final,
        unidade=unidade,
        nome_unidade=nome_unidade,
    )

    set_progress(job, 1, 1)

    add_log(job, f"Arquivo gerado: {arquivo.name}")

    arquivos = [{
        "name": arquivo.name,
        "url": f"/downloads/{arquivo.name}",
        "periodo": f"{data_inicial} até {data_final}",
    }]

    job.result_files = arquivos
    return arquivos

def executar_op001_nfd_job(
    job,
    input_file: Path,
    output_file: Path,
) -> list[dict]:
    add_log(job, "Executando login no SSW...")
    client = criar_client_logado()
    add_log(job, "Login concluído.")

    add_log(job, "Abrindo OP001...")
    op001 = OP001Coleta(client)
    op001.open(unidade="CWB")

    add_log(job, "Iniciando processamento da planilha NFD.")

    arquivo_saida = processar_planilha_nfd(
        op001=op001,
        input_file=input_file,
        output_file=output_file,
        job=job,
    )

    add_log(job, f"Planilha final gerada: {arquivo_saida.name}")

    arquivos = [{
        "name": arquivo_saida.name,
        "url": f"/downloads/{arquivo_saida.name}",
        "periodo": "OP001 - Coletas NFD",
    }]

    job.result_files = arquivos
    return arquivos

def executar_op001_transporte_job(
    job,
    input_file: Path,
    output_file: Path,
) -> list[dict]:
    add_log(job, "Executando login no SSW...")
    client = criar_client_logado()
    add_log(job, "Login concluído.")

    add_log(job, "Abrindo OP001...")
    op001 = OP001Coleta(client)
    op001.open(unidade="CWB")

    add_log(job, "Iniciando processamento da planilha Transporte/Ordem Inversa.")

    arquivo_saida = processar_planilha_transporte(
        op001=op001,
        input_file=input_file,
        output_file=output_file,
        job=job,
    )

    add_log(job, f"Planilha final gerada: {arquivo_saida.name}")

    arquivos = [{
        "name": arquivo_saida.name,
        "url": f"/downloads/{arquivo_saida.name}",
        "periodo": "OP001 - Coletas Transporte",
    }]

    job.result_files = arquivos
    return arquivos