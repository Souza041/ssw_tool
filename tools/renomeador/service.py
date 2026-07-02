import shutil
import zipfile
from pathlib import Path

from tools.renomeador.processor import extrair_nf_da_imagem


EXTENSOES = {".png", ".jpg", ".jpeg"}


def nome_unico(pasta: Path, nome_base: str, ext: str) -> Path:
    destino = pasta / f"{nome_base}{ext}"
    contador = 2

    while destino.exists():
        destino = pasta / f"{nome_base}_{contador}{ext}"
        contador += 1

    return destino


def processar_renomeador(input_dir: Path, output_dir: Path, zip_path: Path, job=None) -> Path:
    renomeados_dir = output_dir / "renomeados"
    falhou_dir = output_dir / "falhou"

    renomeados_dir.mkdir(parents=True, exist_ok=True)
    falhou_dir.mkdir(parents=True, exist_ok=True)

    arquivos = [
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSOES
    ]

    total = len(arquivos)

    if job:
        job.progress = 0
        job.total = total

    renomeados = 0
    falhas = 0

    for idx, arquivo in enumerate(arquivos, start=1):
        if job:
            job.logs.put(f"Processando {idx}/{total}: {arquivo.name}")

        nf, metodo = extrair_nf_da_imagem(arquivo)

        if nf:
            destino = nome_unico(renomeados_dir, nf, arquivo.suffix.lower())
            shutil.copy2(arquivo, destino)
            renomeados += 1

            if job:
                job.logs.put(f"OK: {arquivo.name} -> {destino.name} ({metodo})")
        else:
            destino = nome_unico(falhou_dir, arquivo.stem, arquivo.suffix.lower())
            shutil.copy2(arquivo, destino)
            falhas += 1

            if job:
                job.logs.put(f"Falhou: {arquivo.name} ({metodo})")

        if job:
            job.progress = idx

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for arquivo in output_dir.rglob("*"):
            if arquivo.is_file() and arquivo != zip_path:
                zipf.write(arquivo, arquivo.relative_to(output_dir))

    if job:
        job.logs.put(f"Renomeados: {renomeados}")
        job.logs.put(f"Falhas: {falhas}")

    return zip_path