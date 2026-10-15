"""Diagnóstico somente leitura do esquema OC73.

Não executa reservas, migrations ou comandos DML/DDL. O teste opcional de
advisory lock exige confirmação explícita e é recusado no banco automationx.
"""

import argparse
import os
import re
import sys
from uuid import uuid4

import pymysql
from pymysql.cursors import DictCursor


TABELAS_OC73 = ("oc73_execucoes", "oc73_tentativas")
COMANDO_PERMITIDO = re.compile(r"^\s*(SELECT|SHOW)\b", re.IGNORECASE)
COMANDO_PROIBIDO = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE|ALTER|CREATE|DROP|TRUNCATE|"
    r"GRANT|REVOKE|CALL|LOAD|INTO\s+OUTFILE|INTO\s+DUMPFILE)\b",
    re.IGNORECASE,
)


def validar_sql_somente_leitura(sql: str) -> None:
    normalizado = " ".join(str(sql).split())
    if not COMANDO_PERMITIDO.match(normalizado):
        raise ValueError("Diagnóstico aceita somente SELECT e SHOW.")
    if ";" in normalizado or COMANDO_PROIBIDO.search(normalizado):
        raise ValueError("Comando potencialmente mutável recusado.")


def executar(cursor, sql: str, parametros=()):
    validar_sql_somente_leitura(sql)
    cursor.execute(sql, parametros)
    return cursor.fetchall()


def conectar():
    obrigatorias = {
        "host": os.getenv("OC73_DIAGNOSTICO_DB_HOST"),
        "database": os.getenv("OC73_DIAGNOSTICO_DB_NAME"),
        "user": os.getenv("OC73_DIAGNOSTICO_DB_USER"),
        "password": os.getenv("OC73_DIAGNOSTICO_DB_PASSWORD"),
    }
    faltantes = [nome for nome, valor in obrigatorias.items() if not valor]
    if faltantes:
        raise RuntimeError(
            "Configuração MySQL incompleta: " + ", ".join(faltantes)
        )
    return pymysql.connect(
        host=obrigatorias["host"],
        port=int(os.getenv("OC73_DIAGNOSTICO_DB_PORT", "3306")),
        user=obrigatorias["user"],
        password=obrigatorias["password"],
        database=obrigatorias["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=10,
        read_timeout=15,
        write_timeout=15,
    )


def validar_laboratorio_lock(identidade: dict) -> None:
    destino = {
        "host": os.getenv("OC73_DIAGNOSTICO_DB_HOST", ""),
        "port": os.getenv("OC73_DIAGNOSTICO_DB_PORT", ""),
        "database": os.getenv("OC73_DIAGNOSTICO_DB_NAME", ""),
        "user": os.getenv("OC73_DIAGNOSTICO_DB_USER", ""),
    }
    esperado = {
        "host": "127.0.0.1",
        "port": "3308",
        "database": "oc73_concorrencia_test",
        "user": "oc73_tester",
    }
    diretorio = str(identidade.get("diretorio", "")).replace(
        "\\", "/"
    ).lower()
    if destino != esperado or not (
        int(identidade.get("porta", 0)) == 3308
        and identidade.get("banco") == "oc73_concorrencia_test"
        and identidade.get("usuario") == "oc73_tester@127.0.0.1"
        and "/oc73-lab/data" in diretorio
    ):
        raise RuntimeError(
            "Teste de advisory lock permitido somente no laboratório OC73 "
            "validado em 127.0.0.1:3308."
        )


def diagnosticar(testar_lock: bool = False) -> dict:
    conexao = conectar()
    resultado = {"identidade": {}, "tabelas": {}, "duplicidades": []}
    try:
        with conexao.cursor() as cursor:
            identidade = executar(
                cursor,
                "SELECT VERSION() AS versao, DATABASE() AS banco, "
                "CURRENT_USER() AS usuario, @@port AS porta, "
                "@@datadir AS diretorio",
            )[0]
            resultado["identidade"] = identidade

            colunas = executar(
                cursor,
                "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, "
                "COLUMN_KEY FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME IN (%s, %s) "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION",
                TABELAS_OC73,
            )
            indices = executar(
                cursor,
                "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, "
                "COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME IN (%s, %s) "
                "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
                TABELAS_OC73,
            )
            privilegios = executar(cursor, "SHOW GRANTS FOR CURRENT_USER")
            resultado["privilegios"] = privilegios
            for tabela in TABELAS_OC73:
                resultado["tabelas"][tabela] = {
                    "colunas": [
                        item for item in colunas
                        if item["TABLE_NAME"] == tabela
                    ],
                    "indices": [
                        item for item in indices
                        if item["TABLE_NAME"] == tabela
                    ],
                }

            resultado["duplicidades"] = executar(
                cursor,
                "SELECT data_referencia, triggered_by, COUNT(*) AS total "
                "FROM oc73_execucoes "
                "WHERE triggered_by IN (%s, %s) "
                "GROUP BY data_referencia, triggered_by "
                "HAVING COUNT(*) > 1 "
                "ORDER BY data_referencia DESC LIMIT 100",
                ("scheduler:CWB", "scheduler:BIG"),
            )

            nome_lock = f"ssw_tool:oc73:diagnostico:{uuid4().hex}"
            livre = executar(
                cursor,
                "SELECT IS_FREE_LOCK(%s) AS disponivel",
                (nome_lock,),
            )[0]
            resultado["advisory_lock"] = {
                "inspecao": livre,
                "teste_executado": False,
            }

            if testar_lock:
                validar_laboratorio_lock(identidade)
                adquirido = executar(
                    cursor,
                    "SELECT GET_LOCK(%s, 0) AS adquirido",
                    (nome_lock,),
                )[0]
                try:
                    resultado["advisory_lock"].update({
                        "teste_executado": True,
                        "aquisicao": adquirido,
                    })
                finally:
                    liberado = executar(
                        cursor,
                        "SELECT RELEASE_LOCK(%s) AS liberado",
                        (nome_lock,),
                    )[0]
                    resultado["advisory_lock"]["liberacao"] = liberado
    finally:
        conexao.close()
    return resultado


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico somente leitura das tabelas OC73.",
    )
    parser.add_argument(
        "--testar-advisory-lock",
        action="store_true",
        help="Adquire/libera lock efêmero somente no laboratório validado.",
    )
    parser.add_argument(
        "--confirmar-ambiente-controlado",
        action="store_true",
        help="Confirma que o teste de lock ocorrerá em ambiente controlado.",
    )
    args = parser.parse_args(argv)
    if args.testar_advisory_lock and not args.confirmar_ambiente_controlado:
        parser.error(
            "--testar-advisory-lock exige --confirmar-ambiente-controlado"
        )

    resultado = diagnosticar(testar_lock=args.testar_advisory_lock)
    identidade = resultado["identidade"]
    print("OC73 diagnóstico somente leitura")
    print(f"Versão: {identidade['versao']}")
    print(f"Banco: {identidade['banco']}")
    print(f"Usuário efetivo: {identidade['usuario']}")
    print(f"Porta: {identidade['porta']}")
    print(f"Diretório: {identidade['diretorio']}")
    print(
        "Privilégios efetivos: "
        f"{len(resultado.get('privilegios', []))} linha(s); revisar se são "
        "estritamente de leitura."
    )
    for tabela, dados in resultado["tabelas"].items():
        print(
            f"{tabela}: {len(dados['colunas'])} coluna(s), "
            f"{len(dados['indices'])} entrada(s) de índice"
        )
    print(
        "Duplicidades scheduler fluxo/data: "
        f"{len(resultado['duplicidades'])}"
    )
    print(f"Advisory lock: {resultado['advisory_lock']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
