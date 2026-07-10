import re
import cv2
import numpy as np
import platform
import pytesseract
from pathlib import Path
from datetime import datetime

if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def limpar_nf(valor):
    valor = re.sub(r"\D", "", str(valor)).lstrip("0")
    return valor if valor else None


def preprocess(img):
    if img is None or img.size == 0:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=1.5, fy=1.5)
    gray = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)[1]
    return gray


def ocr(img, numeros=False):
    if img is None or img.size == 0:
        return ""

    imagem = preprocess(img)
    if imagem is None:
        return ""

    config = "--psm 6"
    if numeros:
        config += " -c tessedit_char_whitelist=0123456789/"

    try:
        return pytesseract.image_to_string(imagem, config=config)
    except Exception:
        return ""

def classificar_documento(img):
    if img is None or img.size == 0:
        return "invalido"

    h, w = img.shape[:2]

    maior = max(w, h)
    menor = min(w, h)

    proporcao = maior / menor if menor else 0

    # Canhoto normalmente é uma faixa longa e estreita
    if proporcao >= 2.2:
        return "canhoto"

    # DACTE completo tende a ser mais próximo de folha
    return "dacte"

def carregar_imagem(caminho: Path):
    try:
        dados = np.fromfile(str(caminho), dtype=np.uint8)
        return cv2.imdecode(dados, cv2.IMREAD_COLOR)
    except Exception:
        return None


def rotacoes(img):
    if img is None or img.size == 0:
        return []

    return [
        ("normal", img),
        ("90_direita", cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)),
        ("90_esquerda", cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)),
        ("180", cv2.rotate(img, cv2.ROTATE_180)),
    ]


def detectar_qrcode(img):
    if img is None or img.size == 0:
        return None

    detector = cv2.QRCodeDetector()
    data, points, _ = detector.detectAndDecode(img)

    if points is None:
        return None

    pts = points[0].astype(int)
    return (
        min(p[0] for p in pts),
        min(p[1] for p in pts),
        max(p[0] for p in pts),
        max(p[1] for p in pts),
    )


def extrair_dacte_chave_nfe(img):
    for nome_rot, r in rotacoes(img):
        h, w = r.shape[:2]

        crops = [
            r[int(h * 0.45):int(h * 0.72), int(w * 0.25):int(w * 0.90)],
            r[int(h * 0.50):int(h * 0.78), int(w * 0.30):int(w * 0.92)],
        ]

        for crop in crops:
            if crop is None or crop.size == 0:
                continue

            texto = ocr(crop).upper()

            match = re.search(
                r"NF[\s\-]*E\s*[:\-]?\s*([0-9\s\.\-]{44,90})",
                texto,
                re.DOTALL,
            )

            if not match:
                continue

            chave = re.sub(r"\D", "", match.group(1))

            if len(chave) >= 44:
                chave = chave[:44]

                if chave[20:22] != "55":
                    continue

                nf = limpar_nf(chave[25:34])

                if nf and 5 <= len(nf) <= 7:
                    return nf, f"DACTE chave NF-e {nome_rot}"

    return None, None


def extrair_canhoto_por_qr(img):
    for nome_rot, r in rotacoes(img):
        qr = detectar_qrcode(r)

        if not qr:
            continue

        x, y, x2, y2 = qr
        qr_w = x2 - x
        qr_h = y2 - y

        candidatos = [
            (max(0, x2), max(0, y - qr_h), min(r.shape[1], x2 + qr_w * 4), min(r.shape[0], y2 + qr_h)),
            (max(0, x - qr_w * 4), max(0, y - qr_h), min(r.shape[1], x), min(r.shape[0], y2 + qr_h)),
            (max(0, x - qr_w), max(0, y - qr_h * 3), min(r.shape[1], x2 + qr_w), min(r.shape[0], y)),
            (max(0, x - qr_w), max(0, y2), min(r.shape[1], x2 + qr_w), min(r.shape[0], y2 + qr_h * 3)),
        ]

        for cx1, cy1, cx2, cy2 in candidatos:
            crop = r[cy1:cy2, cx1:cx2]

            if crop is None or crop.size == 0:
                continue

            nums = re.sub(r"\D", "", ocr(crop))
            matches = re.findall(r"000\d{6}", nums)

            for m in matches:
                nf = limpar_nf(m)
                if nf and len(nf) == 6:
                    return nf, f"canhoto QR {nome_rot}"

    return None, None


def extrair_canhoto_fallback_lateral(img):
    for nome_rot, r in rotacoes(img):
        h, w = r.shape[:2]

        crops = [
            r[:, int(w * 0.70):w],
            r[:, 0:int(w * 0.30)],
            r[0:int(h * 0.30), :],
            r[int(h * 0.70):h, :],
        ]

        for crop in crops:
            if crop is None or crop.size == 0:
                continue

            nums = re.sub(r"\D", "", ocr(crop))
            matches = re.findall(r"000\d{6}", nums)

            for m in matches:
                nf = limpar_nf(m)
                if nf and len(nf) == 6:
                    return nf, f"fallback lateral {nome_rot}"

    return None, None


def normalizar_data(valor):
    valor = valor.strip().replace("-", "/").replace(".", "/")

    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", valor)
    if not match:
        return None

    dia, mes, ano = match.groups()

    dia = int(dia)
    mes = int(mes)
    ano = int(ano)

    if ano < 100:
        ano += 2000

    try:
        return datetime(ano, mes, dia)
    except ValueError:
        return None

def extrair_nf_da_imagem(caminho: Path):
    img = carregar_imagem(caminho)

    if img is None:
        return None, None, "imagem inválida"

    tipo = classificar_documento(img)

    if tipo == "canhoto":
        # Canhoto: QR primeiro
        nf, metodo = extrair_canhoto_por_qr(img)
        if nf:
            return nf, None, metodo

        nf, metodo = extrair_canhoto_fallback_lateral(img)
        if nf:
            return nf, None, metodo

        return None, None, "canhoto não identificado"

    # DACTE inteiro: vai direto para a região NF-E
    nf, metodo = extrair_dacte_chave_nfe(img)
    if nf:
        return nf, None, metodo

    return None, None, "DACTE não identificado"