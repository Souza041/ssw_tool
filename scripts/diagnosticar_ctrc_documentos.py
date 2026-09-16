from modules.documentos.database import get_connection


CTRC = "454108"


def main():
    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            print("\n=== OP930 ===")

            cursor.execute(
                """
                SELECT
                    id,
                    ctrc,
                    ctrc_raw,
                    invoice_number,
                    invoice_series,
                    document_id
                FROM ssw_occurrences
                WHERE ctrc = %s
                   OR ctrc_raw LIKE %s
                LIMIT 20
                """,
                (
                    CTRC,
                    f"%{CTRC}%",
                ),
            )

            for row in cursor.fetchall():
                print(row)

            print("\n=== OP455 por CTRC exato ===")

            cursor.execute(
                """
                SELECT
                    id,
                    ctrc,
                    ctrc_raw,
                    issue_date,
                    recipient_name
                FROM ssw_documents
                WHERE ctrc = %s
                   OR ctrc_raw = %s
                LIMIT 20
                """,
                (
                    CTRC,
                    CTRC,
                ),
            )

            for row in cursor.fetchall():
                print(row)

            print("\n=== OP455 contendo o número ===")

            cursor.execute(
                """
                SELECT
                    id,
                    ctrc,
                    ctrc_raw,
                    issue_date,
                    recipient_name
                FROM ssw_documents
                WHERE ctrc LIKE %s
                   OR ctrc_raw LIKE %s
                LIMIT 50
                """,
                (
                    f"%{CTRC}%",
                    f"%{CTRC}%",
                ),
            )

            for row in cursor.fetchall():
                print(row)

    finally:
        connection.close()


if __name__ == "__main__":
    main()