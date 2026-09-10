from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from modules.documentos.schemas import InvoiceReference, OP455Document
from modules.documentos.services.normalizer import (
    clean_text,
    combine_datetime,
    normalize_cnpj,
    normalize_ctrc,
    normalize_identifier,
    optional_text,
    parse_bool,
    parse_date,
    parse_invoice_reference,
    split_grouped,
    unique_invoices,
)


REQUIRED_COLUMNS = {
    "Serie/Numero CTRC",
    "Data de Emissao",
    "Numero da Nota Fiscal",
    "Notas Fiscais",
    "Numero dos Pedidos",
    "Numero do Pacote de Arquivamento",
}


def _header_and_rows(file_path: Path) -> tuple[list[str], Iterator[list[str]]]:
    handle = Path(file_path).open(encoding="latin1", newline="")
    reader = csv.reader(handle, delimiter=";")

    try:
        for row in reader:
            if row and clean_text(row[0]) == "1":
                header = [clean_text(value) for value in row]
                missing = REQUIRED_COLUMNS - set(header)
                if missing:
                    handle.close()
                    raise ValueError(
                        "Layout OP455 inválido. Colunas ausentes: "
                        + ", ".join(sorted(missing))
                    )

                def remaining_rows() -> Iterator[list[str]]:
                    try:
                        yield from reader
                    finally:
                        handle.close()

                return header, remaining_rows()
    except Exception:
        handle.close()
        raise

    handle.close()
    raise ValueError("Cabeçalho da OP455 não encontrado.")


def _value(record: dict[str, str], name: str) -> str:
    return record.get(name, "")


def _invoices(record: dict[str, str]) -> list[InvoiceReference]:
    primary_number = normalize_identifier(_value(record, "Numero da Nota Fiscal"))
    references: list[InvoiceReference] = []

    for item in split_grouped(_value(record, "Notas Fiscais")):
        parsed = parse_invoice_reference(item)
        if parsed is not None:
            parsed = InvoiceReference(
                number=parsed.number,
                series=parsed.series,
                is_primary=(parsed.number == primary_number),
            )
            references.append(parsed)

    if primary_number and not any(item.number == primary_number for item in references):
        references.insert(
            0,
            InvoiceReference(number=primary_number, is_primary=True),
        )

    keys = split_grouped(_value(record, "Chaves NF-es"))
    if keys and references:
        enriched: list[InvoiceReference] = []
        for index, invoice in enumerate(references):
            enriched.append(
                InvoiceReference(
                    number=invoice.number,
                    series=invoice.series,
                    access_key=clean_text(keys[index]) if index < len(keys) else None,
                    is_primary=invoice.is_primary,
                )
            )
        references = enriched

    return unique_invoices(references)


def iter_op455(file_path: Path) -> Iterator[OP455Document]:
    header, rows = _header_and_rows(Path(file_path))

    for row in rows:
        if not row or clean_text(row[0]) != "2":
            continue

        padded = row + [""] * max(0, len(header) - len(row))
        record = dict(zip(header, padded))
        ctrc_raw = clean_text(_value(record, "Serie/Numero CTRC"))
        ctrc = normalize_ctrc(ctrc_raw)
        if not ctrc:
            continue

        yield OP455Document(
            ctrc=ctrc,
            ctrc_raw=ctrc_raw,
            cte_number=optional_text(_value(record, "Serie/Numero CT-e")),
            document_type=optional_text(_value(record, "Tipo do Documento")),
            issue_date=parse_date(_value(record, "Data de Emissao")),
            issuing_unit=optional_text(_value(record, "Unidade Emissora")),
            receiving_unit=optional_text(_value(record, "Unidade Receptora")),
            payer_cnpj=normalize_cnpj(_value(record, "CNPJ Pagador")),
            payer_name=optional_text(_value(record, "Cliente Pagador")),
            recipient_cnpj=normalize_cnpj(_value(record, "CNPJ Destinatario")),
            recipient_name=optional_text(_value(record, "Cliente Destinatario")),
            recipient_state=optional_text(_value(record, "UF do Destinatario")),
            invoices=_invoices(record),
            order_numbers=[
                normalize_identifier(value)
                for value in split_grouped(_value(record, "Numero dos Pedidos"))
                if normalize_identifier(value)
            ],
            remittance_cover_number=optional_text(_value(record, "Numero da Capa de Remessa")),
            archive_package_number=optional_text(_value(record, "Numero do Pacote de Arquivamento")),
            scanned=parse_bool(_value(record, "Compr. de Entrega Escaneado")),
            scanned_at=combine_datetime(
                _value(record, "Data do Escaneamento"),
                _value(record, "Hora do Escaneamento"),
            ),
            origin_ctrc=optional_text(_value(record, "CTRC Origem")),
            expedition_map=optional_text(_value(record, "Rel de Comissao de Expedicao")),
            reception_map=optional_text(_value(record, "Rel de Comissao de Recepcao")),
            volumes=optional_text(_value(record, "Volumes")),
            customer_shipment=optional_text(_value(record, "Volume Cliente/Shipment")),
        )

