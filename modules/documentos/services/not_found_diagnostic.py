from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
import csv
from pathlib import Path
from typing import Iterable

@dataclass(frozen=True)
class NotFoundDiagnostic:
    portal_document_id: int
    report_type: str
    portal_invoice: str
    portal_series: str
    portal_ctrc: str
    portal_ctrc_raw: str
    portal_issue_date: date | None
    portal_recipient_cnpj: str
    portal_recipient_name: str
    op455_invoice_candidates: int
    op455_candidate_ctrcs: str
    op455_candidate_issue_dates: str
    op455_candidate_recipients: str
    op455_exact_ctrc_candidates: int
    op930_invoice_occurrences: int
    op930_invoice_ctrcs: str
    op930_ctrc_occurrences: int
    probable_cause: str
    recommendation: str


def classify_not_found(
    *,
    op455_invoice_candidates: int,
    op455_exact_ctrc_candidates: int,
    op930_invoice_occurrences: int,
    op930_ctrc_occurrences: int,
) -> tuple[str, str]:
    if op455_invoice_candidates == 1:
        return (
            "OP455_HISTORICO_FORA_DA_JANELA",
            "Refazer o vínculo usando o histórico completo da OP455.",
        )
    if op455_invoice_candidates > 1:
        return (
            "MULTIPLOS_CTRCS_NA_OP455",
            "Desempatar por CNPJ/nome do destinatário, série e data de emissão.",
        )
    if op455_exact_ctrc_candidates:
        return (
            "CTRC_NA_OP455_COM_NOTA_DIVERGENTE",
            "Conferir a nota do Portal e as notas vinculadas ao CTRC na OP455.",
        )
    if op930_invoice_occurrences:
        return (
            "NOTA_SOMENTE_NA_OP930",
            "Usar o CTRC candidato da OP930 para buscar/ampliar a OP455.",
        )
    if op930_ctrc_occurrences:
        return (
            "CTRC_SOMENTE_NA_OP930",
            "Conferir a nota informada no Portal para esse CTRC.",
        )
    return (
        "AUSENTE_NAS_BASES_SSW",
        "Ampliar o período/histórico da OP455 e OP930 e conferir o identificador.",
    )


def _unique_join(values: Iterable[object]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = "" if value is None else str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return " | ".join(result)


def _fetch_rows(connection, query: str, params: tuple) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return list(cursor.fetchall())


def diagnose_not_found(period_start: date, period_end: date) -> list[NotFoundDiagnostic]:
    if period_end < period_start:
        raise ValueError("A data final não pode ser anterior à inicial.")

    from modules.documentos.database import get_connection

    connection = get_connection(autocommit=True)
    try:
        portal_rows = _fetch_rows(
            connection,
            """
            SELECT p.id, p.report_type, p.invoice_number, p.invoice_series,
                   p.warehouse_ctrc, p.warehouse_ctrc_raw, p.issue_date,
                   p.recipient_cnpj, p.recipient_name
            FROM document_matches m
            INNER JOIN portal_documents p ON p.id = m.portal_document_id
            WHERE m.status = 'NOT_FOUND'
              AND p.active = 1
              AND p.issue_date BETWEEN %s AND %s
            ORDER BY p.issue_date, p.invoice_number, p.id
            """,
            (period_start, period_end),
        )
        if not portal_rows:
            return []

        invoice_numbers = sorted({row["invoice_number"] for row in portal_rows})
        portal_ctrcs = sorted({row["warehouse_ctrc"] for row in portal_rows if row["warehouse_ctrc"]})

        def placeholders(values: list[str]) -> str:
            return ",".join(["%s"] * len(values))

        op455_by_invoice: dict[str, list[dict]] = defaultdict(list)
        if invoice_numbers:
            rows = _fetch_rows(
                connection,
                f"""
                SELECT i.invoice_number, i.invoice_series, d.id, d.ctrc,
                       d.ctrc_raw, d.issue_date, d.recipient_cnpj,
                       d.recipient_name, imp.period_start, imp.period_end
                FROM ssw_document_invoices i
                INNER JOIN ssw_documents d ON d.id = i.document_id
                LEFT JOIN document_imports imp ON imp.id = d.source_import_id
                WHERE i.invoice_number IN ({placeholders(invoice_numbers)})
                ORDER BY i.invoice_number, d.issue_date, d.ctrc
                """,
                tuple(invoice_numbers),
            )
            for row in rows:
                op455_by_invoice[row["invoice_number"]].append(row)

        exact_ctrc_counts: Counter[str] = Counter()
        if portal_ctrcs:
            rows = _fetch_rows(
                connection,
                f"SELECT ctrc, COUNT(*) AS total FROM ssw_documents "
                f"WHERE ctrc IN ({placeholders(portal_ctrcs)}) GROUP BY ctrc",
                tuple(portal_ctrcs),
            )
            exact_ctrc_counts.update({row["ctrc"]: int(row["total"]) for row in rows})

        op930_by_invoice: dict[str, list[dict]] = defaultdict(list)
        if invoice_numbers:
            rows = _fetch_rows(
                connection,
                f"SELECT invoice_number, ctrc FROM ssw_occurrences "
                f"WHERE invoice_number IN ({placeholders(invoice_numbers)})",
                tuple(invoice_numbers),
            )
            for row in rows:
                op930_by_invoice[row["invoice_number"]].append(row)

        op930_ctrc_counts: Counter[str] = Counter()
        if portal_ctrcs:
            rows = _fetch_rows(
                connection,
                f"SELECT ctrc, COUNT(*) AS total FROM ssw_occurrences "
                f"WHERE ctrc IN ({placeholders(portal_ctrcs)}) GROUP BY ctrc",
                tuple(portal_ctrcs),
            )
            op930_ctrc_counts.update({row["ctrc"]: int(row["total"]) for row in rows})

        diagnostics: list[NotFoundDiagnostic] = []
        for portal in portal_rows:
            candidates = op455_by_invoice[portal["invoice_number"]]
            occurrence_candidates = op930_by_invoice[portal["invoice_number"]]
            cause, recommendation = classify_not_found(
                op455_invoice_candidates=len(candidates),
                op455_exact_ctrc_candidates=exact_ctrc_counts[portal["warehouse_ctrc"]],
                op930_invoice_occurrences=len(occurrence_candidates),
                op930_ctrc_occurrences=op930_ctrc_counts[portal["warehouse_ctrc"]],
            )
            diagnostics.append(NotFoundDiagnostic(
                portal_document_id=int(portal["id"]),
                report_type=portal["report_type"] or "",
                portal_invoice=portal["invoice_number"] or "",
                portal_series=portal["invoice_series"] or "",
                portal_ctrc=portal["warehouse_ctrc"] or "",
                portal_ctrc_raw=portal["warehouse_ctrc_raw"] or "",
                portal_issue_date=portal["issue_date"],
                portal_recipient_cnpj=portal["recipient_cnpj"] or "",
                portal_recipient_name=portal["recipient_name"] or "",
                op455_invoice_candidates=len(candidates),
                op455_candidate_ctrcs=_unique_join(row["ctrc_raw"] for row in candidates),
                op455_candidate_issue_dates=_unique_join(row["issue_date"] for row in candidates),
                op455_candidate_recipients=_unique_join(row["recipient_name"] for row in candidates),
                op455_exact_ctrc_candidates=exact_ctrc_counts[portal["warehouse_ctrc"]],
                op930_invoice_occurrences=len(occurrence_candidates),
                op930_invoice_ctrcs=_unique_join(row["ctrc"] for row in occurrence_candidates),
                op930_ctrc_occurrences=op930_ctrc_counts[portal["warehouse_ctrc"]],
                probable_cause=cause,
                recommendation=recommendation,
            ))
        return diagnostics
    finally:
        connection.close()


CSV_COLUMNS = {
    "portal_document_id": "ID Portal",
    "report_type": "Relatório Portal",
    "portal_invoice": "Nota Portal",
    "portal_series": "Série Portal",
    "portal_ctrc": "CTRC Portal Normalizado",
    "portal_ctrc_raw": "CTRC Portal Original",
    "portal_issue_date": "Data Emissão Portal",
    "portal_recipient_cnpj": "CNPJ Destinatário Portal",
    "portal_recipient_name": "Destinatário Portal",
    "op455_invoice_candidates": "Candidatos OP455 pela Nota",
    "op455_candidate_ctrcs": "CTRCs Candidatos OP455",
    "op455_candidate_issue_dates": "Emissões Candidatas OP455",
    "op455_candidate_recipients": "Destinatários Candidatos OP455",
    "op455_exact_ctrc_candidates": "CTRC Portal Encontrado OP455",
    "op930_invoice_occurrences": "Ocorrências OP930 pela Nota",
    "op930_invoice_ctrcs": "CTRCs Candidatos OP930",
    "op930_ctrc_occurrences": "Ocorrências OP930 pelo CTRC",
    "probable_cause": "Causa Provável",
    "recommendation": "Ação Recomendada",
}


def export_diagnostics_csv(rows: Iterable[NotFoundDiagnostic], output_path: Path) -> dict:
    rows = list(rows)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS.values()), delimiter=";")
        writer.writeheader()
        for item in rows:
            raw = asdict(item)
            writer.writerow({label: raw[key] for key, label in CSV_COLUMNS.items()})
    return {
        "total": len(rows),
        "by_cause": dict(sorted(Counter(row.probable_cause for row in rows).items())),
        "file": str(output_path),
    }
