from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
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

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


def criar_client_logado() -> SSWClient:
    client = SSWClient()
    client.login()
    client.open_menu()
    return client


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/op455", response_class=HTMLResponse)
def op455_form(request: Request):
    return templates.TemplateResponse("op455.html", {"request": request})


@router.post("/op455", response_class=HTMLResponse)
def op455_run(
    request: Request,
    periodos: str = Form(...),
    timeout: int = Form(300),
):
    client = criar_client_logado()
    op455 = OP455Report(client)

    arquivos = []

    linhas = [
        linha.strip()
        for linha in periodos.splitlines()
        if linha.strip()
    ]

    for linha in linhas:
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

        arquivo = op455.gerar_e_baixar_por_datas(
            output_dir=Path("downloads"),
            data_inicial=data_inicial,
            data_final=data_final,
            timeout_seconds=timeout,
        )

        arquivos.append({
            "path": arquivo,
            "name": arquivo.name,
            "url": f"/downloads/{arquivo.name}",
            "periodo": f"{data_inicial} até {data_final}",
        })

    return templates.TemplateResponse(
        "op455.html",
        {
            "request": request,
            "success": True,
            "arquivos": arquivos,
            "periodos": periodos,
        },
    )


@router.get("/op150", response_class=HTMLResponse)
def op150_form(request: Request):
    return templates.TemplateResponse("op150.html", {"request": request})


@router.post("/op150", response_class=HTMLResponse)
def op150_run(
    request: Request,
    data_inicial: str = Form(...),
    data_final: str = Form(...),
    unidade: str = Form("CWB"),
    nome_unidade: str = Form("RODOBRAS TRANSP RODOVIARIOS"),
):
    client = criar_client_logado()
    op150 = OP150Report(client)

    arquivo = op150.gerar_e_baixar(
        output_dir=Path("downloads"),
        data_inicial=data_inicial,
        data_final=data_final,
        unidade=unidade,
        nome_unidade=nome_unidade,
    )

    return templates.TemplateResponse(
        "op150.html",
        {
            "request": request,
            "success": True,
            "arquivo": arquivo,
            "download_url": f"/downloads/{arquivo.name}",
        },
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

@router.post("/op488", response_class=HTMLResponse)
def op488_run(
    request: Request,
    unidade: str = Form("CWB"),
    cod_evento: str = Form(...),
    evento: str = Form(...),
    mes_comp: str = Form(...),
    sit_desp: str = Form("X"),
    sit_arq: str = Form("T"),
    timeout: int = Form(300),
):
    client = criar_client_logado()

    op488 = OP488Report(client)

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

    return templates.TemplateResponse(
        "op488.html",
        {
            "request": request,
            "success": True,
            "arquivo": arquivo,
            "download_url": f"/downloads/{arquivo.name}",
        },
    )

@router.get("/op001", response_class=HTMLResponse)
def op001_form(request: Request):
    return templates.TemplateResponse("op001.html", {"request": request})


@router.post("/op001", response_class=HTMLResponse)
async def op001_run(
    request: Request,
    arquivo_excel: UploadFile = File(...),
):
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)

    input_file = uploads_dir / arquivo_excel.filename

    ext = Path(arquivo_excel.filename).suffix.lower()

    if ext not in {
        ".xlsx",
        ".xls",
        ".csv",
    }:
        raise ValueError(
            "Arquivo inválido. Use XLSX, XLS ou CSV."
        )

    with input_file.open("wb") as f:
        f.write(await arquivo_excel.read())

    output_file = Path("downloads") / f"processado_{arquivo_excel.filename}"

    client = criar_client_logado()

    op001 = OP001Coleta(client)
    op001.open(unidade="CWB")

    arquivo_saida = processar_planilha_nfd(
        op001=op001,
        input_file=input_file,
        output_file=output_file,
    )

    return templates.TemplateResponse(
        "op001.html",
        {
            "request": request,
            "success": True,
            "arquivo": arquivo_saida,
            "download_url": f"/downloads/{arquivo_saida.name}",
        },
    )

@router.get("/op001-transporte", response_class=HTMLResponse)
def op001_transporte_form(request: Request):
    return templates.TemplateResponse(
        "op001_transporte.html",
        {"request": request},
    )


@router.post("/op001-transporte", response_class=HTMLResponse)
async def op001_transporte_run(
    request: Request,
    arquivo_excel: UploadFile = File(...),
):
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)

    input_file = uploads_dir / arquivo_excel.filename

    with input_file.open("wb") as f:
        f.write(await arquivo_excel.read())

    output_file = Path("downloads") / f"transporte_processado_{arquivo_excel.filename}"

    client = criar_client_logado()

    op001 = OP001Coleta(client)
    op001.open(unidade="CWB")

    arquivo_saida = processar_planilha_transporte(
        op001=op001,
        input_file=input_file,
        output_file=output_file,
    )

    return templates.TemplateResponse(
        "op001_transporte.html",
        {
            "request": request,
            "success": True,
            "arquivo": arquivo_saida,
            "download_url": f"/downloads/{arquivo_saida.name}",
        },
    )