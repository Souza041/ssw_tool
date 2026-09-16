from __future__ import annotations

import json
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

from modules.documentos.database import transaction

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "sim",
    }


def _recipient(name: str) -> str:
    # Em modo de teste, absolutamente todos os e-mails
    # do módulo Documentos vão para EMAIL_TEST_TO.
    if _env_bool("EMAIL_TEST_MODE", True):
        recipient = os.getenv("EMAIL_TEST_TO", "").strip()

        if not recipient:
            raise RuntimeError(
                "EMAIL_TEST_MODE está ativo, mas "
                "EMAIL_TEST_TO não foi configurado."
            )

        return recipient

    # Produção: exige explicitamente o destinatário
    # correspondente ao tipo de notificação.
    recipient = os.getenv(name, "").strip()

    if not recipient:
        raise RuntimeError(
            f"Destinatário de produção não configurado: {name}"
        )

    return recipient

def _email_mode() -> str:
    return "TESTE" if _env_bool("EMAIL_TEST_MODE", True) else "PRODUÇÃO"

def _load_alerts(connection: Any, alert_type: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []

    type_filter = ""

    if alert_type:
        type_filter = "AND a.alert_type = %s"
        params.append(alert_type)
        
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT a.*, p.invoice_number, p.transport_number,
                   p.recipient_name, p.pending_reason, s.ctrc, s.ctrc_raw
            FROM document_alerts a
            INNER JOIN portal_documents p ON p.id = a.portal_document_id
            LEFT JOIN ssw_documents s ON s.id = a.ssw_document_id
            WHERE a.status = 'OPEN'
              AND a.alert_type IN ('VENCIDO', 'ALERTA_5')
              {type_filter}
            ORDER BY a.deadline_date ASC, a.id ASC
            """
        , params)
        rows = list(cursor.fetchall())
    for row in rows:
        try:
            row["details_json"] = json.loads(row.get("details") or "{}")
        except (TypeError, json.JSONDecodeError):
            row["details_json"] = {}
    return rows


def _already_sent(connection: Any, alert_id: int, recipient: str, subject: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM notification_history
            WHERE alert_id = %s AND channel = 'EMAIL'
              AND recipient = %s AND subject = %s AND status = 'SENT'
            LIMIT 1
            """,
            (alert_id, recipient, subject),
        )
        return cursor.fetchone() is not None


def _save_history(connection: Any, alert_id: int, recipient: str, subject: str, status: str, error: str | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO notification_history
                (alert_id, channel, recipient, subject, status, attempt_count, error_message, sent_at)
            VALUES (%s, 'EMAIL', %s, %s, %s, 1, %s, IF(%s = 'SENT', NOW(), NULL))
            """,
            (alert_id, recipient, subject, status, error, status),
        )


def _send_email(recipient: str, subject: str, html: str) -> None:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", user or recipient)
    if not host:
        raise RuntimeError("SMTP_HOST não configurado.")

    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.attach(MIMEText(html, "html", "utf-8"))

    smtp_timeout = int(os.getenv("SMTP_TIMEOUT", "60"))

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=smtp_timeout)
    else:
        server = smtplib.SMTP(host, port, timeout=smtp_timeout)
        server.starttls()
    try:
        if user and password:
            server.login(user, password)
        server.sendmail(sender, [recipient], message.as_string())
    finally:
        try:
            server.quit()
        except smtplib.SMTPServerDisconnected:
            pass


def process_notifications(*, send: bool = False, limit: int | None = None, alert_type: str | None = None, force_test: bool = False) -> dict[str, int]:
    stats = {
        "candidates": 0,
        "sent": 0,
        "preview": 0,
        "skipped": 0,
        "errors": 0,
    }

    email_test_mode = _env_bool("EMAIL_TEST_MODE", True)

    if force_test and not email_test_mode:
        raise RuntimeError(
            "--forcar-teste bloqueado: "
            "EMAIL_TEST_MODE precisa estar true."
        )

    if force_test and not send:
        raise RuntimeError(
            "--forcar-teste deve ser utilizado junto com --enviar."
        )

    print()
    print("=" * 70)
    print(
        "MODO DE E-MAIL: "
        + ("TESTE" if email_test_mode else "PRODUÇÃO")
    )

    if email_test_mode:
        test_to = os.getenv("EMAIL_TEST_TO", "").strip()

        if not test_to:
            raise RuntimeError(
                "EMAIL_TEST_MODE=true, mas EMAIL_TEST_TO não foi configurado."
            )

        print(f"Todos os e-mails serão redirecionados para: {test_to}")
    else:
        print("ATENÇÃO: destinatários reais de produção serão utilizados.")

    if force_test:
        print("FORÇAR TESTE: ATIVO")
        print("Alertas com histórico SENT poderão ser reenviados.")

    print("=" * 70)

    # A conexão de banco não pode permanecer aberta durante o SMTP.
    with transaction() as connection:
        alerts = _load_alerts(connection, alert_type=alert_type)
        prepared = []
        for alert in alerts:
            details = alert["details_json"]
            recipient = _recipient("DOCUMENTOS_ALERT_TO")

            if alert["alert_type"] == "VENCIDO":
                prefix = "Documento vencido"
            else:
                prefix = "Documento próximo do vencimento"

            subject = (
                f"[Documentos GCE] {prefix} - "
                f"NF {alert['invoice_number']}"
            )
            already_sent = _already_sent(
                connection,
                int(alert["id"]),
                recipient,
                subject,
            )

            if send and already_sent and not force_test:
                stats["skipped"] += 1
                continue
            body = _build_email_body(alert)
            prepared.append((alert, recipient, subject, body))

    # Sem --limite, respeita obrigatoriamente o lote configurado no .env.
    if limit is None:
        limit = int(os.getenv("DOCUMENTOS_EMAIL_BATCH_SIZE", "5"))

    prepared = prepared[:max(0, limit)]
    stats["candidates"] = len(prepared)

    sleep_seconds = max(0.0, float(os.getenv("EMAIL_SLEEP_SECONDS", "3")))

    for index, (alert, recipient, subject, body) in enumerate(prepared):
        if not send:
            print()
            print("=" * 70)
            print(f"ALERTA #{alert['id']} | {alert['alert_type']}")
            print(f"NF: {alert['invoice_number']}")
            print(f"CTRC: {alert.get('ctrc_raw') or alert.get('ctrc') or '-'}")
            print(f"Destinatário: {alert.get('recipient_name') or '-'}")
            print(f"Prazo: {alert.get('deadline_date') or '-'}")
            print(
                "Dias restantes: "
                f"{alert.get('days_remaining') if alert.get('days_remaining') is not None else '-'}"
            )
            print(f"Modo de e-mail: {_email_mode()}")

            if email_test_mode:
                print("Destinatário original: DOCUMENTOS_ALERT_TO")
                print(f"Destinatário efetivo: {recipient}")
            else:
                print(f"Destinatário: {recipient}")

            print(f"Assunto: {subject}")

            stats["preview"] += 1
            continue
        try:
            _send_email(recipient, subject, body)
            with transaction() as connection:
                _save_history(connection, int(alert["id"]), recipient, subject, "SENT")
            stats["sent"] += 1
        except Exception as exc:
            print()
            print("=" * 70)
            print("ERRO AO ENVIAR NOTIFICAÇÃO")
            print(f"Alert ID: {alert.get('id')}")
            print(f"NF: {alert.get('invoice_number')}")
            print(f"Destinatário: {recipient}")
            print(f"Tipo: {type(exc).__name__}")
            print(f"Erro: {exc}")
            print("=" * 70)

            try:
                with transaction() as connection:
                    _save_history(
                        connection,
                        int(alert["id"]),
                        recipient,
                        subject,
                        "ERROR",
                        str(exc),
                    )
            except Exception as history_exc:
                print(
                    "ERRO adicional ao salvar notification_history: "
                    f"{history_exc}"
                )

            stats["errors"] += 1

        if send and index < len(prepared) - 1 and sleep_seconds:
            time.sleep(sleep_seconds)
    return stats

def _format_date_br(value: Any) -> str:
    if not value:
        return "-"

    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")

    return str(value)


def _build_email_body(alert: dict[str, Any]) -> str:
    invoice = alert.get("invoice_number") or "-"
    recipient_name = alert.get("recipient_name") or "-"
    deadline = _format_date_br(alert.get("deadline_date"))
    delivery = _format_date_br(alert.get("delivery_date"))
    days_remaining = alert.get("days_remaining")

    ctrc = alert.get("ctrc_raw") or alert.get("ctrc") or "-"

    if alert["alert_type"] == "VENCIDO":
        title = "Documento vencido"
        badge = "PRAZO EXPIRADO"
        message = (
            "O prazo para regularização deste documento expirou. "
            "Solicitamos o acompanhamento da pendência."
        )

        overdue_days = abs(int(days_remaining or 0))

        situation = (
            f"{overdue_days} DIA{'S' if overdue_days != 1 else ''} "
            "EM ATRASO"
        )

    else:
        title = "Documento próximo do vencimento"
        badge = "ACOMPANHAMENTO"
        message = (
            "Este documento está se aproximando do prazo limite "
            "para regularização."
        )

        remaining_days = int(days_remaining or 0)

        situation = (
            f"{remaining_days} DIA"
            f"{'S' if remaining_days != 1 else ''} RESTANTE"
            f"{'S' if remaining_days != 1 else ''}"
        )

    return f"""
<!DOCTYPE html>
<html>
<body style="
    margin:0;
    padding:0;
    background:#f3f5f8;
    font-family:Arial,Helvetica,sans-serif;
    color:#172033;
">

<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#f3f5f8;padding:32px 12px;">
<tr>
<td align="center">

<table width="620" cellpadding="0" cellspacing="0"
       style="
           width:100%;
           max-width:620px;
           background:#ffffff;
           border-radius:12px;
           overflow:hidden;
           box-shadow:0 4px 18px rgba(0,0,0,.08);
       ">

    <!-- CABEÇALHO -->
    <tr>
        <td style="
            background:#062b59;
            padding:24px 30px;
            color:#ffffff;
        ">
            <table width="100%">
                <tr>
                    <td>
                        <div style="
                            font-size:22px;
                            font-weight:bold;
                            letter-spacing:.5px;
                        ">
                            RODOBRAS
                        </div>

                        <div style="
                            font-size:11px;
                            margin-top:4px;
                            opacity:.75;
                            letter-spacing:1.5px;
                        ">
                            TRANSPORTES RODOVIÁRIOS
                        </div>
                    </td>

                    <td align="right">
                        <div style="
                            font-size:12px;
                            font-weight:bold;
                            letter-spacing:1px;
                        ">
                            NEXUS
                        </div>

                        <div style="
                            font-size:10px;
                            opacity:.7;
                        ">
                            CENTRAL DE AUTOMAÇÕES
                        </div>
                    </td>
                </tr>
            </table>
        </td>
    </tr>

    <!-- CONTEÚDO -->
    <tr>
        <td style="padding:34px 34px 18px 34px;">

            <div style="
                display:inline-block;
                background:#eef2f7;
                border-radius:20px;
                padding:7px 12px;
                font-size:11px;
                font-weight:bold;
                letter-spacing:.7px;
                color:#44546a;
            ">
                {badge}
            </div>

            <h1 style="
                margin:18px 0 5px 0;
                font-size:25px;
                color:#12243d;
            ">
                {title}
            </h1>

            <div style="
                font-size:15px;
                color:#667085;
                margin-bottom:25px;
            ">
                Nota Fiscal <strong>{invoice}</strong>
            </div>

            <p style="
                font-size:14px;
                line-height:1.7;
                color:#475467;
                margin-bottom:26px;
            ">
                {message}
            </p>

            <!-- DADOS -->
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="
                       background:#f8fafc;
                       border:1px solid #e7ebf0;
                       border-radius:8px;
                   ">

                <tr>
                    <td style="padding:17px 20px;border-bottom:1px solid #e7ebf0;">
                        <span style="font-size:11px;color:#98a2b3;">
                            NOTA FISCAL
                        </span><br>
                        <strong>{invoice}</strong>
                    </td>

                    <td style="padding:17px 20px;border-bottom:1px solid #e7ebf0;">
                        <span style="font-size:11px;color:#98a2b3;">
                            CTRC
                        </span><br>
                        <strong>{ctrc}</strong>
                    </td>
                </tr>

                <tr>
                    <td colspan="2"
                        style="padding:17px 20px;border-bottom:1px solid #e7ebf0;">
                        <span style="font-size:11px;color:#98a2b3;">
                            DESTINATÁRIO
                        </span><br>
                        <strong>{recipient_name}</strong>
                    </td>
                </tr>

                <tr>
                    <td style="padding:17px 20px;">
                        <span style="font-size:11px;color:#98a2b3;">
                            DATA DA ENTREGA
                        </span><br>
                        <strong>{delivery}</strong>
                    </td>

                    <td style="padding:17px 20px;">
                        <span style="font-size:11px;color:#98a2b3;">
                            PRAZO
                        </span><br>
                        <strong>{deadline}</strong>
                    </td>
                </tr>

            </table>

            <!-- SITUAÇÃO -->
            <div style="
                margin-top:22px;
                padding:17px;
                text-align:center;
                background:#f8fafc;
                border-radius:8px;
                font-size:15px;
                font-weight:bold;
                color:#243b5a;
                letter-spacing:.4px;
            ">
                {situation}
            </div>

        </td>
    </tr>

    <!-- RODAPÉ -->
    <tr>
        <td style="
            padding:24px 34px 28px;
            text-align:center;
            color:#98a2b3;
            font-size:11px;
            line-height:1.6;
        ">
            <strong style="color:#667085;">
                RODOBRAS • AUTOMAÇÃO DOCUMENTAL
            </strong>
            <br>
            Mensagem gerada automaticamente pelo Nexus.
            <br>
            Não responda este e-mail.
        </td>
    </tr>

</table>

</td>
</tr>
</table>

</body>
</html>
"""