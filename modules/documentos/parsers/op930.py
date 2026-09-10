from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from modules.documentos.schemas import OP930Occurrence
from modules.documentos.services.normalizer import (
    clean_text,
    combine_datetime,
    normalize_cnpj,
    normalize_ctrc,
    normalize_identifier,
    normalize_occurrence_code,
    normalized_label,
    optional_text,
    parse_date,
)


REQUIRED_COLUMNS = {
    "CTRC",
    "NRO_NOTA_FISCAL",
    "COD_OCOR",
    "DATA_OCOR",
    "DIA_INCLUSAO_OCOR",
}


def iter_op930(file_path: Path, include_canceled: bool = False) -> Iterator[OP930Occurrence]:
    with Path(file_path).open(encoding="latin1", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(
                "Layout OP930 inválido. Colunas ausentes: "
                + ", ".join(sorted(missing))
            )

        for record in reader:
            canceled = normalized_label(record.get("CANCELADO")) in {"SIM", "S"}
            if canceled and not include_canceled:
                continue

            ctrc_raw = clean_text(record.get("CTRC"))
            ctrc = normalize_ctrc(ctrc_raw)
            occurrence_code = normalize_occurrence_code(record.get("COD_OCOR"))
            if not ctrc or not occurrence_code:
                continue

            yield OP930Occurrence(
                ctrc=ctrc,
                ctrc_raw=ctrc_raw,
                invoice_number=normalize_identifier(record.get("NRO_NOTA_FISCAL")) or None,
                invoice_series=normalize_identifier(record.get("SERIE_NOTA_FISCAL")) or None,
                occurrence_code=occurrence_code,
                occurrence_description=optional_text(record.get("DESCRICAO_OCOR")),
                occurrence_complement=optional_text(record.get("COMPLEMENTO_OCOR")),
                occurrence_at=combine_datetime(record.get("DATA_OCOR"), record.get("HORA_OCOR")),
                included_at=combine_datetime(
                    record.get("DIA_INCLUSAO_OCOR"),
                    record.get("HORA_INCLUSAO_OCOR"),
                ),
                occurrence_user=optional_text(record.get("USUARIO_OCOR")),
                occurrence_company=optional_text(record.get("EMPR_OCOR")),
                occurrence_unit=optional_text(record.get("UNID_OCOR")),
                delivery_date=parse_date(record.get("DATA_ENTREGA")),
                payer_cnpj=normalize_cnpj(record.get("CNPJ_PAGADOR")),
                payer_name=optional_text(record.get("NOME_PAGADOR")),
                canceled=canceled,
            )

