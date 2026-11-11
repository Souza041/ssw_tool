
"""Consulta unificada de incidentes — somente leitura."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from database.automation import get_connection


class ConsultaInvalida(ValueError):
    pass


def _data_json(valor: Any) -> Any:
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def _texto(valor: Any) -> str:
    return str(valor).strip() if valor is not None else ""


def _campo(payload: dict, *nomes: str) -> Any:
    for nome in nomes:
        valor = payload.get(nome)
        if valor is not None and str(valor).strip():
            return valor
    return None


def _evento(registro: dict, payload: dict) -> dict:
    """Identidade provisória de um registro operacional."""
    return {
        "ctrc": registro["ctrc"],
        "nota_fiscal": registro["nota_fiscal"],
        "codigo": registro["codigo_ocorrencia"],
        "data": _data_json(registro["data_ocorrencia"]),
        "hora": _campo(payload, "HORA_OCOR"),
        "hora_inclusao": _campo(
            payload, "HORA_INCLUSAO_OCOR"
        ),
        "descricao": _campo(
            payload,
            "DESCRICAO_OCOR",
            "AN_DESCRICAO_OCORRENCIA",
        ),
        "complemento": _campo(
            payload, "COMPLEMENTO_OCOR"
        ),
        "usuario": _campo(
            payload, "USUARIO_OCOR"
        ),
    }


def _historico_op101(payload: dict) -> list[dict]:
    historico = []

    for numero in range(1, 6):
        prefixo = f"OP101_OC_{numero}_"

        codigo = payload.get(prefixo + "CODIGO")
        if codigo is None or str(codigo).strip() == "":
            continue

        historico.append({
            "posicao": numero,
            "codigo": codigo,
            "data_hora": payload.get(
                prefixo + "DATA_HORA"
            ),
            "descricao": payload.get(
                prefixo + "DESCRICAO"
            ),
            "complemento": payload.get(
                prefixo + "COMPLEMENTO"
            ),
            "usuario": payload.get(
                prefixo + "USUARIO"
            ),
        })

    return historico


def consultar_incidentes(
    termo: str,
    *,
    tipo: str = "auto",
    limite: int = 100,
) -> dict:
    """
    Pesquisa a importação ativa por CTRC ou NF.

    Não altera o banco e não consulta o SSW em tempo real.
    """
    termo = _texto(termo)
    tipo = _texto(tipo).lower()

    if not termo or len(termo) > 80:
        raise ConsultaInvalida(
            "Informe um CTRC ou NF com até 80 caracteres."
        )

    if tipo not in ("auto", "ctrc", "nf"):
        raise ConsultaInvalida(
            "Tipo deve ser auto, ctrc ou nf."
        )

    if isinstance(limite, bool) or not 1 <= limite <= 500:
        raise ConsultaInvalida(
            "Limite deve estar entre 1 e 500."
        )

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    i.id,
                    i.origem_arquivo,
                    i.iniciada_em,
                    i.finalizada_em,
                    i.publicada_em,
                    i.total_registros
                FROM incidentes_estado e
                JOIN incidentes_importacoes i
                  ON i.id = e.importacao_ativa_id
                WHERE e.chave = %s
                  AND i.status = %s
                """,
                ("atual", "PUBLICADA"),
            )

            importacao = cur.fetchone()

            if not importacao:
                raise ConsultaInvalida(
                    "Não existe importação ativa publicada."
                )

            # Igualdade exata: evita pesquisas amplas
            # e aproveita os índices existentes.
            if tipo == "ctrc":
                filtro = "r.ctrc = %s"
                parametros = [termo]
            elif tipo == "nf":
                filtro = "r.nota_fiscal = %s"
                parametros = [termo]
            else:
                filtro = (
                    "(r.ctrc = %s OR r.nota_fiscal = %s)"
                )
                parametros = [termo, termo]

            sql = f"""
                SELECT
                    r.id,
                    r.linha_origem,
                    r.ctrc,
                    r.nota_fiscal,
                    r.codigo_ocorrencia,
                    r.regra_debito,
                    r.status_debito,
                    r.unidade,
                    r.grupo_cliente,
                    r.data_ocorrencia,
                    r.payload_json
                FROM incidentes_registros r
                WHERE r.importacao_id = %s
                  AND {filtro}
                ORDER BY r.id
                LIMIT %s
            """

            parametros = [
                importacao["id"],
                *parametros,
                limite + 1,
            ]

            cur.execute(sql, tuple(parametros))
            encontrados = cur.fetchall()

        truncado = len(encontrados) > limite
        registros = encontrados[:limite]

        grupos = {}
        documentos = {}
        historicos = {}

        for registro in registros:
            payload = json.loads(
                registro["payload_json"]
            )

            if not isinstance(payload, dict):
                raise ConsultaInvalida(
                    "Payload de incidente inválido."
                )

            evento = _evento(registro, payload)

            # Agrupamento de apresentação.
            # Horários distintos permanecem separados.
            chave = json.dumps(
                evento,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )

            if chave not in grupos:
                grupos[chave] = {
                    "evento": evento,
                    "regra_debito": registro["regra_debito"],
                    "status_debito": registro["status_debito"],
                    "motivo_debito": _campo(
                        payload,
                        "MOTIVO_VALIDACAO_DEBITO",
                    ),
                    "quantidade_linhas": 0,
                    "ids_registros": [],
                    "linhas_origem": [],
                    "status_parser_xml": [],
                }

            grupo = grupos[chave]
            grupo["quantidade_linhas"] += 1
            grupo["ids_registros"].append(
                registro["id"]
            )
            grupo["linhas_origem"].append(
                registro["linha_origem"]
            )

            parser = payload.get("STATUS_PARSER_XML")
            if (
                parser is not None
                and parser not in grupo["status_parser_xml"]
            ):
                grupo["status_parser_xml"].append(parser)

            ctrc = _texto(registro["ctrc"])
            nf = _texto(registro["nota_fiscal"])
            chave_documento = (ctrc, nf)

            if chave_documento not in documentos:
                documentos[chave_documento] = {
                    "ctrc": ctrc,
                    "nota_fiscal": nf,
                    "cte": _campo(
                        payload, "NRO_CTE", "NUMERO_CTE"
                    ),
                    "chave_cte": payload.get("CHAVE_CTE"),
                    "cliente": _campo(
                        payload,
                        "AN_CLIENTE",
                        "NOME_PAGADOR",
                    ),
                    "grupo_cliente": registro[
                        "grupo_cliente"
                    ],
                    "unidade": registro["unidade"],
                    "tipo_operacao": payload.get(
                        "TIPO_OPERACAO"
                    ),
                    "valor_carga_cte": payload.get(
                        "VALOR_CARGA_CTE"
                    ),
                    "data_emissao_cte": payload.get(
                        "DATA_EMISSAO_CTE"
                    ),
                }

            # O OP101 é um histórico parcial embutido
            # em cada snapshot, não uma tabela de eventos.
            for item in _historico_op101(payload):
                identidade = (
                    ctrc,
                    _texto(item["codigo"]),
                    _texto(item["data_hora"]),
                    _texto(item["descricao"]),
                    _texto(item["complemento"]),
                    _texto(item["usuario"]),
                )

                if identidade not in historicos:
                    historicos[identidade] = {
                        "ctrc": ctrc,
                        **item,
                    }

        return {
            "consulta": {
                "termo": termo,
                "tipo": tipo,
                "fonte": "snapshot_mysql",
                "tempo_real_ssw": False,
                "limite": limite,
                "truncado": truncado,
            },
            "importacao": {
                "id": importacao["id"],
                "arquivo": importacao["origem_arquivo"],
                "iniciada_em": _data_json(
                    importacao["iniciada_em"]
                ),
                "finalizada_em": _data_json(
                    importacao["finalizada_em"]
                ),
                "publicada_em": _data_json(
                    importacao["publicada_em"]
                ),
                "total_registros": importacao[
                    "total_registros"
                ],
            },
            "resumo": {
                "linhas_encontradas": len(registros),
                "documentos_encontrados": len(documentos),
                "grupos_operacionais": len(grupos),
                "registros_op101_exibidos": len(
                    historicos
                ),
            },
            "documentos": list(documentos.values()),
            "ocorrencias": list(grupos.values()),
            "historico_op101": list(historicos.values()),
        }

    finally:
        conn.close()
