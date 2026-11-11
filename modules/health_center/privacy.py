"""Contrato fechado para dados persistidos pelo Health Center."""

import json
import re
from collections.abc import Mapping


COUNT_KEYS = frozenset({
    "total_op455", "total_after_filters", "total_found_op101",
    "total_not_found_op101", "total_already_exists", "total_pending_launch",
    "total_launched", "total_launch_errors", "total_query_errors",
    "total_ignored_test_scope", "total_ignored_limit", "cwb_curitiba",
    "joi_florianopolis", "big_oc64_allowed_customer",
    "allowed_customer_count", "rejected_customer_count",
})
MAX_COUNT = 1_000_000_000
MAX_METADATA_BYTES = 2048
SUMMARY_OC73 = re.compile(
    r"OC73: OP455=\d{1,10}; filtrados=\d{1,10}; "
    r"OP101=\d{1,10}; erros=\d{1,10}\.\Z"
)
ERROR_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,80}: falha na execução\Z")
SENSITIVE_MARKERS = ("cpf", "senha", "password", "token", "cookie", "documento")


def checked_catalog_text(value: str, *, maximum: int) -> str:
    if (not isinstance(value, str) or not value or
            len(value.encode("utf-8")) > maximum or not value.isprintable() or
            any(marker in value.casefold() for marker in SENSITIVE_MARKERS) or
            re.search(r"\d{11,}", value)):
        raise ValueError("Texto de catálogo fora do contrato.")
    return value


def checked_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", value):
        raise ValueError("Identificador de bot inválido.")
    return value


def checked_execution_id(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", value)
            or re.search(r"\d{11,}", value)):
        raise ValueError("Identificador de execução inválido.")
    return value


def checked_parameters(value: object) -> str:
    # O catálogo ainda não possui parâmetros necessários ao piloto.
    if value != {}:
        raise ValueError("Parâmetros de catálogo não permitidos.")
    return "{}"


def checked_metadata(value: object) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("Metadados inválidos.")
    if set(value) - (COUNT_KEYS | {"dry_run"}):
        raise ValueError("Chave de metadados não permitida.")
    for key, item in value.items():
        if key == "dry_run":
            if type(item) is not bool:
                raise ValueError("Tipo de metadado inválido.")
        elif type(item) is not int or not 0 <= item <= MAX_COUNT:
            raise ValueError("Contador de metadado inválido.")
    encoded = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("Metadados excedem o limite.")
    return encoded


def checked_summary(value: str) -> str:
    if value and value != "Execução interrompida por exceção." and not SUMMARY_OC73.fullmatch(value):
        raise ValueError("Resumo fora do contrato.")
    if len(value.encode("utf-8")) > 200:
        raise ValueError("Resumo excede o limite.")
    return value


def checked_error(value: str | None) -> str | None:
    if value is not None and not ERROR_CLASS.fullmatch(value):
        raise ValueError("Erro fora do contrato.")
    return value
