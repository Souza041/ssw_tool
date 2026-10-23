import hashlib
import json
import uuid
from datetime import date
from pathlib import Path

from .database import conectar
from .leitores import ler_registros


VERSAO_LEITOR = "lg-1"


def json_texto(valor):
    return json.dumps(
        valor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha_arquivo(path):
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for bloco in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(bloco)

    return digest.hexdigest()


def importar(relatorio, arquivo, inicio, fim, *, tipo="manual"):
    relatorio = relatorio.upper()

    if tipo not in ("manual", "historica", "incremental"):
        raise ValueError("Tipo de execução inválido.")

    if relatorio not in ("OP435", "OP455", "OP930"):
        raise ValueError("Relatório inválido.")

    inicio = date.fromisoformat(inicio)
    fim = date.fromisoformat(fim)

    if inicio > fim:
        raise ValueError(
            "O início deve ser anterior ou igual ao fim."
        )

    arquivo = Path(arquivo).resolve(strict=True)

    if len(arquivo.name) > 255:
        raise ValueError("Nome de arquivo muito longo.")

    digest = sha_arquivo(arquivo)

    trava = hashlib.sha256(
        f"lg:{relatorio}:{digest}".encode()
    ).hexdigest()

    conexao = conectar()
    adquirida = False
    execucao_id = None
    importacao_id = None

    try:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS banco")

            if cursor.fetchone()["banco"] != "documentos_lg":
                raise RuntimeError(
                    "O banco conectado não é documentos_lg."
                )

            cursor.execute(
                "SELECT GET_LOCK(%s, 0) AS adquirida",
                (trava,),
            )

            adquirida = (
                cursor.fetchone()["adquirida"] == 1
            )

            if not adquirida:
                raise RuntimeError(
                    "Não foi possível obter a trava "
                    "de importação deste arquivo."
                )

            cursor.execute(
                """
                SELECT id, registros_gravados
                FROM lg_importacoes
                WHERE relatorio = %s
                  AND arquivo_sha256 = %s
                  AND versao_leitor = %s
                  AND status = 'concluida'
                ORDER BY id DESC
                LIMIT 1
                """,
                (relatorio, digest, VERSAO_LEITOR),
            )

            anterior = cursor.fetchone()

            if anterior:
                return {
                    "status": "ja_importado",
                    "importacao_id": anterior["id"],
                    "registros": anterior["registros_gravados"],
                }

            cursor.execute(
                """
                INSERT INTO lg_execucoes (
                    identificador,
                    tipo,
                    status,
                    inicio_periodo,
                    fim_periodo,
                    iniciada_em
                )
                VALUES (
                    %s,
                    %s,
                    'executando',
                    %s,
                    %s,
                    UTC_TIMESTAMP()
                )
                """,
                (str(uuid.uuid4()), tipo, inicio, fim),
            )

            execucao_id = cursor.lastrowid

            filtros = {
                "origem": "arquivo_local",
                "periodo": "declarado_pelo_operador",
                "pagador_filtrado": relatorio == "OP435",
            }

            cursor.execute(
                """
                INSERT INTO lg_importacoes (
                    execucao_id,
                    relatorio,
                    status,
                    inicio_periodo,
                    fim_periodo,
                    nome_arquivo,
                    arquivo_sha256,
                    tamanho_bytes,
                    filtros_json,
                    versao_leitor,
                    iniciada_em
                )
                VALUES (
                    %s, %s, 'importando', %s, %s,
                    %s, %s, %s, %s, %s, UTC_TIMESTAMP()
                )
                """,
                (
                    execucao_id,
                    relatorio,
                    inicio,
                    fim,
                    arquivo.name,
                    digest,
                    arquivo.stat().st_size,
                    json_texto(filtros),
                    VERSAO_LEITOR,
                ),
            )

            importacao_id = cursor.lastrowid

        # Preserva o registro da tentativa antes de importar.
        conexao.commit()

        sql = """
            INSERT INTO lg_registros_importados (
                importacao_id,
                numero_linha,
                registro_sha256,
                ctrc_chave,
                ctrc_original,
                cte_serie,
                cte_numero,
                dados_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """

        total = 0

        with conexao.cursor() as cursor:
            # Evita montar INSERTs muito grandes.
            cursor.max_stmt_length = 128 * 1024
            lote = []

            for item in ler_registros(relatorio, arquivo):
                dados = json_texto(item["dados"])

                lote.append((
                    importacao_id,
                    item["numero_linha"],
                    hashlib.sha256(
                        dados.encode("utf-8")
                    ).hexdigest(),
                    item["ctrc_chave"],
                    item["ctrc_original"],
                    item["cte_serie"],
                    item["cte_numero"],
                    dados,
                ))

                total += 1

                if len(lote) >= 50:
                    cursor.executemany(sql, lote)
                    lote.clear()

            if lote:
                cursor.executemany(sql, lote)

            if total == 0:
                raise ValueError(
                    "Arquivo sem registros. Confira a extração."
                )

            if sha_arquivo(arquivo) != digest:
                raise RuntimeError(
                    "O arquivo mudou durante a importação."
                )

            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM lg_registros_importados
                WHERE importacao_id = %s
                """,
                (importacao_id,),
            )

            if cursor.fetchone()["total"] != total:
                raise RuntimeError(
                    "A contagem gravada diverge da leitura."
                )

            cursor.execute(
                """
                UPDATE lg_importacoes
                SET status = 'concluida',
                    registros_lidos = %s,
                    registros_gravados = %s,
                    finalizada_em = UTC_TIMESTAMP()
                WHERE id = %s
                """,
                (total, total, importacao_id),
            )

            cursor.execute(
                """
                UPDATE lg_execucoes
                SET status = 'concluida',
                    finalizada_em = UTC_TIMESTAMP()
                WHERE id = %s
                """,
                (execucao_id,),
            )

        # Linhas e conclusão são confirmadas juntas.
        conexao.commit()

        return {
            "status": "concluida",
            "importacao_id": importacao_id,
            "registros": total,
        }

    except Exception:
        try:
            conexao.rollback()

            if importacao_id is not None:
                with conexao.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE lg_importacoes
                        SET status = 'falhou',
                            erro = %s,
                            finalizada_em = UTC_TIMESTAMP()
                        WHERE id = %s
                          AND status = 'importando'
                        """,
                        (
                            "Importação interrompida. "
                            "Verificar a execução no terminal.",
                            importacao_id,
                        ),
                    )

                    cursor.execute(
                        """
                        UPDATE lg_execucoes
                        SET status = 'falhou',
                            erro = %s,
                            finalizada_em = UTC_TIMESTAMP()
                        WHERE id = %s
                          AND status = 'executando'
                        """,
                        (
                            "Importação interrompida.",
                            execucao_id,
                        ),
                    )

                conexao.commit()

        except Exception:
            # Se a conexão caiu, pode não ser possível
            # registrar a falha. Não esconder o erro original.
            pass

        raise

    finally:
        try:
            if adquirida:
                with conexao.cursor() as cursor:
                    cursor.execute(
                        "SELECT RELEASE_LOCK(%s)",
                        (trava,),
                    )
        except Exception:
            pass

        conexao.close()
