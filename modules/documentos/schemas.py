from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class InvoiceReference:
    number: str
    series: str | None = None
    access_key: str | None = None
    is_primary: bool = False


@dataclass
class OP455Document:
    ctrc: str
    ctrc_raw: str
    cte_number: str | None
    document_type: str | None
    issue_date: date | None
    issuing_unit: str | None
    receiving_unit: str | None
    payer_cnpj: str | None
    payer_name: str | None
    recipient_cnpj: str | None
    recipient_name: str | None
    recipient_state: str | None
    invoices: list[InvoiceReference] = field(default_factory=list)
    order_numbers: list[str] = field(default_factory=list)
    remittance_cover_number: str | None = None
    archive_package_number: str | None = None
    scanned: bool = False
    scanned_at: datetime | None = None
    origin_ctrc: str | None = None
    expedition_map: str | None = None
    reception_map: str | None = None
    volumes: str | None = None
    customer_shipment: str | None = None


@dataclass
class OP930Occurrence:
    ctrc: str
    ctrc_raw: str
    invoice_number: str | None
    invoice_series: str | None
    occurrence_code: str
    occurrence_description: str | None
    occurrence_complement: str | None
    occurrence_at: datetime | None
    included_at: datetime | None
    occurrence_user: str | None
    occurrence_company: str | None
    occurrence_unit: str | None
    delivery_date: date | None
    payer_cnpj: str | None
    payer_name: str | None
    canceled: bool = False


@dataclass
class PortalDocument:
    report_type: str
    carrier_cnpj: str | None
    ctrc: str
    ctrc_raw: str
    invoice_number: str
    invoice_series: str | None
    transport_number: str | None
    issue_date: date | None
    recipient_name: str | None
    recipient_cnpj: str | None
    recipient_state: str | None
    pending_at: datetime | None = None
    pending_user: str | None = None
    pending_reason: str | None = None

    @property
    def warehouse_ctrc(self) -> str:
        """Identificador do portal; não corresponde ao CTRC operacional do SSW."""
        return self.ctrc
