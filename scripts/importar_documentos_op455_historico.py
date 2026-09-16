from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

import os

import time

import pymysql

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from modules.documentos.database import transaction
from modules.documentos.parsers.op455 import iter_op455
from modules.documentos.repository import DocumentRepository
from operations.op455.report import OP455Report
from ssw.client import SSWClient
from modules.documentos.config import settings

OUTPUT_DIR = Path("data/documentos/historico/op455")


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()

    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use o formato AAAA-MM-DD."
        ) from exc


def data_ssw(value: date) -> str:
    return value.strftime("%d%m%y")


def gerar_periodos(
    inicio: date,
    fim: date,
    dias: int = 30,
):
    atual = inicio

    while atual <= fim:
        final = min(
            atual + timedelta(days=dias - 1),
            fim,
        )

        yield atual, final

        atual = final + timedelta(days=1)


def criar_client_logado() -> SSWClient:
    settings.validate_ssw()

    print(
        f"Login SSW Documentos | "
        f"usuário={settings.ssw_usuario} | "
        f"unidade={settings.ssw_unidade}"
    )

    client = SSWClient(
        dominio=settings.ssw_dominio,
        cpf=settings.ssw_cpf,
        usuario=settings.ssw_usuario,
        senha=settings.ssw_senha,
        unidade=settings.ssw_unidade,
    )

    client.login()
    client.open_menu()

    print("Login SSW Documentos concluído.")

    return client


def importar_op455(
    arquivo: Path,
    inicio: date,
    fim: date,
    commit_every: int = 500,
) -> dict:
    repository = DocumentRepository()

    with transaction() as connection:
        import_id = repository.create_import(
            connection,
            source="OP455",
            file_path=arquivo,
            period_start=inicio,
            period_end=fim,
        )

    total = 0
    inserted = 0
    updated = 0
    rejected = 0

    batch = []

    def salvar_batch():
        nonlocal inserted, updated

        if not batch:
            return

        max_tentativas = 8

        for tentativa in range(1, max_tentativas + 1):
            try:
                with transaction() as connection:
                    inseridos_lote = 0
                    atualizados_lote = 0

                    for document in batch:
                        _, was_inserted = repository.upsert_ssw_document(
                            connection,
                            document,
                            import_id,
                        )

                        if was_inserted:
                            inseridos_lote += 1
                        else:
                            atualizados_lote += 1

                inserted += inseridos_lote
                updated += atualizados_lote

                batch.clear()
                return

            except pymysql.err.OperationalError as exc:
                code = exc.args[0] if exc.args else None

                if code not in {2003, 2006, 2013}:
                    raise

                if tentativa >= max_tentativas:
                    raise

                espera = min(2 ** (tentativa - 1), 30)

                print(
                    f"MySQL caiu durante lote | tentativa "
                    f"{tentativa}/{max_tentativas} | "
                    f"reconectando em {espera}s..."
                )

                time.sleep(espera)

    try:
        for document in iter_op455(arquivo):
            total += 1
            batch.append(document)

            if len(batch) >= commit_every:
                salvar_batch()

                print(
                    f"OP455 {inicio} -> {fim} | "
                    f"{total:,} lidos | "
                    f"{inserted:,} inseridos | "
                    f"{updated:,} atualizados"
                )

        salvar_batch()

        with transaction() as connection:
            repository.finish_import(
                connection,
                import_id,
                total_rows=total,
                inserted_rows=inserted,
                updated_rows=updated,
                rejected_rows=rejected,
            )

        return {
            "total": total,
            "inserted": inserted,
            "updated": updated,
            "rejected": rejected,
        }

    except Exception as exc:
        try:
            with transaction() as connection:
                repository.fail_import(
                    connection,
                    import_id,
                    exc,
                )
        except Exception as status_exc:
            print(
                "Não foi possível registrar o status ERROR "
                f"da importação no banco: {status_exc}"
            )

        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Baixa e importa o histórico da OP455 "
            "para o módulo Documentos GCE."
        )
    )

    parser.add_argument(
        "--inicio",
        required=True,
        type=parse_date,
    )

    parser.add_argument(
        "--fim",
        required=True,
        type=parse_date,
    )

    parser.add_argument(
        "--dias-por-periodo",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
    )

    parser.add_argument(
        "--commit-every",
        type=int,
        default=500,
    )

    args = parser.parse_args()

    if args.fim < args.inicio:
        parser.error(
            "A data final não pode ser anterior à inicial."
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    client = criar_client_logado()

    op455 = OP455Report(client)

    totais = {
        "periodos": 0,
        "total": 0,
        "inserted": 0,
        "updated": 0,
        "rejected": 0,
    }

    for inicio, fim in gerar_periodos(
        args.inicio,
        args.fim,
        args.dias_por_periodo,
    ):
        totais["periodos"] += 1

        print()
        print(
            "=" * 70
        )

        print(
            f"OP455 histórico | "
            f"{inicio} -> {fim}"
        )

        print(
            "=" * 70
        )

        #
        # IMPORTANTE:
        # usamos o método EXCLUSIVO de Documentos,
        # com o payload f37=F / f38=C / f39=D.
        #
        arquivo = op455.gerar_e_baixar_documentos(
            output_dir=OUTPUT_DIR,
            data_inicial=data_ssw(inicio),
            data_final=data_ssw(fim),
            timeout_seconds=args.timeout,
        )

        print(
            f"Arquivo baixado: {arquivo}"
        )

        stats = importar_op455(
            arquivo=arquivo,
            inicio=inicio,
            fim=fim,
            commit_every=args.commit_every,
        )

        totais["total"] += stats["total"]
        totais["inserted"] += stats["inserted"]
        totais["updated"] += stats["updated"]
        totais["rejected"] += stats["rejected"]

        print(
            f"Período concluído | "
            f"total={stats['total']:,} | "
            f"inseridos={stats['inserted']:,} | "
            f"atualizados={stats['updated']:,}"
        )

    print()
    print("=" * 70)
    print("CARGA HISTÓRICA OP455 FINALIZADA")
    print("=" * 70)

    print(
        f"Períodos: {totais['periodos']}"
    )

    print(
        f"Total lido: {totais['total']:,}"
    )

    print(
        f"Inseridos: {totais['inserted']:,}"
    )

    print(
        f"Atualizados: {totais['updated']:,}"
    )

    print(
        f"Rejeitados: {totais['rejected']:,}"
    )


if __name__ == "__main__":
    main()