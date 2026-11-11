import hashlib
import re
from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook

from modules.whirlpool.schemas import EstadoEnvio, ResumoTransporte


ABA_BASE = "BASE"
ABA_EMAIL = "email"
COLUNAS_BASE = (
    "OI", "Transportes", "COLETAS", "Chave", "plantas",
    "data de coleta", "NF", "Cliente", "Cidade", "UF", "SIGLA",
)
COLUNAS_EMAIL = ("SIGLA", "E-MAIL")
COLUNA_EMAIL_ENVIADO = "EMAIL ENVIADO"
COLUNA_ESTADO = "STATUS WHIRLPOOL"
COLUNA_IDEMPOTENCIA = "CHAVE IDEMPOTENCIA"


class PlanilhaWhirlpoolInvalida(ValueError):
    pass


def _normalizar(value) -> str:
    return " ".join(str(value or "").strip().split())


def _header_key(value) -> str:
    return _normalizar(value).casefold()


def _headers(sheet) -> dict[str, int]:
    result = {}
    for column, cell in enumerate(sheet[1], start=1):
        key = _header_key(cell.value)
        if not key:
            continue
        if key in result:
            raise PlanilhaWhirlpoolInvalida("A planilha possui cabeçalho duplicado.")
        result[key] = column
    return result


def _require_columns(headers: dict[str, int], required: tuple[str, ...], sheet: str) -> None:
    missing = [name for name in required if _header_key(name) not in headers]
    if missing:
        raise PlanilhaWhirlpoolInvalida(
            f"Aba {sheet}: colunas obrigatórias ausentes: {', '.join(missing)}."
        )


def _split_recipients(value) -> tuple[str, ...]:
    candidates = [item.strip() for item in re.split(r"[;,]", _normalizar(value))]
    valid = []
    for candidate in candidates:
        if not candidate:
            continue
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", candidate):
            continue
        valid.append(candidate.casefold())
    return tuple(dict.fromkeys(valid))


def _sent(value) -> bool:
    return _header_key(value) in {"sim", "ok", "enviado", "true", "1"}


def _operation_key(row: dict, recipients: tuple[str, ...]) -> str:
    fields = (
        row.get("Transportes"), row.get("OI"), row.get("NF"),
        row.get("Chave"), row.get("COLETAS"), *sorted(recipients),
    )
    canonical = "\x1f".join(_normalizar(value).casefold() for value in fields)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def simular_planilha(input_path: Path, result_path: Path) -> dict:
    try:
        workbook = load_workbook(input_path)
    except Exception as exc:
        raise PlanilhaWhirlpoolInvalida("Arquivo XLSX inválido ou corrompido.") from exc

    try:
        if ABA_BASE not in workbook.sheetnames or ABA_EMAIL not in workbook.sheetnames:
            missing = [name for name in (ABA_BASE, ABA_EMAIL) if name not in workbook.sheetnames]
            raise PlanilhaWhirlpoolInvalida(
                f"Abas obrigatórias ausentes: {', '.join(missing)}."
            )

        base = workbook[ABA_BASE]
        email = workbook[ABA_EMAIL]
        base_headers = _headers(base)
        email_headers = _headers(email)
        _require_columns(base_headers, COLUNAS_BASE, ABA_BASE)
        _require_columns(email_headers, COLUNAS_EMAIL, ABA_EMAIL)

        email_map: dict[str, tuple[str, ...]] = {}
        for row_number in range(2, email.max_row + 1):
            acronym = _header_key(email.cell(row_number, email_headers["sigla"]).value)
            recipients = _split_recipients(
                email.cell(row_number, email_headers["e-mail"]).value
            )
            if acronym and recipients:
                email_map[acronym] = recipients

        for name in (COLUNA_ESTADO, COLUNA_IDEMPOTENCIA):
            key = _header_key(name)
            if key not in base_headers:
                column = base.max_column + 1
                base.cell(1, column, name)
                base_headers[key] = column

        sent_column = base_headers.get(_header_key(COLUNA_EMAIL_ENVIADO))
        grouped = defaultdict(lambda: {
            "recipients": set(), "total": 0, "pending": 0, "sent": 0, "errors": 0,
        })
        operations = []
        for row_number in range(2, base.max_row + 1):
            row = {
                name: base.cell(row_number, base_headers[_header_key(name)]).value
                for name in COLUNAS_BASE
            }
            if not any(_normalizar(value) for value in row.values()):
                continue

            transport = _normalizar(row["Transportes"]) or "(sem transporte)"
            recipients = email_map.get(_header_key(row["SIGLA"]), ())
            already_sent = bool(
                sent_column and _sent(base.cell(row_number, sent_column).value)
            )
            if already_sent:
                state = EstadoEnvio.ENVIADO
                message = "Transporte já marcado como enviado na planilha de origem."
            elif not recipients:
                state = EstadoEnvio.ERRO
                message = "Destinatário não encontrado para a SIGLA informada."
            else:
                state = EstadoEnvio.PENDENTE
                message = "Simulação concluída; nenhum e-mail foi enviado."

            key = _operation_key(row, recipients)
            base.cell(row_number, base_headers[_header_key(COLUNA_ESTADO)], state.value)
            base.cell(row_number, base_headers[_header_key(COLUNA_IDEMPOTENCIA)], key)

            bucket = grouped[transport]
            bucket["recipients"].update(recipients)
            bucket["total"] += 1
            if state is EstadoEnvio.PENDENTE:
                bucket["pending"] += 1
            elif state is EstadoEnvio.ENVIADO:
                bucket["sent"] += 1
            else:
                bucket["errors"] += 1
            operations.append({
                "linha": row_number,
                "transporte": transport,
                "destinatarios": list(recipients),
                "estado": state.value,
                "mensagem": message,
                "chave_idempotencia": key,
            })

        result_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(result_path)
    finally:
        workbook.close()
    summaries = [
        ResumoTransporte(
            transporte=transport,
            destinatarios=tuple(sorted(data["recipients"])),
            total_linhas=data["total"],
            pendentes=data["pending"],
            ja_enviados=data["sent"],
            erros=data["errors"],
        ).as_dict()
        for transport, data in sorted(grouped.items())
    ]
    return {
        "total_linhas": len(operations),
        "total_transportes": len(summaries),
        "resumo_transportes": summaries,
        "operacoes": operations,
    }


def carregar_transportes_portal(input_path: Path) -> list[tuple[str, str]]:
    """Lê somente planta/transporte para homologação; não altera a planilha."""
    try:
        workbook = load_workbook(input_path, read_only=True, data_only=True)
    except Exception as exc:
        raise PlanilhaWhirlpoolInvalida("Arquivo XLSX inválido ou corrompido.") from exc
    try:
        if ABA_BASE not in workbook.sheetnames:
            raise PlanilhaWhirlpoolInvalida("Aba BASE não encontrada.")
        base = workbook[ABA_BASE]
        headers = _headers(base)
        _require_columns(headers, ("Transportes", "plantas"), ABA_BASE)
        items = []
        seen = set()
        for row_number in range(2, base.max_row + 1):
            transporte = _normalizar(
                base.cell(row_number, headers[_header_key("Transportes")]).value
            )
            planta = _normalizar(
                base.cell(row_number, headers[_header_key("plantas")]).value
            )
            if not transporte and not planta:
                continue
            if not transporte or not planta:
                raise PlanilhaWhirlpoolInvalida(
                    f"Linha {row_number}: transporte e planta são obrigatórios."
                )
            key = (planta.casefold(), transporte.casefold())
            if key not in seen:
                seen.add(key)
                items.append((planta, transporte))
        if not items:
            raise PlanilhaWhirlpoolInvalida(
                "Nenhum transporte válido para homologação do portal."
            )
        return items
    finally:
        workbook.close()
