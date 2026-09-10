from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.parsers.op455 import iter_op455
from modules.documentos.parsers.op930 import iter_op930
from modules.documentos.parsers.portal import iter_portal
from modules.documentos.services.matcher import DocumentMatcher
from modules.documentos.services.normalizer import normalize_identifier


def percentual(value: int, total: int) -> str:
    return f"{(value / total * 100):.2f}%" if total else "0.00%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnóstico do cruzamento documental de junho.")
    parser.add_argument("--portal-solucionar", type=Path, required=True)
    parser.add_argument("--portal-pendencias", type=Path, required=True)
    parser.add_argument("--op455", type=Path, required=True)
    parser.add_argument("--op930", type=Path, required=True)
    args = parser.parse_args()

    portal = [
        item
        for source, report_type in (
            (args.portal_solucionar, "SOLUCIONAR"),
            (args.portal_pendencias, "AGUARDANDO_SOLUCAO"),
        )
        for item in iter_portal(source, report_type)
        if item.issue_date and item.issue_date.year == 2026
    ]

    op455_by_key: set[tuple[str, str]] = set()
    op455_by_cte_key: set[tuple[str, str]] = set()
    op455_by_invoice: set[str] = set()
    op455_examples: dict[str, tuple[str, str | None]] = {}
    total_op455 = 0
    total_invoices = 0
    grouped_documents = 0
    documents = []

    for document in iter_op455(args.op455):
        documents.append(document)
        total_op455 += 1
        total_invoices += len(document.invoices)
        grouped_documents += int(len(document.invoices) > 1)
        for invoice in document.invoices:
            op455_by_key.add((document.ctrc, invoice.number))
            cte = normalize_identifier(document.cte_number)
            if cte:
                op455_by_cte_key.add((cte, invoice.number))
            op455_by_invoice.add(invoice.number)
            op455_examples.setdefault(invoice.number, (document.ctrc_raw, document.cte_number))

    op930_by_key: set[tuple[str, str]] = set()
    op930_by_invoice: set[str] = set()
    op930_examples: dict[str, str] = {}
    occurrence_counts = Counter()
    oc93_by_ctrc = Counter()
    total_op930 = 0
    occurrences = []

    for occurrence in iter_op930(args.op930):
        occurrences.append(occurrence)
        total_op930 += 1
        occurrence_counts[occurrence.occurrence_code] += 1
        if occurrence.invoice_number:
            op930_by_key.add((occurrence.ctrc, occurrence.invoice_number))
            op930_by_invoice.add(occurrence.invoice_number)
            op930_examples.setdefault(occurrence.invoice_number, occurrence.ctrc_raw)
        if occurrence.occurrence_code == "93":
            oc93_by_ctrc[occurrence.ctrc] += 1

    portal_keys = {(item.ctrc, item.invoice_number) for item in portal}
    portal_invoices = {item.invoice_number for item in portal}
    portal_455_key = len(portal_keys & op455_by_key)
    portal_455_cte_key = len(portal_keys & op455_by_cte_key)
    portal_455_invoice = len(portal_invoices & op455_by_invoice)
    portal_930_key = len(portal_keys & op930_by_key)
    portal_930_invoice = len(portal_invoices & op930_by_invoice)
    portal_june = [item for item in portal if item.issue_date and item.issue_date.month == 6]
    portal_june_keys = {(item.ctrc, item.invoice_number) for item in portal_june}

    print("=== PORTAL 2026 ===")
    print(f"Registros: {len(portal):,}")
    print(f"Chaves CTRC + nota: {len(portal_keys):,}")
    print(f"Registros emitidos em junho: {len(portal_june):,}")
    print()
    print("=== OP455 JUNHO ===")
    print(f"CTRCs: {total_op455:,}")
    print(f"Notas normalizadas: {total_invoices:,}")
    print(f"CTRCs com notas agrupadas: {grouped_documents:,}")
    print()
    print("=== OP930 JUNHO ===")
    print(f"Ocorrências: {total_op930:,}")
    print(f"OC 01: {occurrence_counts['01']:,}")
    print(f"OC 10: {occurrence_counts['10']:,}")
    print(f"OC 93: {occurrence_counts['93']:,}")
    print(f"CTRCs com múltiplas OC 93: {sum(1 for count in oc93_by_ctrc.values() if count > 1):,}")
    print()
    print("=== CRUZAMENTO DO PORTAL ===")
    print(
        "OP455 por CTRC + nota: "
        f"{portal_455_key:,} de {len(portal_keys):,} ({percentual(portal_455_key, len(portal_keys))})"
    )
    print(
        "OP455 somente por nota: "
        f"{portal_455_invoice:,} de {len(portal_invoices):,} "
        f"({percentual(portal_455_invoice, len(portal_invoices))})"
    )
    print(
        "OP455 por CT-e + nota: "
        f"{portal_455_cte_key:,} de {len(portal_keys):,} "
        f"({percentual(portal_455_cte_key, len(portal_keys))})"
    )
    print(
        "OP455 junho por CTRC + nota: "
        f"{len(portal_june_keys & op455_by_key):,} de {len(portal_june_keys):,}"
    )
    portal_june_invoices = {item.invoice_number for item in portal_june}
    print(
        "OP455 junho somente por nota: "
        f"{len(portal_june_invoices & op455_by_invoice):,} de "
        f"{len(portal_june_invoices):,} "
        f"({percentual(len(portal_june_invoices & op455_by_invoice), len(portal_june_invoices))})"
    )
    print(
        "OP930 por CTRC + nota: "
        f"{portal_930_key:,} de {len(portal_keys):,} ({percentual(portal_930_key, len(portal_keys))})"
    )
    print(
        "OP930 somente por nota: "
        f"{portal_930_invoice:,} de {len(portal_invoices):,} "
        f"({percentual(portal_930_invoice, len(portal_invoices))})"
    )
    common_invoices = sorted(portal_invoices & op455_by_invoice & op930_by_invoice)[:5]
    if common_invoices:
        print()
        print("=== AMOSTRA DE IDENTIFICADORES PARA A MESMA NOTA ===")
        portal_by_invoice = {item.invoice_number: item.ctrc_raw for item in portal}
        for invoice in common_invoices:
            ctrc_455, cte_455 = op455_examples[invoice]
            print(
                f"Nota {invoice}: portal CTRC={portal_by_invoice.get(invoice)} | "
                f"OP455 CTRC={ctrc_455} | OP455 CT-e={cte_455} | "
                f"OP930 CTRC={op930_examples[invoice]}"
            )

    matcher = DocumentMatcher(documents, occurrences)
    matches = [matcher.match(item) for item in portal_june]
    statuses = Counter(item.status for item in matches)
    matched = [item for item in matches if item.status == "MATCHED"]
    print()
    print("=== CADEIA PORTAL -> OP455 -> OP930 (EMISSÃO JUNHO) ===")
    print(f"MATCHED: {statuses['MATCHED']:,}")
    print(f"AMBIGUOUS: {statuses['AMBIGUOUS']:,}")
    print(f"NOT_FOUND: {statuses['NOT_FOUND']:,}")
    print(
        "Com ocorrências OP930 no CTRC encontrado: "
        f"{sum(1 for item in matched if item.occurrences):,}"
    )
    for code in ("01", "10", "93"):
        print(
            f"Com OC {code}: "
            f"{sum(1 for item in matched if any(o.occurrence_code == code for o in item.occurrences)):,}"
        )


if __name__ == "__main__":
    main()
