from __future__ import annotations

from datetime import date

from modules.documentos.database import get_connection
from modules.documentos.repository import DocumentRepository

def carregar_not_found(
    connection,
    inicio: date,
    fim: date,
) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                p.id AS portal_document_id,
                p.invoice_number,
                p.invoice_series,
                p.issue_date,
                p.recipient_name,
                p.warehouse_ctrc,
                m.status AS match_status
            FROM portal_documents p
            INNER JOIN document_matches m
                ON m.portal_document_id = p.id
            WHERE p.issue_date BETWEEN %s AND %s
              AND p.active = 1
              AND m.status IN ('NOT_FOUND', 'AMBIGUOUS')
            ORDER BY p.id
            """,
            (inicio, fim),
        )

        return list(cursor.fetchall())


def reprocessar(
    inicio: date,
    fim: date,
    *,
    progress=print,
) -> dict:
    repository = DocumentRepository()

    connection = get_connection(autocommit=False)

    stats = {
        "total": 0,
        "matched": 0,
        "ambiguous": 0,
        "not_found": 0,
        "errors": 0,
    }

    detalhes = []

    try:
        registros = carregar_not_found(
            connection,
            inicio,
            fim,
        )

        stats["total"] = len(registros)

        progress(
            f"Reprocessando {len(registros)} documentos "
            f"NOT_FOUND ou AMBIGUOUS entre {inicio} e {fim}..."
        )

        for index, registro in enumerate(registros, start=1):
            portal_document_id = int(
                registro["portal_document_id"]
            )

            invoice_number = str(
                registro.get("invoice_number") or ""
            ).strip()

            invoice_series = str(
                registro.get("invoice_series") or ""
            ).strip()

            try:
                if not invoice_number:
                    repository.upsert_match(
                        connection,
                        portal_document_id=portal_document_id,
                        ssw_document_id=None,
                        status="NOT_FOUND",
                        score=0,
                        candidate_count=0,
                    )

                    stats["not_found"] += 1
                    continue

                #
                # 1. Procura a NF no histórico completo da OP930.
                #
                ctrcs_raw = repository.find_occurrence_ctrc_raws_by_invoice(
                    connection,
                    invoice_number,
                    invoice_series or None,
                )

                #
                # Remove duplicidade preservando ordem.
                #
                ctrcs_raw = list(
                    dict.fromkeys(
                        ctrc_raw
                        for ctrc_raw in ctrcs_raw
                        if ctrc_raw
                    )
                )

                #
                # Nenhum CTRC encontrado na OP930.
                #
                if not ctrcs_raw:
                    # Fallback histórico:
                    # se a NF + série existir em exatamente um documento da OP455,
                    # podemos vinculá-la sem depender da OP930.
                    document_ids = repository.find_document_ids_by_invoice(
                        connection,
                        invoice_number,
                        invoice_series or None,
                    )

                    document_ids = list(dict.fromkeys(document_ids))

                    if len(document_ids) == 1:
                        ssw_document_id = document_ids[0]

                        repository.upsert_match(
                            connection,
                            portal_document_id=portal_document_id,
                            ssw_document_id=ssw_document_id,
                            status="MATCHED",
                            score=1,
                            candidate_count=1,
                        )

                        stats["matched"] += 1

                        detalhes.append(
                            {
                                "portal_document_id": portal_document_id,
                                "invoice_number": invoice_number,
                                "invoice_series": invoice_series,
                                "status": "MATCHED",
                                "reason": "FALLBACK_HISTORICO_OP455_NF_SERIE_UNICA",
                                "ssw_document_id": ssw_document_id,
                            }
                        )

                        continue

                    if len(document_ids) > 1:
                        repository.upsert_match(
                            connection,
                            portal_document_id=portal_document_id,
                            ssw_document_id=None,
                            status="AMBIGUOUS",
                            score=0,
                            candidate_count=len(document_ids),
                        )

                        stats["ambiguous"] += 1

                        detalhes.append(
                            {
                                "portal_document_id": portal_document_id,
                                "invoice_number": invoice_number,
                                "invoice_series": invoice_series,
                                "status": "AMBIGUOUS",
                                "reason": "MULTIPLOS_DOCUMENTOS_HISTORICOS_OP455_NF_SERIE",
                                "document_ids": document_ids,
                            }
                        )

                        continue

                    repository.upsert_match(
                        connection,
                        portal_document_id=portal_document_id,
                        ssw_document_id=None,
                        status="NOT_FOUND",
                        score=0,
                        candidate_count=0,
                    )

                    stats["not_found"] += 1

                    detalhes.append(
                        {
                            "portal_document_id": portal_document_id,
                            "invoice_number": invoice_number,
                            "invoice_series": invoice_series,
                            "status": "NOT_FOUND",
                            "reason": "SEM_OP930_E_SEM_OP455_HISTORICO",
                        }
                    )

                    continue

                #
                # Mais de um CTRC distinto para a mesma NF.
                # Não escolhemos no chute.
                #
                if len(ctrcs_raw) > 1:
                    repository.upsert_match(
                        connection,
                        portal_document_id=portal_document_id,
                        ssw_document_id=None,
                        status="AMBIGUOUS",
                        score=0,
                        candidate_count=len(ctrcs_raw),
                    )

                    stats["ambiguous"] += 1

                    detalhes.append(
                        {
                            "portal_document_id": portal_document_id,
                            "invoice_number": invoice_number,
                            "invoice_series": invoice_series,
                            "status": "AMBIGUOUS",
                            "reason": "MULTIPLOS_CTRCS_OP930",
                            "ctrcs": ctrcs_raw,
                        }
                    )

                    continue

                #
                # Existe exatamente um CTRC candidato.
                #
                ctrc_raw = ctrcs_raw[0]

                #
                # 2. Procura esse CTRC no histórico completo da OP455.
                #
                document_ids = (
                    repository.find_document_ids_by_ctrc_raw(
                        connection,
                        ctrc_raw,
                    )
                )

                document_ids = list(
                    dict.fromkeys(document_ids)
                )

                #
                # CTRC apareceu na OP930, mas não existe na OP455.
                #
                if not document_ids:
                    repository.upsert_match(
                        connection,
                        portal_document_id=portal_document_id,
                        ssw_document_id=None,
                        status="NOT_FOUND",
                        score=0,
                        candidate_count=0,
                    )

                    stats["not_found"] += 1

                    detalhes.append(
                        {
                            "portal_document_id": portal_document_id,
                            "invoice_number": invoice_number,
                            "invoice_series": invoice_series,
                            "status": "NOT_FOUND",
                            "reason": "CTRC_OP930_NAO_ENCONTRADO_OP455",
                            "ctrc_raw": ctrc_raw,
                        }
                    )

                    continue

                #
                # Mais de um documento OP455 para o mesmo CTRC.
                #
                if len(document_ids) > 1:
                    repository.upsert_match(
                        connection,
                        portal_document_id=portal_document_id,
                        ssw_document_id=None,
                        status="AMBIGUOUS",
                        score=0,
                        candidate_count=len(document_ids),
                    )

                    stats["ambiguous"] += 1

                    detalhes.append(
                        {
                            "portal_document_id": portal_document_id,
                            "invoice_number": invoice_number,
                            "invoice_series": invoice_series,
                            "status": "AMBIGUOUS",
                            "reason": "MULTIPLOS_DOCUMENTOS_OP455_POR_CTRC",
                            "ctrc_raw": ctrc_raw,
                            "document_ids": document_ids,
                        }
                    )

                    continue

                #
                # Encontramos exatamente:
                #
                # Portal NF
                #   -> OP930
                #   -> CTRC
                #   -> OP455
                #
                ssw_document_id = document_ids[0]

                repository.upsert_match(
                    connection,
                    portal_document_id=portal_document_id,
                    ssw_document_id=ssw_document_id,
                    status="MATCHED",
                    score=1,
                    candidate_count=1,
                )

                stats["matched"] += 1

                detalhes.append(
                    {
                        "portal_document_id": portal_document_id,
                        "invoice_number": invoice_number,
                        "invoice_series": invoice_series,
                        "status": "MATCHED",
                        "reason": "FALLBACK_OP930_CTRC_OP455",
                        "ctrc_raw": ctrc_raw,
                        "ssw_document_id": ssw_document_id,
                    }
                )

                if index % 25 == 0:
                    connection.commit()

                    progress(
                        f"{index}/{len(registros)} | "
                        f"MATCHED={stats['matched']} | "
                        f"AMBIGUOUS={stats['ambiguous']} | "
                        f"NOT_FOUND={stats['not_found']}"
                    )

            except Exception as exc:
                stats["errors"] += 1

                detalhes.append(
                    {
                        "portal_document_id": portal_document_id,
                        "invoice_number": invoice_number,
                        "status": "ERROR",
                        "error": str(exc),
                    }
                )

                progress(
                    f"ERRO | Portal={portal_document_id} | "
                    f"NF={invoice_number} | {exc}"
                )

        connection.commit()

        return {
            "success": True,
            "period": {
                "start": inicio.isoformat(),
                "end": fim.isoformat(),
            },
            **stats,
            "details": detalhes,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        try:
            connection.close()
        except Exception:
            pass
