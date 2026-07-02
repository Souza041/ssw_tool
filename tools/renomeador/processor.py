import re
import cv2
import pytesseract
from pathlib import Path

import platform

if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def limpar_nf(valor):
    valor = re.sub(r"\D", "", valor).lstrip("0")
    return valor if valor else None


def preprocess(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.5, fy=2.5)
    gray = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)[1]
    return gray


def ocr(img, numeros=False):
    config = "--psm 6"
    if numeros:
        config += " -c tessedit_char_whitelist=0123456789"

    return pytesseract.image_to_string(preprocess(img), config=config)


def rotacoes(img):
    return [
        ("normal", img),
        ("90_direita", cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)),
        ("90_esquerda", cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)),
        ("180", cv2.rotate(img, cv2.ROTATE_180)),
    ]


def detectar_qrcode(img):
    detector = cv2.QRCodeDetector()
    data, points, _ = detector.detectAndDecode(img)

    if points is None:
        return None

    pts = points[0].astype(int)
    x = min(p[0] for p in pts)
    y = min(p[1] for p in pts)
    x2 = max(p[0] for p in pts)
    y2 = max(p[1] for p in pts)

    return x, y, x2, y2


def extrair_dacte_chave_nfe(img):
    for nome_rot, r in rotacoes(img):
        h, w = r.shape[:2]

        crops = [
            r[int(h * 0.48):int(h * 0.75), int(w * 0.35):int(w * 0.88)],
            r[int(h * 0.50):int(h * 0.70), int(w * 0.35):int(w * 0.85)],
            r[int(h * 0.45):int(h * 0.80), int(w * 0.30):int(w * 0.90)],
        ]

        for crop in crops:
            texto = ocr(crop, numeros=False).upper()

            match = re.search(
                r"NF[\s\-]*E\s*[:\-]?\s*([0-9\s\.\-]{44,90})",
                texto,
                re.DOTALL
            )

            if not match:
                continue

            chave = re.sub(r"\D", "", match.group(1))

            if len(chave) >= 44:
                chave = chave[:44]

                if chave[20:22] != "55":
                    continue

                numero_nf = chave[25:34]
                nf = limpar_nf(numero_nf)

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
            texto = ocr(crop, numeros=False)
            nums = re.sub(r"\D", "", texto)

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
            texto = ocr(crop, numeros=False)
            nums = re.sub(r"\D", "", texto)

            matches = re.findall(r"000\d{6}", nums)

            for m in matches:
                nf = limpar_nf(m)

                if nf and len(nf) == 6:
                    return nf, f"fallback lateral {nome_rot}"

    return None, None


def extrair_nf_da_imagem(caminho: Path):
    img = cv2.imread(str(caminho))

    if img is None:
        return None, "imagem inválida"

    nf, metodo = extrair_dacte_chave_nfe(img)
    if nf:
        return nf, metodo

    nf, metodo = extrair_canhoto_por_qr(img)
    if nf:
        return nf, metodo

    nf, metodo = extrair_canhoto_fallback_lateral(img)
    if nf:
        return nf, metodo

    return None, "não encontrado"