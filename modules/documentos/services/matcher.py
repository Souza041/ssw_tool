from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from modules.documentos.schemas import OP455Document, OP930Occurrence, PortalDocument
from modules.documentos.services.normalizer import normalized_label


@dataclass
class DocumentMatch:
    portal: PortalDocument
    op455: OP455Document | None
    occurrences: list[OP930Occurrence]
    status: str
    score: int = 0
    candidate_count: int = 0


def _recipient_similarity(left: str | None, right: str | None) -> float:
    left_normalized = normalized_label(left)
    right_normalized = normalized_label(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    if left_normalized in right_normalized or right_normalized in left_normalized:
        return 0.95
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _candidate_score(portal: PortalDocument, document: OP455Document) -> int:
    score = 0
    if portal.recipient_cnpj and document.recipient_cnpj:
        score += 8 if portal.recipient_cnpj == document.recipient_cnpj else -8

    similarity = _recipient_similarity(portal.recipient_name, document.recipient_name)
    if similarity >= 0.95:
        score += 6
    elif similarity >= 0.80:
        score += 3

    if portal.issue_date and document.issue_date:
        score += 2 if portal.issue_date == document.issue_date else 0

    if portal.invoice_series:
        if any(
            invoice.number == portal.invoice_number
            and invoice.series == portal.invoice_series
            for invoice in document.invoices
        ):
            score += 2

    return score


class DocumentMatcher:
    def __init__(
        self,
        documents: Iterable[OP455Document],
        occurrences: Iterable[OP930Occurrence],
    ) -> None:
        self.documents_by_invoice: dict[str, list[OP455Document]] = defaultdict(list)
        self.documents_by_ctrc: dict[str, list[OP455Document]] = defaultdict(list)

        self.occurrences_by_ctrc: dict[str, list[OP930Occurrence]] = defaultdict(list)
        self.occurrences_by_invoice: dict[str, list[OP930Occurrence]] = defaultdict(list)

        for document in documents:
            self.documents_by_ctrc[document.ctrc].append(document)

            for invoice in document.invoices:
                self.documents_by_invoice[invoice.number].append(document)

        for occurrence in occurrences:
            self.occurrences_by_ctrc[occurrence.ctrc].append(occurrence)

            if occurrence.invoice_number:
                self.occurrences_by_invoice[occurrence.invoice_number].append(occurrence)

    def match(self, portal: PortalDocument) -> DocumentMatch:
        candidates = self.documents_by_invoice.get(
            portal.invoice_number,
            [],
        )

        # Fallback:
        # Portal NF -> OP930 NF -> CTRC -> OP455
        if not candidates:
            occurrences_by_invoice = self.occurrences_by_invoice.get(
                portal.invoice_number,
                [],
            )

            candidate_ctrcs = {
                occurrence.ctrc
                for occurrence in occurrences_by_invoice
                if occurrence.ctrc
            }

            if len(candidate_ctrcs) == 1:
                ctrc = next(iter(candidate_ctrcs))

                candidates = self.documents_by_ctrc.get(
                    ctrc,
                    [],
                )

            elif len(candidate_ctrcs) > 1:
                return DocumentMatch(
                    portal=portal,
                    op455=None,
                    occurrences=occurrences_by_invoice,
                    status="AMBIGUOUS",
                    score=0,
                    candidate_count=len(candidate_ctrcs),
                )

        if not candidates:
            return DocumentMatch(
                portal=portal,
                op455=None,
                occurrences=[],
                status="NOT_FOUND",
                score=0,
                candidate_count=0,
            )

        ranked = sorted(
            ((_candidate_score(portal, candidate), candidate) for candidate in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best = ranked[0]

        if len(ranked) > 1 and ranked[1][0] == best_score:
            return DocumentMatch(
                portal,
                None,
                [],
                "AMBIGUOUS",
                score=best_score,
                candidate_count=len(candidates),
            )

        occurrences = list(self.occurrences_by_ctrc.get(best.ctrc, []))
        return DocumentMatch(
            portal,
            best,
            occurrences,
            "MATCHED",
            score=best_score,
            candidate_count=len(candidates),
        )
