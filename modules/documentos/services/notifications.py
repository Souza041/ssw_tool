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

DEFAULT_RECIPIENT = "suporte.ti2@rodobrastransp.com.br"


def _recipient(name: str) -> str:
    return os.getenv(name, DEFAULT_RECIPIENT).strip() or DEFAULT_RECIPIENT


def _load_alerts(connection: Any) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.*, p.invoice_number, p.transport_number,
                   p.recipient_name, p.pending_reason, s.ctrc
            FROM document_alerts a
            INNER JOIN portal_documents p ON p.id = a.portal_document_id
            LEFT JOIN ssw_documents s ON s.id = a.ssw_document_id
            WHERE a.status = 'OPEN'
              AND a.alert_type IN ('VENCIDO', 'ALERTA_5')
            ORDER BY a.deadline_date ASC, a.id ASC
            """
        )
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


def process_notifications(*, send: bool = False, limit: int | None = None) -> dict[str, int]:
    stats = {"candidates": 0, "sent": 0, "preview": 0, "skipped": 0, "errors": 0}

    # A conexão de banco não pode permanecer aberta durante o SMTP.
    with transaction() as connection:
        alerts = _load_alerts(connection)
        prepared = []
        for alert in alerts:
            details = alert["details_json"]
            is_oc10 = bool(details.get("has_oc10"))
            recipient = _recipient("DOCUMENTOS_OC10_TO" if is_oc10 else "DOCUMENTOS_ALERT_TO")
            prefix = "OC 10 - Baixa necessária" if is_oc10 else ("Documento vencido" if alert["alert_type"] == "VENCIDO" else "Documento próximo do vencimento")
            subject = f"[Documentos GCE] {prefix} - NF {alert['invoice_number']}"
            if _already_sent(connection, int(alert["id"]), recipient, subject):
                stats["skipped"] += 1
                continue
            body = (f"<p>Solicitamos a baixa da nota fiscal <strong>{alert['invoice_number']}</strong>.</p><p>CTRC: {alert.get('ctrc') or '-'}<br>Destinatário: {alert.get('recipient_name') or '-'}</p>" if is_oc10 else f"<p>Documento com acompanhamento necessário.</p><p>Nota: <strong>{alert['invoice_number']}</strong><br>CTRC: {alert.get('ctrc') or '-'}<br>Destinatário: {alert.get('recipient_name') or '-'}<br>Prazo: {alert.get('deadline_date') or '-'}<br>Dias restantes: {alert.get('days_remaining') if alert.get('days_remaining') is not None else '-'}</p>")
            prepared.append((alert, recipient, subject, body))

    # Sem --limite, respeita obrigatoriamente o lote configurado no .env.
    if limit is None:
        limit = int(os.getenv("DOCUMENTOS_EMAIL_BATCH_SIZE", "5"))

    prepared = prepared[:max(0, limit)]
    stats["candidates"] = len(prepared)

    sleep_seconds = max(0.0, float(os.getenv("EMAIL_SLEEP_SECONDS", "3")))

    for index, (alert, recipient, subject, body) in enumerate(prepared):
        if not send:
            with transaction() as connection:
                _save_history(connection, int(alert["id"]), recipient, subject, "PREVIEW")
            stats["preview"] += 1
            continue
        try:
            _send_email(recipient, subject, body)
            with transaction() as connection:
                _save_history(connection, int(alert["id"]), recipient, subject, "SENT")
            stats["sent"] += 1
        except Exception as exc:
            try:
                with transaction() as connection:
                    _save_history(connection, int(alert["id"]), recipient, subject, "ERROR", str(exc))
            except Exception:
                pass
            stats["errors"] += 1

        if send and index < len(prepared) - 1 and sleep_seconds:
            time.sleep(sleep_seconds)
    return stats
