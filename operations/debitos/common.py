import html
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote


def somente_digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def normalizar_texto(valor) -> str:
    texto = str(valor or "").strip().upper()
    texto = unicodedata.normalize("NFKD", texto)
    return texto.encode("ASCII", "ignore").decode("ASCII")


def valor_ssw(valor) -> str:
    if valor is None or str(valor).strip() == "":
        raise ValueError("Valor vazio.")

    texto = str(valor).strip()
    if isinstance(valor, (int, float, Decimal)):
        numero = Decimal(str(valor))
    else:
        texto = texto.replace("R$", "").replace(" ", "")
        if "," in texto and "." in texto:
            texto = texto.replace(".", "").replace(",", ".")
        elif "," in texto:
            texto = texto.replace(",", ".")
        try:
            numero = Decimal(texto)
        except InvalidOperation as exc:
            raise ValueError(f"Valor inválido: {valor}") from exc

    return f"{numero:.2f}".replace(".", ",")


def data_ssw(valor) -> str:
    if valor is None or str(valor).strip() == "":
        raise ValueError("Data vazia.")

    # Quando pandas/openpyxl entrega datetime de verdade
    if isinstance(valor, datetime):
        return valor.strftime("%d%m%y")

    if isinstance(valor, date):
        return valor.strftime("%d%m%y")

    texto = str(valor).strip()

    # Tenta formatos completos antes de remover caracteres.
    # Inclui o formato que apareceu no nosso primeiro teste:
    # 2026-07-31 00:00:00
    for formato in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
    ):
        try:
            return datetime.strptime(texto, formato).strftime("%d%m%y")
        except ValueError:
            pass

    somente = somente_digitos(texto)

    if len(somente) == 6:
        datetime.strptime(somente, "%d%m%y")
        return somente

    if len(somente) == 8:
        return datetime.strptime(somente, "%d%m%Y").strftime("%d%m%y")

    raise ValueError(f"Data inválida: {valor}")


def competencia_por_data(data_ddmmyy: str) -> str:
    data = datetime.strptime(data_ddmmyy, "%d%m%y")
    return data.strftime("%m%y")


def competencia_ssw(valor, fallback_data: str) -> str:
    if valor is None or str(valor).strip() == "":
        return competencia_por_data(fallback_data)

    somente = somente_digitos(valor)
    if len(somente) == 4:
        mes = int(somente[:2])
        if not 1 <= mes <= 12:
            raise ValueError(f"Competência inválida: {valor}")
        return somente

    if len(somente) in (6, 8):
        return competencia_por_data(data_ssw(valor))

    raise ValueError(f"Competência inválida: {valor}")


def decodificar_html(texto: str) -> str:
    return unquote(html.unescape(texto or ""))


def texto_limpo(texto: str) -> str:
    decoded = decodificar_html(texto)
    decoded = re.sub(r"<br\s*/?>", " ", decoded, flags=re.I)
    decoded = re.sub(r"<[^>]+>", " ", decoded)
    return re.sub(r"\s+", " ", decoded).strip()


def extrair_inputs(html_texto: str) -> dict[str, str]:
    decoded = decodificar_html(html_texto)
    resultado: dict[str, str] = {}

    for tag in re.findall(r"<input\b[^>]*>", decoded, flags=re.I):
        name_match = re.search(r"\bname\s*=\s*[\"']?([^\"'\s>]+)", tag, flags=re.I)
        if not name_match:
            continue
        value_match = re.search(r"\bvalue\s*=\s*[\"']([^\"']*)[\"']", tag, flags=re.I)
        if not value_match:
            value_match = re.search(r"\bvalue\s*=\s*([^\s>]+)", tag, flags=re.I)
        resultado[name_match.group(1)] = value_match.group(1) if value_match else ""

    return resultado
