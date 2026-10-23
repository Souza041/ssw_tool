import argparse

from .database import conectar
from .executar_historico import executar


ENCERRADAS = {"concluida", "sem_dados"}


def consultar_tarefas():
    conexao = conectar()
    try:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS banco")
            if cursor.fetchone()["banco"] != "documentos_lg":
                raise RuntimeError("Banco conectado não é documentos_lg.")

            cursor.execute("""
                SELECT
                    id,
                    relatorio,
                    inicio_periodo,
                    fim_periodo,
                    status
                FROM lg_carga_periodos
                ORDER BY inicio_periodo, relatorio, id
            """)
            return cursor.fetchall()
    finally:
        conexao.close()


def processar(limite, aplicar=False, timeout=600):
    tarefas = [
        tarefa
        for tarefa in consultar_tarefas()
        if tarefa["status"] not in ENCERRADAS
    ]

    # Não passar por cima de uma execução interrompida ou com falha.
    bloqueadas = [
        tarefa for tarefa in tarefas
        if tarefa["status"] != "pendente"
    ]
    if bloqueadas:
        detalhes = ", ".join(
            f'{tarefa["id"]} ({tarefa["status"]})'
            for tarefa in bloqueadas
        )
        raise RuntimeError(
            f"Confira estas tarefas antes de continuar: {detalhes}. "
            "Nenhum relatório foi solicitado por este lote."
        )

    selecionadas = tarefas[:limite]

    print(f"Tarefas pendentes: {len(tarefas)}", flush=True)
    print(f"Tarefas selecionadas: {len(selecionadas)}", flush=True)

    for tarefa in selecionadas:
        print(
            f'{tarefa["id"]}: {tarefa["relatorio"]} | '
            f'{tarefa["inicio_periodo"]} a {tarefa["fim_periodo"]}',
            flush=True,
        )

    if not aplicar:
        print("Simulação: nenhuma alteração e nenhum acesso ao SSW.")
        return

    for posicao, tarefa in enumerate(selecionadas, start=1):
        tarefa_id = tarefa["id"]
        print(
            f"\n[{posicao}/{len(selecionadas)}] "
            f"Executando tarefa {tarefa_id}...",
            flush=True,
        )

        # Qualquer exceção interrompe o lote.
        # O executor individual registra o estado da tarefa.
        executar(tarefa_id, aplicar=True, timeout=timeout)

        atualizadas = {
            item["id"]: item for item in consultar_tarefas()
        }
        atual = atualizadas.get(tarefa_id)

        if atual is None or atual["status"] not in ENCERRADAS:
            raise RuntimeError(
                f"Tarefa {tarefa_id} sem encerramento confirmado. "
                "Lote interrompido."
            )

        print(
            f'Tarefa {tarefa_id}: {atual["status"]}.',
            flush=True,
        )

    print("\nLote finalizado.", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Executa períodos históricos LG em sequência."
    )
    parser.add_argument("--limite", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--executar", action="store_true")
    args = parser.parse_args()

    if args.limite <= 0:
        parser.error("--limite deve ser positivo.")
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout deve estar entre 30 e 1800 segundos.")

    processar(
        limite=args.limite,
        aplicar=args.executar,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
