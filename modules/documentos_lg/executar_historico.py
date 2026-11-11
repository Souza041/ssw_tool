import argparse
import hashlib
import json
import uuid
from pathlib import Path

from ssw.client import SSWClient
from operations.op455.report import OP455Report
from operations.op930.report import OP930Report
from operations.op156.queue import RelatorioSemDados

from .database import conectar
from .importador import sha_arquivo
from .retry_importacao import importar_com_retry as importar
from .op435 import BASE, Extrator
from .planejar_historico import PERFIS
from .service import ClientLG, validar_download


ROOT = Path(__file__).resolve().parents[2]


def carregar(cursor, tarefa_id):
    cursor.execute(
        """
        SELECT *
        FROM lg_carga_periodos
        WHERE id = %s
        FOR UPDATE
        """,
        (tarefa_id,),
    )

    tarefa = cursor.fetchone()

    if not tarefa:
        raise ValueError("Tarefa não encontrada.")

    perfil = PERFIS.get(tarefa["relatorio"])

    texto = json.dumps(
        perfil,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    digest = hashlib.sha256(
        texto.encode("utf-8")
    ).hexdigest()

    if (
        perfil is None
        or json.loads(tarefa["perfil_json"]) != perfil
        or digest != tarefa["perfil_sha256"]
    ):
        raise ValueError(
            "O perfil da tarefa difere do planejador atual."
        )

    if tarefa["inicio_periodo"] > tarefa["fim_periodo"]:
        raise ValueError("Período inválido.")

    return tarefa


def salvar_recibo(pasta, tarefa, arquivo):
    arquivo = validar_download(arquivo).resolve()

    if arquivo.parent != pasta.resolve():
        raise ValueError(
            "Download fora da pasta desta tarefa."
        )

    dados = {
        "tarefa_id": tarefa["id"],
        "perfil_sha256": tarefa["perfil_sha256"],
        "inicio": tarefa["inicio_periodo"].isoformat(),
        "fim": tarefa["fim_periodo"].isoformat(),
        "arquivo": arquivo.name,
        "sha256": sha_arquivo(arquivo),
    }

    temporario = pasta / "recibo.part"

    temporario.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    temporario.replace(pasta / "recibo.json")
    return arquivo


def recuperar(pasta, tarefa):
    dados = json.loads(
        (pasta / "recibo.json").read_text(
            encoding="utf-8"
        )
    )

    esperado = (
        tarefa["id"],
        tarefa["perfil_sha256"],
        tarefa["inicio_periodo"].isoformat(),
        tarefa["fim_periodo"].isoformat(),
    )

    encontrado = (
        dados["tarefa_id"],
        dados["perfil_sha256"],
        dados["inicio"],
        dados["fim"],
    )

    arquivo = (pasta / dados["arquivo"]).resolve()

    if (
        encontrado != esperado
        or arquivo.parent != pasta.resolve()
        or not arquivo.is_file()
    ):
        raise ValueError(
            "Recibo incompatível com a tarefa."
        )

    if sha_arquivo(arquivo) != dados["sha256"]:
        raise ValueError(
            "O arquivo salvo difere do recibo."
        )

    return validar_download(arquivo)


def finalizar(
    tarefa_id,
    token,
    status,
    importacao_id=None,
    erro=None,
):
    conexao = conectar()

    try:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS banco")

            if cursor.fetchone()["banco"] != "documentos_lg":
                raise RuntimeError(
                    "Banco conectado não é documentos_lg."
                )

            cursor.execute(
                """
                UPDATE lg_carga_periodos
                SET status = %s,
                    importacao_id = %s,
                    erro = %s,
                    finalizada_em = UTC_TIMESTAMP()
                WHERE id = %s
                  AND reserva_token = %s
                  AND status = 'executando'
                """,
                (
                    status,
                    importacao_id,
                    erro,
                    tarefa_id,
                    token,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "A reserva da tarefa mudou; "
                    "conferir o banco."
                )

        conexao.commit()

    except Exception:
        conexao.rollback()
        raise

    finally:
        conexao.close()


def executar(
    tarefa_id,
    aplicar=False,
    retomar=False,
    timeout=600,
    tipo="historica",
):
    if tipo not in {"historica", "incremental"}:
        raise ValueError("Tipo de execução inválido.")
    if not 30 <= timeout <= 1800:
        raise ValueError(
            "Timeout deve estar entre 30 e 1800 segundos."
        )

    conexao = conectar()
    token = str(uuid.uuid4())
    reservado = False
    pedido = False
    fase = "preparacao"
    client = None

    try:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS banco")

            if cursor.fetchone()["banco"] != "documentos_lg":
                raise RuntimeError(
                    "Banco conectado não é documentos_lg."
                )

            tarefa = carregar(cursor, tarefa_id)

            print(
                f"Tarefa {tarefa_id}: "
                f"{tarefa['relatorio']} | "
                f"{tarefa['inicio_periodo']} a "
                f"{tarefa['fim_periodo']} | "
                f"{tarefa['status']}",
                flush=True,
            )

            if not aplicar:
                conexao.rollback()
                print(
                    "Simulação: nenhuma alteração "
                    "e nenhum acesso ao SSW."
                )
                return

            pasta = (
                ROOT
                / "data"
                / "documentos_lg"
                / "historico"
                / str(tarefa_id)
            )

            if retomar:
                if tarefa["status"] not in (
                    "falhou",
                    "incerta",
                ):
                    raise ValueError(
                        "Retomada local exige tarefa "
                        "falhou ou incerta."
                    )

                arquivo = recuperar(pasta, tarefa)

            else:
                if tarefa["status"] != "pendente":
                    raise ValueError(
                        "Nova extração exige tarefa pendente."
                    )

                if pasta.exists():
                    raise ValueError(
                        "A pasta desta tarefa já existe. "
                        "Conferir antes de solicitar novamente."
                    )

            cursor.execute(
                """
                UPDATE lg_carga_periodos
                SET status = 'executando',
                    reserva_token = %s,
                    tentativas = tentativas + 1,
                    iniciada_em = UTC_TIMESTAMP(),
                    finalizada_em = NULL,
                    erro = NULL
                WHERE id = %s
                """,
                (token, tarefa_id),
            )

        conexao.commit()
        reservado = True
        conexao.close()
        conexao = None

        if not retomar:
            pasta.mkdir(parents=True, exist_ok=False)

            client = SSWClient()
            client.timeout = timeout

            if client.base_url.rstrip("/") != BASE:
                raise ValueError(
                    "Servidor SSW diferente do validado."
                )

            if not all((
                client.dominio,
                client.cpf,
                client.usuario,
                client.senha,
            )):
                raise ValueError(
                    "Preencha as credenciais SSW no .env."
                )

            print("Autenticando no SSW...", flush=True)

            Extrator(
                session=client.session,
                timeout=timeout,
            ).login(
                client.dominio,
                client.cpf,
                client.usuario,
                client.senha,
            )

            adapter = ClientLG(client)

            inicio = tarefa["inicio_periodo"].strftime(
                "%d%m%y"
            )
            fim = tarefa["fim_periodo"].strftime(
                "%d%m%y"
            )

            fase = "extracao"
            pedido = True

            print(
                "Iniciando a extração do período...",
                flush=True,
            )

            if tarefa["relatorio"] == "OP455":
                arquivo = OP455Report(
                    adapter
                ).gerar_e_baixar_documentos(
                    output_dir=pasta,
                    data_inicial=inicio,
                    data_final=fim,
                    timeout_seconds=timeout,
                )

            else:
                perfil = PERFIS["OP930"]

                adapter.open_option(
                    "930",
                    perfil["unidade"],
                )

                arquivo = OP930Report(
                    adapter
                ).gerar_e_baixar_por_cnpj(
                    output_dir=pasta,
                    data_inicial=inicio,
                    data_final=fim,
                    cnpj=perfil["cnpj"],
                    grupo=perfil["grupo"],
                    timeout_seconds=timeout,
                )

            arquivo = salvar_recibo(
                pasta,
                tarefa,
                arquivo,
            )

        fase = "importacao"

        print(
            "Importando o arquivo no MySQL...",
            flush=True,
        )

        resultado = importar(
            tarefa["relatorio"],
            arquivo,
            tarefa["inicio_periodo"].isoformat(),
            tarefa["fim_periodo"].isoformat(),
            tipo=tipo,
        )

        finalizar(
            tarefa_id,
            token,
            "concluida",
            resultado["importacao_id"],
        )

        reservado = False

        print(
            json.dumps(resultado, ensure_ascii=False),
            flush=True,
        )

    except RelatorioSemDados:
        if not reservado or fase != "extracao":
            raise

        finalizar(
            tarefa_id,
            token,
            "sem_dados",
            erro=(
                "SSW informou nenhum registro "
                "para a solicitação."
            ),
        )

        reservado = False

        print(
            "Período encerrado: "
            "SSW informou nenhum registro."
        )

    except (Exception, KeyboardInterrupt) as exc:
        if conexao is not None:
            conexao.rollback()

        if reservado:
            status = (
                "incerta"
                if pedido and fase == "extracao"
                else "falhou"
            )

            try:
                finalizar(
                    tarefa_id,
                    token,
                    status,
                    erro=(
                        f"Etapa {fase}; "
                        f"{type(exc).__name__}. "
                        "Conferir terminal e arquivos."
                    ),
                )

            except Exception:
                print(
                    "Não foi possível registrar a falha; "
                    "confira o estado da tarefa no banco."
                )

        raise

    finally:
        if client is not None:
            client.session.close()

        if conexao is not None:
            conexao.close()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Executa um único período histórico LG."
        )
    )

    parser.add_argument(
        "--tarefa",
        required=True,
        type=int,
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
    )
    parser.add_argument(
        "--executar",
        action="store_true",
    )
    parser.add_argument(
        "--retomar-importacao",
        action="store_true",
    )

    args = parser.parse_args()

    if args.tarefa <= 0:
        parser.error("Informe um ID positivo.")

    executar(
        args.tarefa,
        args.executar,
        args.retomar_importacao,
        args.timeout,
    )


if __name__ == "__main__":
    main()
