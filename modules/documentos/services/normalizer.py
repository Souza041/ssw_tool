from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
import unicodedata

from modules.documentos.schemas import InvoiceReference


EMPTY_VALUES = {"", "NAN", "NONE", "NULL", "NAT"}


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\xa0", " ").replace("\ufffd", " ")
    return " ".join(text.split()).strip()


def optional_text(value: object) -> str | None:
    text = clean_text(value)
    return None if text.upper() in EMPTY_VALUES else text


def normalized_label(value: object) -> str:
    text = clean_text(value).upper()
    return "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def only_digits(value: object) -> str:
    return re.sub(r"\D", "", clean_text(value))


def normalize_identifier(value: object) -> str:
    digits = only_digits(value)
    return digits.lstrip("0") or ("0" if digits else "")


def normalize_cnpj(value: object) -> str | None:
    digits = only_digits(value)
    return digits.zfill(14)[-14:] if digits else None


def normalize_ctrc(value: object) -> str:
    text = clean_text(value).upper()
    if not text:
        return ""

    # SSW: APU404986-1 -> 404986. Portal: 404986 -> 404986.
    match = re.search(r"[A-Z]*0*(\d+)(?:-\d+)?$", text)
    if match:
        return match.group(1).lstrip("0") or "0"

    return normalize_identifier(text)


def parse_bool(value: object) -> bool:
    return normalized_label(value) in {"S", "SIM", "TRUE", "1", "X"}


def parse_date(value: object) -> date | None:
    if value is None or clean_text(value).upper() in EMPTY_VALUES:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()

    text = clean_text(value)
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(value: object) -> time | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, time):
        return value.replace(microsecond=0)

    text = clean_text(value)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def combine_datetime(date_value: object, time_value: object = None) -> datetime | None:
    parsed_date = parse_date(date_value)
    if parsed_date is None:
        return None
    return datetime.combine(parsed_date, parse_time(time_value) or time.min)


def split_grouped(value: object) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,;]", text) if part.strip()]


def parse_invoice_reference(value: object, is_primary: bool = False) -> InvoiceReference | None:
    text = clean_text(value)
    if not text:
        return None

    if "/" in text:
        series_raw, number_raw = text.split("/", 1)
        series = normalize_identifier(series_raw) or None
    else:
        series = None
        number_raw = text

    number = normalize_identifier(number_raw)
    if not number:
        return None

    return InvoiceReference(number=number, series=series, is_primary=is_primary)


def unique_invoices(invoices: list[InvoiceReference]) -> list[InvoiceReference]:
    result: list[InvoiceReference] = []
    indexes: dict[tuple[str, str | None], int] = {}

    for invoice in invoices:
        key = (invoice.number, invoice.series)
        existing = indexes.get(key)
        if existing is None:
            indexes[key] = len(result)
            result.append(invoice)
        elif invoice.is_primary and not result[existing].is_primary:
            result[existing] = invoice
    return result


def normalize_occurrence_code(value: object) -> str:
    digits = only_digits(value)
    return digits.zfill(2) if digits else ""

