import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

import time

import os

DATA_DIR = Path("data")


def to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "sim", "yes", "s"}


def to_list(value: str | None) -> list[str]:
    if not value:
        return []

    return [
        item.strip()
        for item in value.replace(",", ";").split(";")
        if item.strip()
    ]


class SettingsGestao:
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    smtp_timeout = int(os.getenv("SMTP_TIMEOUT", "60"))

    email_test_mode = to_bool(os.getenv("EMAIL_TEST_MODE"), False)
    email_test_to = os.getenv("EMAIL_TEST_TO", "")

    email_interno_alertas = to_list(os.getenv("EMAIL_INTERNO_ALERTAS"))

    email_sleep_seconds = float(os.getenv("EMAIL_SLEEP_SECONDS", "2"))

    email_retry_attempts = int(os.getenv("EMAIL_RETRY_ATTEMPTS", "3"))
    email_retry_sleep = int(os.getenv("EMAIL_RETRY_SLEEP", "15"))


settings = SettingsGestao()


FILIAIS_EMAILS_FILE = DATA_DIR / "filiais_emails.xlsx"


def carregar_emails_filiais(file_path: Path = FILIAIS_EMAILS_FILE) -> dict[str, list[str]]:
    if not file_path.exists():
        raise FileNotFoundError(f"Planilha de e-mails não encontrada: {file_path}")

    df = pd.read_excel(file_path)

    required = {"SIGLA", "E-MAIL"}
    missing = required - set(df.columns.str.upper())

    if missing:
        raise ValueError(f"Planilha de e-mails precisa ter as colunas: {required}")

    df.columns = [str(col).strip().upper() for col in df.columns]

    emails_por_filial: dict[str, list[str]] = {}

    for _, row in df.iterrows():
        unidade = str(row["SIGLA"]).strip().upper()
        emails_raw = str(row["E-MAIL"]).strip()

        if not unidade or not emails_raw or emails_raw.lower() == "nan":
            continue

        emails = [
            email.strip()
            for email in emails_raw.replace(",", ";").split(";")
            if email.strip()
        ]

        emails_por_filial[unidade] = emails

    return emails_por_filial


def dataframe_to_html(df: pd.DataFrame, limite: int = 200) -> str:
    if df.empty:
        return "<p>Nenhum registro encontrado.</p>"

    colunas_exibir = [
        col for col in [
            "Serie/Numero CTRC",
            "Tipo do Documento",
            "Cliente Pagador",
            "Cliente Destinatario",
            "Cidade de Entrega",
            "UF de Entrega",
            "Unidade Receptora",
            "Numero da Nota Fiscal",
            "Mercadoria",
            "TIPO_MERCADORIA",
            "Codigo da Ultima Ocorrencia",
            "Descricao da Ultima Ocorrencia",
            "Previsao de Entrega",
            "DIAS_EM_ATRASO",
            "Entrega Programada",
            "Data da Entrega Realizada",
        ]
        if col in df.columns
    ]

    base = df[colunas_exibir].head(limite).copy()

    if "DIAS_EM_ATRASO" in base.columns:
        base = base.rename(columns={
            "DIAS_EM_ATRASO": "Dias em Atraso"
        })

    return base.to_html(
        index=False,
        border=0,
        justify="center",
    )


def montar_html_alerta(
    filial: str,
    atrasados: pd.DataFrame,
    pre_alerta: pd.DataFrame,
) -> str:
    total_atrasados = len(atrasados)
    total_pre_alerta = len(pre_alerta)

    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; font-size: 13px;">
            <p>Prezados Parceiros boa tarde!</p>

            <p>
                Segue relação de NFs em aberto no sistema SSW.<br>
                Esta comunicação é uma sinalização de <strong>Casos em atraso e prevenção de futuros atrasos.</strong><br>
                Identificamos pedidos com necessidade de atenção na unidade
                <strong>{filial}</strong>.
            </p>

            <ul>
                <li><strong>Pedidos em atraso:</strong> {total_atrasados}</li>
                <li><strong>Pedidos em pré-alerta:</strong> {total_pre_alerta}</li>
            </ul>

            <h3>Pedidos em atraso</h3>
            {dataframe_to_html(atrasados)}

            <h3>Pedidos em pré-alerta</h3>
            {dataframe_to_html(pre_alerta)}

            <p>
                Pedimos a gentileza de atualizar as informações no sistema SSW.<br>
                Casos que vencem na data de hoje, por gentileza contatar o cliente e inserir agendamento com a data alinhada.
            </p>

            <p><mark style="background-color: yellow;">Obs. Não responder o e-mail.</mark></p>

            <p>
                Atenciosamente,<br>
                Gestão Rodobras
            </p>
        </body>
    </html>
    """

def montar_html_interno(
    filial: str,
    atrasados: pd.DataFrame,
    pre_alerta: pd.DataFrame,
    pendencias: pd.DataFrame,
) -> str:
    total_atrasados = len(atrasados)
    total_pre_alerta = len(pre_alerta)
    
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; font-size: 13px;">
            <p>Prezados,</p>

            <p>
                Segue relação de pendências gestão identificadas para tratativa interna.<br>
                Unidade: <strong>{filial}</strong>.
            </p>

            <ul>
                <li><strong>Pedidos em atraso:</strong> {total_atrasados}</li>
                <li><strong>Pedidos em pré-alerta:</strong> {total_pre_alerta}</li>
                <li><strong>Pendências gestão:</strong> {len(pendencias)}</li>
            </ul>

            <h3>Pendências gestão</h3>
            {dataframe_to_html(pendencias)}

            <p>
                Favor analisar e seguir com a tratativa necessária.
            </p>

            <p>
                Atenciosamente,<br>
                Gestão Rodobras
            </p>
        </body>
    </html>
    """

def montar_html_parceiro(
    filial: str,
    atrasados: pd.DataFrame,
    pre_alerta: pd.DataFrame,
) -> str:
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; font-size: 13px;">
            <p>Prezados Parceiros, boa tarde!</p>

            <p>
                Segue relação de NFs em aberto no sistema SSW.<br>
                Esta comunicação é uma sinalização de <strong>casos em atraso e prevenção de futuros atrasos</strong>.<br>
                Unidade: <strong>{filial}</strong>.
            </p>

            <ul>
                <li><strong>Pedidos em atraso:</strong> {len(atrasados)}</li>
                <li><strong>Pedidos em pré-alerta:</strong> {len(pre_alerta)}</li>
            </ul>

            <h3>Pedidos em atraso</h3>
            {dataframe_to_html(atrasados)}

            <h3>Pedidos em pré-alerta</h3>
            {dataframe_to_html(pre_alerta)}

            <p>
                Pedimos a gentileza de atualizar as informações no sistema SSW.
            </p>

            <p><mark style="background-color: yellow;">Obs. Não responder o e-mail.</mark></p>

            <p>
                Atenciosamente,<br>
                Gestão Rodobras
            </p>
        </body>
    </html>
    """

def criar_servidor_smtp():
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout,
        )
    else:
        server = smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout,
        )
        server.starttls()

    if settings.smtp_user and settings.smtp_password:
        server.login(settings.smtp_user, settings.smtp_password)

    return server


def enviar_email(
    server,
    destinatarios: list[str],
    assunto: str,
    html: str,
) -> None:
    if not destinatarios:
        return

    message = MIMEMultipart("alternative")
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(destinatarios)
    message["Subject"] = assunto

    message.attach(MIMEText(html, "html", "utf-8"))

    server.sendmail(
        settings.smtp_from,
        destinatarios,
        message.as_string(),
    )


def obter_destinatarios(
    filial: str,
    emails_por_filial: dict[str, list[str]],
) -> list[str]:
    if settings.email_test_mode:
        return [settings.email_test_to]

    return emails_por_filial.get(filial, [])


def enviar_alertas_por_filial(
    filtros: dict[str, pd.DataFrame],
    logger=None,
) -> None:
    emails_por_filial = carregar_emails_filiais()

    todas_filiais = set()

    for df in filtros.values():
        if "_UNIDADE_ALERTA" in df.columns:
            todas_filiais.update(df["_UNIDADE_ALERTA"].dropna().unique())

    server = criar_servidor_smtp()

    try:
        for filial in sorted(todas_filiais):
            atrasados = filtros["em_atraso"][
                filtros["em_atraso"]["_UNIDADE_ALERTA"] == filial
            ]

            pre_alerta = filtros["pre_alerta"][
                filtros["pre_alerta"]["_UNIDADE_ALERTA"] == filial
            ]

            pendencias = filtros["pendencia_gestao"][
                filtros["pendencia_gestao"]["_UNIDADE_ALERTA"] == filial
            ]

            base_filial = pd.concat(
                [atrasados, pre_alerta, pendencias],
                ignore_index=True,
            )

            if base_filial.empty:
                continue

            base_parceiro = base_filial[
                base_filial["_DESTINO_ALERTA"] == "PARCEIRO"
            ].copy()

            base_interno = base_filial[
                base_filial["_DESTINO_ALERTA"] == "INTERNO"
            ].copy()

            envios = [
                ("PARCEIRO", base_parceiro, emails_por_filial.get(filial, [])),
                ("INTERNO", base_interno, settings.email_interno_alertas),
            ]

            hoje = pd.Timestamp.today().date()

            for tipo_envio, base_envio, destinatarios in envios:
                if base_envio.empty:
                    continue

                destinatarios = list(dict.fromkeys(destinatarios))

                if settings.email_test_mode:
                    destinatarios = [settings.email_test_to]

                if not destinatarios:
                    if logger:
                        logger.warning(
                            "Sem destinatários cadastrados | unidade=%s | tipo=%s",
                            filial,
                            tipo_envio,
                        )
                    continue

                if tipo_envio == "PARCEIRO":
                    assunto = f"[Gestão Tool] Alerta Operacional - Unidade {filial}"
                    html = montar_html_parceiro(
                        filial=filial,
                        atrasados=base_envio[
                            base_envio["_DATA_PREVISAO"] < hoje
                        ],
                        pre_alerta=base_envio[
                            base_envio["_DATA_PREVISAO"] >= hoje
                        ],
                    )

                else:
                    assunto = f"[Gestão Tool] Pendências Gestão - Unidade {filial}"

                    atrasados_parceiro = base_parceiro[
                        base_parceiro["_DATA_PREVISAO"] < hoje
                    ]

                    pre_alerta_parceiro = base_parceiro[
                        base_parceiro["_DATA_PREVISAO"] >= hoje
                    ]

                    html = montar_html_interno(
                        filial=filial,
                        atrasados=atrasados_parceiro,
                        pre_alerta=pre_alerta_parceiro,
                        pendencias=base_envio,
                    )

                ok, server = enviar_email_com_retry(
                    server=server,
                    destinatarios=destinatarios,
                    assunto=assunto,
                    html=html,
                    logger=logger,
                )

                if not ok:
                    if logger:
                        logger.error(
                            "E-mail não enviado após tentativas | unidade=%s | tipo=%s",
                            filial,
                            tipo_envio,
                        )
                    continue

                time.sleep(settings.email_sleep_seconds)

                if logger:
                    logger.info(
                        "E-mail enviado | unidade=%s | tipo=%s | destinatarios=%s",
                        filial,
                        tipo_envio,
                        destinatarios,
                    )

    finally:
        try:
            server.quit()
        except Exception:
            pass

def enviar_email_com_retry(
    server,
    destinatarios: list[str],
    assunto: str,
    html: str,
    tentativas: int | None = None,
    pausa: float | None = None,
    logger=None,
):
    tentativas = tentativas or settings.email_retry_attempts
    pausa = pausa or settings.email_retry_sleep

    for tentativa in range(1, tentativas + 1):
        try:
            enviar_email(
                server=server,
                destinatarios=destinatarios,
                assunto=assunto,
                html=html,
            )
            return True, server

        except Exception as exc:
            if logger:
                logger.warning(
                    "Falha SMTP ao enviar e-mail tentativa %s/%s | destinatarios=%s | erro=%s",
                    tentativa,
                    tentativas,
                    destinatarios,
                    exc,
                )

            try:
                server.quit()
            except Exception:
                pass

            if tentativa >= tentativas:
                return False, None

            time.sleep(pausa)
            server = criar_servidor_smtp()

    return False, None