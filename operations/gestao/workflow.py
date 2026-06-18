from pathlib import Path

from operations.op455.report import OP455Report
from operations.gestao.parser import tratar_relatorio_455
from operations.gestao.filters import filtrar_pedidos_455
from operations.gestao.email_alert import enviar_alertas_por_filial
from ssw.client import SSWClient

from operations.gestao.instrucoes import processar_instrucoes_gestao


def executar_gestao_op455(
    data_inicial: str,
    data_final: str,
    output_dir: Path,
    job=None,
    log_func=None,
):
    def log(msg: str):
        if log_func:
            log_func(msg)

        if job:
            from web.jobs import add_log
            add_log(job, msg)

    log("Iniciando login fixo via .env...")
    client = SSWClient()
    client.login()
    client.open_menu()
    log("Login SSW concluído.")

    log(f"Baixando relatório OP455: {data_inicial} até {data_final}")
    op455 = OP455Report(client)

    arquivo = op455.gerar_e_baixar_por_datas(
        output_dir=output_dir,
        data_inicial=data_inicial,
        data_final=data_final,
        timeout_seconds=300,
    )

    log(f"Relatório baixado: {arquivo.name}")

    log("Tratando relatório...")
    arquivo_tratado, base_tratada = tratar_relatorio_455(arquivo)

    log(f"Base tratada salva: {arquivo_tratado.name}")

    log("Aplicando filtros...")
    filtros = filtrar_pedidos_455(base_tratada)

    log("Enviando alertas por e-mail...")

    emails_enviados = False

    try:
        enviar_alertas_por_filial(filtros)
        emails_enviados = True
        log("Envio de alertas finalizado.")
    except Exception as exc:
        log(f"Falha no envio de e-mails: {exc}")
        log("OP101 não será processada porque os e-mails não foram enviados.")

    if emails_enviados:
        log("Processando instruções OP101...")
        processar_instrucoes_gestao(
            client=client,
            filtros=filtros,
            job=job,
            log_func=log_func,
        )
    else:
        log("Fluxo encerrado sem lançamento de instruções OP101.")

    log("Fluxo de gestão finalizado.")

    return arquivo