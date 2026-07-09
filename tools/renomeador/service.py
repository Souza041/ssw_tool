import shutil
import zipfile
import pandas as pd
from pathlib import Path
from datetime import datetime
from openpyxl import load_workbook

from tools.renomeador.processor import extrair_nf_da_imagem


EXTENSOES = {".png", ".jpg", ".jpeg"}
TEMPLATE_CARRIER = Path("tools/renomeador/templates/Carrier.xltm")

EMAIL_FIXO = "suporte.ti@rodobrastransp.com.br"
LIMITE_POR_ARQUIVO = 20


def nome_unico(pasta: Path, nome_base: str, ext: str) -> Path:
    destino = pasta / f"{nome_base}{ext}"
    contador = 2

    while destino.exists():
        destino = pasta / f"{nome_base}_{contador}{ext}"
        contador += 1

    return destino


def ler_base_csv(base_csv: Path) -> dict:
    df = pd.read_csv(base_csv, sep="\t", encoding="utf-16", dtype=str)

    df.columns = [c.strip() for c in df.columns]

    mapa = {}

    for _, row in df.iterrows():
        nf = str(row.get("NF No", "")).strip()
        serie = str(row.get("NS No", "")).strip()
        load_id = str(row.get("Load ID", "")).strip()

        if not nf or not load_id:
            continue

        nf = nf.lstrip("0")

        mapa[nf] = {
            "nf": nf,
            "serie": serie,
            "load_id": load_id,
        }

    return mapa


def transportadora_por_serie(serie: str) -> str:
    serie_normalizada = str(serie).strip().lstrip("0")

    if serie_normalizada == "3":
        return "ROBR_SP"

    if serie_normalizada == "33":
        return "ROBR_SC"

    return "ROBR_SC"


def gerar_protocolos_carrier(
    registros: list[dict],
    output_dir: Path,
    email: str,
    observacao: str,
    job=None,
) -> list[Path]:
    arquivos = []

    grupos = {}

    for item in registros:
        transportadora = transportadora_por_serie(item["serie"])
        grupos.setdefault(transportadora, []).append(item)

    for transportadora, itens in grupos.items():
        for idx in range(0, len(itens), LIMITE_POR_ARQUIVO):
            lote = itens[idx:idx + LIMITE_POR_ARQUIVO]
            numero_lote = (idx // LIMITE_POR_ARQUIVO) + 1

            wb = load_workbook(TEMPLATE_CARRIER, keep_vba=True)
            ws = wb.active

            protocolo = datetime.now().strftime("%Y%m%d%H%M%S") + f"{numero_lote:02d}"

            ws["C1"] = transportadora
            ws["C2"] = datetime.now()
            ws["C3"] = protocolo
            ws["C4"] = email

            for linha in range(9, 80):
                for coluna in range(2, 7):
                    ws.cell(linha, coluna).value = None

            linha_excel = 9

            for item in lote:
                ws.cell(linha_excel, 2).value = item["load_id"]
                ws.cell(linha_excel, 3).value = int(item["nf"])
                ws.cell(linha_excel, 4).value = int(item["serie"]) if str(item["serie"]).isdigit() else item["serie"]
                ws.cell(linha_excel, 5).value = None
                ws.cell(linha_excel, 6).value = observacao

                ws.cell(linha_excel, 5).number_format = "DD/MM/YYYY"

                linha_excel += 1

            nome = f"Carrier_{transportadora}_{numero_lote:03d}.xltm"
            caminho = output_dir / nome
            wb.save(caminho)
            arquivos.append(caminho)

            if job:
                job.logs.put(f"Carrier gerado: {nome} ({len(lote)} notas)")

    return arquivos


def adicionar_zip(zipf, arquivo: Path, base: Path):
    if arquivo.is_file():
        zipf.write(arquivo, arquivo.relative_to(base))


def processar_carrier_lg(
    input_dir: Path,
    base_csv: Path,
    output_dir: Path,
    zip_path: Path,
    email: str = EMAIL_FIXO,
    observacao: str = "",
    job=None,
) -> Path:
    renomeados_dir = output_dir / "renomeados"
    falhou_dir = output_dir / "falhou"
    sem_base_dir = output_dir / "sem_base"

    renomeados_dir.mkdir(parents=True, exist_ok=True)
    falhou_dir.mkdir(parents=True, exist_ok=True)
    sem_base_dir.mkdir(parents=True, exist_ok=True)

    mapa_base = ler_base_csv(base_csv)

    arquivos = [
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSOES
    ]

    if job:
        job.progress = 0
        job.total = len(arquivos)

    registros_carrier = []

    for idx, arquivo in enumerate(arquivos, start=1):
        if job:
            job.logs.put(f"Processando {idx}/{len(arquivos)}: {arquivo.name}")

        nf, data_assinatura, metodo = extrair_nf_da_imagem(arquivo)

        if not nf:
            destino = nome_unico(falhou_dir, arquivo.stem, arquivo.suffix.lower())
            shutil.copy2(arquivo, destino)

            if job:
                job.logs.put(f"Falhou OCR: {arquivo.name}")

            job.progress = idx
            continue

        destino = nome_unico(renomeados_dir, nf, arquivo.suffix.lower())
        shutil.copy2(arquivo, destino)

        dados_base = mapa_base.get(nf)

        if not dados_base:
            destino_sem_base = nome_unico(sem_base_dir, nf, arquivo.suffix.lower())
            shutil.copy2(arquivo, destino_sem_base)

            if job:
                job.logs.put(f"Sem base: NF {nf} não localizada no CSV")

            job.progress = idx
            continue

        registros_carrier.append({
            "nf": nf,
            "serie": dados_base["serie"],
            "load_id": dados_base["load_id"],
            "data_assinatura": None,
        })

        if job:
            job.logs.put(f"OK: {arquivo.name} -> {destino.name} | Load {dados_base['load_id']} | Série {dados_base['serie']} | {metodo}")
            job.progress = idx

    carrier_dir = output_dir / "carrier"
    carrier_dir.mkdir(exist_ok=True)

    gerar_protocolos_carrier(
        registros=registros_carrier,
        output_dir=carrier_dir,
        email=email,
        observacao=observacao,
        job=job,
    )

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for arquivo in output_dir.rglob("*"):
            if arquivo.is_file():
                adicionar_zip(zipf, arquivo, output_dir)

    if job:
        job.logs.put(f"Notas para Carrier: {len(registros_carrier)}")
        job.logs.put("ZIP final gerado.")

    return zip_path