from __future__ import annotations

from typing import Iterable

from database.automation import get_connection


class OP535Cache:
    """
    Cache persistente dos resultados obtidos pela BrasilAPI
    para os CNPJs processados pela OP535.
    """

    def buscar(
        self,
        cnpj: str,
    ) -> dict | None:
        connection = get_connection(
            autocommit=True
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        cnpj,
                        razao_social,
                        regime_tributario,
                        ano_regime,
                        optante_simples,
                        optante_mei,
                        status_consulta,
                        consultado_em,
                        atualizado_em
                    FROM op535_cnpj_cache
                    WHERE cnpj = %s
                    LIMIT 1
                    """,
                    (cnpj,),
                )

                return cursor.fetchone()

        finally:
            connection.close()

    def buscar_varios(
        self,
        cnpjs: Iterable[str],
        tamanho_lote: int = 1000,
    ) -> dict[str, dict]:
        """
        Busca CNPJs existentes no cache em lotes.

        Retorna:
            {
                "12345678000190": {...},
                "98765432000100": {...},
            }
        """

        lista = sorted({
            str(cnpj).strip()
            for cnpj in cnpjs
            if str(cnpj).strip()
        })

        if not lista:
            return {}

        encontrados: dict[str, dict] = {}

        connection = get_connection(
            autocommit=True
        )

        try:
            with connection.cursor() as cursor:

                for inicio in range(
                    0,
                    len(lista),
                    tamanho_lote,
                ):
                    lote = lista[
                        inicio:
                        inicio + tamanho_lote
                    ]

                    placeholders = ",".join(
                        ["%s"] * len(lote)
                    )

                    sql = f"""
                        SELECT
                            cnpj,
                            razao_social,
                            regime_tributario,
                            ano_regime,
                            optante_simples,
                            optante_mei,
                            status_consulta,
                            consultado_em,
                            atualizado_em
                        FROM op535_cnpj_cache
                        WHERE cnpj IN ({placeholders})
                    """

                    cursor.execute(
                        sql,
                        tuple(lote),
                    )

                    for registro in cursor.fetchall():
                        encontrados[
                            registro["cnpj"]
                        ] = registro

            return encontrados

        finally:
            connection.close()

    def salvar(
        self,
        *,
        cnpj: str,
        regime_tributario: str | None,
        razao_social: str | None = None,
        ano_regime: int | None = None,
        optante_simples: bool | None = None,
        optante_mei: bool | None = None,
        status_consulta: str = "OK",
    ) -> None:
        """
        Insere ou atualiza imediatamente um CNPJ.

        O UPSERT permite executar novamente a OP535
        sem gerar registros duplicados.
        """

        connection = get_connection(
            autocommit=True
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO op535_cnpj_cache (
                        cnpj,
                        razao_social,
                        regime_tributario,
                        ano_regime,
                        optante_simples,
                        optante_mei,
                        status_consulta,
                        consultado_em
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        NOW()
                    )

                    ON DUPLICATE KEY UPDATE
                        razao_social =
                            VALUES(razao_social),

                        regime_tributario =
                            VALUES(regime_tributario),

                        ano_regime =
                            VALUES(ano_regime),

                        optante_simples =
                            VALUES(optante_simples),

                        optante_mei =
                            VALUES(optante_mei),

                        status_consulta =
                            VALUES(status_consulta),

                        consultado_em =
                            VALUES(consultado_em)
                    """,
                    (
                        cnpj,
                        razao_social,
                        regime_tributario,
                        ano_regime,
                        optante_simples,
                        optante_mei,
                        status_consulta,
                    ),
                )

        finally:
            connection.close()

    def salvar_nao_encontrado(
        self,
        cnpj: str,
    ) -> None:
        self.salvar(
            cnpj=cnpj,
            regime_tributario=None,
            status_consulta="NAO_ENCONTRADO",
        )

    def contar(self) -> int:
        connection = get_connection(
            autocommit=True
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM op535_cnpj_cache
                    """
                )

                row = cursor.fetchone()

                return int(
                    row["total"] or 0
                )

        finally:
            connection.close()