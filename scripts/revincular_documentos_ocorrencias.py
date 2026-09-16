from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pymysql


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.documentos.database import transaction


def contar_pendentes() -> dict:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(
                        CASE
                            WHEN o.ctrc_raw IS NULL OR TRIM(o.ctrc_raw) = ''
                            THEN 1 ELSE 0
                        END
                    ) AS sem_ctrc
                FROM ssw_occurrences o
                WHERE o.document_id IS NULL
                """
            )
            return cursor.fetchone()


def buscar_lote(limit: int) -> list[dict]:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    o.id,
                    o.ctrc_raw
                FROM ssw_occurrences o
                WHERE o.document_id IS NULL
                  AND o.ctrc_raw IS NOT NULL
                  AND TRIM(o.ctrc_raw) <> ''
                ORDER BY o.id
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())


def buscar_documentos_por_ctrc(
    connection,
    ctrcs: list[str],
) -> dict[str, list[int]]:
    if not ctrcs:
        return {}

    placeholders = ",".join(["%s"] * len(ctrcs))

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                ctrc_raw,
                id
            FROM ssw_documents
            WHERE ctrc_raw IN ({placeholders})
            ORDER BY ctrc_raw, id
            """,
            tuple(ctrcs),
        )

        encontrados: dict[str, list[int]] = {}

        for row in cursor.fetchall():
            ctrc_raw = row["ctrc_raw"]
            encontrados.setdefault(ctrc_raw, []).append(
                int(row["id"])
            )

        return encontrados


def processar_lote(registros: list[dict]) -> dict:
    stats = {
        "analisadas": 0,
        "vinculadas": 0,
        "ambiguas": 0,
        "sem_op455": 0,
    }

    if not registros:
        return stats

    ctrcs = list(
        dict.fromkeys(
            str(row["ctrc_raw"]).strip()
            for row in registros
            if row.get("ctrc_raw")
        )
    )

    with transaction() as connection:
        documentos = buscar_documentos_por_ctrc(
            connection,
            ctrcs,
        )

        updates: list[tuple[int, int]] = []

        for row in registros:
            stats["analisadas"] += 1

            occurrence_id = int(row["id"])
            ctrc_raw = str(row["ctrc_raw"]).strip()

            candidatos = documentos.get(ctrc_raw, [])

            if len(candidatos) == 0:
                stats["sem_op455"] += 1
                continue

            if len(candidatos) > 1:
                stats["ambiguas"] += 1
                continue

            updates.append(
                (
                    candidatos[0],
                    occurrence_id,
                )
            )

        if updates:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    UPDATE ssw_occurrences
                    SET document_id = %s
                    WHERE id = %s
                      AND document_id IS NULL
                    """,
                    updates,
                )

            stats["vinculadas"] += len(updates)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Revincula ocorrências OP930 sem document_id "
            "aos documentos históricos da OP455."
        )
    )

    parser.add_argument(
        "--lote",
        type=int,
        default=500,
        help="Quantidade de ocorrências processadas por lote.",
    )

    parser.add_argument(
        "--tentativas",
        type=int,
        default=4,
        help="Tentativas em caso de queda temporária do MySQL.",
    )

    args = parser.parse_args()

    if args.lote <= 0:
        raise ValueError("--lote deve ser maior que zero.")

    antes = contar_pendentes()

    print(
        "Ocorrências sem document_id antes do backfill: "
        f"{int(antes['total'] or 0):,}"
    )

    total = {
        "analisadas": 0,
        "vinculadas": 0,
        "ambiguas": 0,
        "sem_op455": 0,
        "erros": 0,
    }

    processadas_ids_sem_match: set[int] = set()

    while True:
        registros = buscar_lote(args.lote)

        # Evita loop infinito com registros que realmente
        # não possuem correspondente na OP455.
        registros = [
            row
            for row in registros
            if int(row["id"]) not in processadas_ids_sem_match
        ]

        if not registros:
            break

        sucesso = False

        for tentativa in range(1, args.tentativas + 1):
            try:
                stats = processar_lote(registros)
                sucesso = True
                break

            except pymysql.err.OperationalError as exc:
                if tentativa >= args.tentativas:
                    raise

                espera = tentativa * 2

                print(
                    f"MySQL caiu durante lote | "
                    f"tentativa {tentativa}/{args.tentativas} | "
                    f"reconectando em {espera}s | erro={exc}"
                )

                time.sleep(espera)

        if not sucesso:
            total["erros"] += len(registros)
            continue

        total["analisadas"] += stats["analisadas"]
        total["vinculadas"] += stats["vinculadas"]
        total["ambiguas"] += stats["ambiguas"]
        total["sem_op455"] += stats["sem_op455"]

        # Os que não foram vinculados continuarão com document_id NULL.
        # Guardamos os IDs para não buscá-los novamente eternamente.
        if stats["sem_op455"] or stats["ambiguas"]:
            with transaction() as connection:
                ctrcs = list(
                    dict.fromkeys(
                        str(row["ctrc_raw"]).strip()
                        for row in registros
                    )
                )

                documentos = buscar_documentos_por_ctrc(
                    connection,
                    ctrcs,
                )

                for row in registros:
                    candidatos = documentos.get(
                        str(row["ctrc_raw"]).strip(),
                        [],
                    )

                    if len(candidatos) != 1:
                        processadas_ids_sem_match.add(
                            int(row["id"])
                        )

        print(
            f"Analisadas={total['analisadas']:,} | "
            f"Vinculadas={total['vinculadas']:,} | "
            f"Ambíguas={total['ambiguas']:,} | "
            f"Sem OP455={total['sem_op455']:,}"
        )

    depois = contar_pendentes()

    resultado = {
        "success": True,
        **total,
        "pendentes_antes": int(antes["total"] or 0),
        "pendentes_depois": int(depois["total"] or 0),
    }

    print()
    print("=" * 70)
    print("BACKFILL OP930 -> OP455 FINALIZADO")
    print("=" * 70)
    print(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()