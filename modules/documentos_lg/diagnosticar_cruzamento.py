import json

from .database import conectar


def diagnosticar():
    conexao = conectar()

    try:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS banco")
            if cursor.fetchone()["banco"] != "documentos_lg":
                raise RuntimeError("Banco conectado não é documentos_lg.")

            cursor.execute("""
                SELECT id
                FROM lg_importacoes
                WHERE relatorio = 'OP435'
                  AND status = 'concluida'
                ORDER BY id DESC
                LIMIT 1
            """)
            importacao = cursor.fetchone()

            if not importacao:
                raise RuntimeError("Nenhuma OP435 concluída encontrada.")

            importacao_id = importacao["id"]

            cursor.execute("""
                SELECT DISTINCT ctrc_chave
                FROM lg_registros_importados
                WHERE importacao_id = %s
                  AND ctrc_chave IS NOT NULL
                  AND ctrc_chave <> ''
            """, (importacao_id,))

            base = {linha["ctrc_chave"] for linha in cursor.fetchall()}
            encontrados = {"OP455": set(), "OP930": set()}
            chaves = sorted(base)

            for inicio in range(0, len(chaves), 300):
                lote = chaves[inicio:inicio + 300]
                marcadores = ",".join(["%s"] * len(lote))

                cursor.execute(f"""
                    SELECT DISTINCT
                        i.relatorio,
                        r.ctrc_chave
                    FROM lg_registros_importados AS r
                    INNER JOIN lg_importacoes AS i
                        ON i.id = r.importacao_id
                    WHERE i.status = 'concluida'
                      AND i.relatorio IN ('OP455', 'OP930')
                      AND r.ctrc_chave IN ({marcadores})
                """, tuple(lote))

                for linha in cursor.fetchall():
                    encontrados[linha["relatorio"]].add(
                        linha["ctrc_chave"]
                    )

        op455 = encontrados["OP455"]
        op930 = encontrados["OP930"]

        resultado = {
            "importacao_op435": importacao_id,
            "ctrcs_distintos_op435": len(base),
            "encontrados_op455": len(op455),
            "encontrados_op930": len(op930),
            "encontrados_em_ambos": len(op455 & op930),
            "sem_op455": len(base - op455),
            "sem_op930": len(base - op930),
            "sem_ambos": len(base - (op455 | op930)),
        }

        print(json.dumps(resultado, ensure_ascii=False, indent=2))

    finally:
        conexao.close()


if __name__ == "__main__":
    diagnosticar()
