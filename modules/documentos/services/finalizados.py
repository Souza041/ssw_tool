from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from modules.documentos.database import transaction
from modules.documentos.parsers.portal_finalizados import (
    PortalFinalizado,
    iter_portal_finalizados,
)
from modules.documentos.repository import DocumentRepository


@dataclass
class FinalizadosImportStats:
    total: int = 0
    matched: int = 0
    already_finalized: int = 0
    not_found: int = 0
    ambiguous: int = 0


def _text(value) -> str:
    return str(value or "").strip().upper()


def _key(
    carrier_cnpj,
    invoice_number,
    invoice_series,
) -> tuple[str, str, str]:
    return (
        _text(carrier_cnpj),
        _text(invoice_number),
        _text(invoice_series),
    )


def _same(value1, value2) -> bool:
    return _text(value1) == _text(value2)


def _resolve_candidate(
    item: PortalFinalizado,
    candidates: list[dict],
) -> dict | None:

    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    filtered = candidates

    # 1. Número de transporte
    if item.transport_number:
        matches = [
            row
            for row in filtered
            if _same(
                row.get("transport_number"),
                item.transport_number,
            )
        ]

        if matches:
            filtered = matches

    if len(filtered) == 1:
        return filtered[0]

    # 2. Data de emissão
    if item.issue_date:
        matches = [
            row
            for row in filtered
            if row.get("issue_date") == item.issue_date
        ]

        if matches:
            filtered = matches

    if len(filtered) == 1:
        return filtered[0]

    # 3. CNPJ do destinatário
    if item.recipient_cnpj:
        matches = [
            row
            for row in filtered
            if _same(
                row.get("recipient_cnpj"),
                item.recipient_cnpj,
            )
        ]

        if matches:
            filtered = matches

    if len(filtered) == 1:
        return filtered[0]

    return None


def import_finalizados(
    file_path: Path,
) -> FinalizadosImportStats:

    stats = FinalizadosImportStats()

    print("[FINALIZADOS] Lendo planilha...")

    items = list(
        iter_portal_finalizados(file_path)
    )

    stats.total = len(items)

    print(
        f"[FINALIZADOS] {stats.total:,} registros lidos."
    )

    if not items:
        print(
            "[FINALIZADOS] Nenhum documento finalizado "
            "encontrado no arquivo."
        )
        return stats

    # daqui para baixo continua seu código atual...

    carrier_cnpjs = {
        item.carrier_cnpj
        for item in items
        if item.carrier_cnpj
    }

    print(
        "[FINALIZADOS] Carregando documentos "
        "candidatos do banco..."
    )

    with transaction() as connection:

        rows = (
            DocumentRepository
            .load_portal_finalization_candidates(
                connection,
                carrier_cnpjs=carrier_cnpjs,
            )
        )

        print(
            f"[FINALIZADOS] {len(rows):,} documentos "
            "candidatos carregados."
        )

        #
        # Índice:
        #
        # (CNPJ transportador, NF, série)
        #       ↓
        # [portal_document, portal_document, ...]
        #
        index: dict[
            tuple[str, str, str],
            list[dict],
        ] = defaultdict(list)

        for row in rows:
            index[
                _key(
                    row.get("carrier_cnpj"),
                    row.get("invoice_number"),
                    row.get("invoice_series"),
                )
            ].append(row)

        print(
            f"[FINALIZADOS] Índice criado com "
            f"{len(index):,} chaves."
        )

        for position, item in enumerate(
            items,
            start=1,
        ):
            candidates = index.get(
                _key(
                    item.carrier_cnpj,
                    item.invoice_number,
                    item.invoice_series,
                ),
                [],
            )

            if not candidates:
                stats.not_found += 1
                continue

            candidate = _resolve_candidate(
                item,
                candidates,
            )

            if candidate is None:
                stats.ambiguous += 1
                continue

            if (
                candidate.get("finalized_at")
                == item.finalized_at
            ):
                stats.already_finalized += 1
                continue

            DocumentRepository.set_portal_finalized(
                connection,
                portal_document_id=int(
                    candidate["id"]
                ),
                finalized_at=item.finalized_at,
            )

            #
            # Atualiza também nosso objeto em memória.
            # Evita UPDATE duplicado se o XLSX trouxer
            # o mesmo documento mais de uma vez.
            #
            candidate["finalized_at"] = (
                item.finalized_at
            )

            stats.matched += 1

            if position % 5000 == 0:
                print(
                    "[FINALIZADOS] "
                    f"{position:,}/{stats.total:,} "
                    "processados..."
                )

    print(
        "[FINALIZADOS] Concluído | "
        f"total={stats.total:,} | "
        f"matched={stats.matched:,} | "
        f"already={stats.already_finalized:,} | "
        f"not_found={stats.not_found:,} | "
        f"ambiguous={stats.ambiguous:,}"
    )

    return stats