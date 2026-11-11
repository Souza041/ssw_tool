
"""Historico versionado de incidentes — somente leitura."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from database.automation import get_connection


class HistoricoInvalido(ValueError):
    """Parametros invalidos ou historico indisponivel."""


def _texto(valor: Any) -> str:
    return str(valor).strip() if valor is not None else ""


def _data(valor: Any) -> Any:
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def _payload(valor: Any) -> dict:
    if isinstance(valor, dict):
        return valor

    if isinstance(valor, (str, bytes)):
        resultado = json.loads(valor)
        if isinstance(resultado, dict):
            return resultado

    raise HistoricoInvalido(
        "Registro possui payload_json invalido."
    )


def _primeiro(payload: dict, *campos: str) -> Any:
    for campo in campos:
        valor = payload.get(campo)
        if valor is not None and _texto(valor):
            return valor
    return None


def _identidade_operacional(
    registro: dict,
    payload: dict,
) -> tuple:
    """
    Chave provisoria para agrupamento visual.

    Nao representa um identificador oficial do SSW.
    """
    return (
        _texto(registro["ctrc"]),
        _texto(registro["nota_fiscal"]),
        _texto(registro["codigo_ocorrencia"]),
        _texto(_data(registro["data_ocorrencia"])),
        _texto(payload.get("HORA_OCOR")),
        _texto(payload.get("HORA_INCLUSAO_OCOR")),
        _texto(payload.get("USUARIO_OCOR")),
        _texto(
            _primeiro(
                payload,
                "DESCRICAO_OCOR",
                "AN_DESCRICAO_OCORRENCIA",
            )
        ),
        _texto(payload.get("COMPLEMENTO_OCOR")),
    )


def _resumir_versao(
    importacao: dict,
    registros: list[dict],
) -> dict:
    grupos = defaultdict(list)
    documentos = set()
    regras = set()
    status = set()
    codigos = set()
    eventos = []

    for registro in registros:
        payload = _payload(registro["payload_json"])

        chave = _identidade_operacional(
            registro,
            payload,
        )

        grupos[chave].append(registro["id"])

        documentos.add((
            _texto(registro["ctrc"]),
            _texto(registro["nota_fiscal"]),
        ))

        if registro["regra_debito"]:
            regras.add(_texto(registro["regra_debito"]))

        if registro["status_debito"]:
            status.add(_texto(registro["status_debito"]))

        if registro["codigo_ocorrencia"]:
            codigos.add(
                _texto(registro["codigo_ocorrencia"])
            )

    for chave, ids in grupos.items():
        eventos.append({
            "ctrc": chave[0],
            "nota_fiscal": chave[1],
            "codigo_ocorrencia": chave[2],
            "data_ocorrencia": chave[3],
            "hora_ocorrencia": chave[4],
            "hora_inclusao": chave[5],
            "usuario": chave[6],
            "descricao": chave[7],
            "complemento": chave[8],
            "quantidade_linhas": len(ids),
            "ids_registros": ids,
        })

    eventos.sort(
        key=lambda e: (
            e["ctrc"],
            e["nota_fiscal"],
            e["data_ocorrencia"],
            e["hora_ocorrencia"],
            e["codigo_ocorrencia"],
        )
    )

    return {
        "importacao_id": importacao["id"],
        "arquivo": importacao["origem_arquivo"],
        "iniciada_em": _data(importacao["iniciada_em"]),
        "publicada_em": _data(importacao["publicada_em"]),
        "ativa": bool(importacao["ativa"]),
        "linhas": len(registros),
        "documentos": len(documentos),
        "grupos_operacionais": len(eventos),
        "codigos_ocorrencia": sorted(codigos),
        "regras_debito": sorted(regras),
        "status_debito": sorted(status),
        "eventos": eventos,
    }


def _comparar_versoes(
    anterior: dict,
    atual: dict,
) -> dict:
    campos = (
        "codigos_ocorrencia",
        "regras_debito",
        "status_debito",
        "grupos_operacionais",
    )

    alteracoes = {}

    for campo in campos:
        antes = anterior[campo]
        depois = atual[campo]

        if antes != depois:
            alteracoes[campo] = {
                "anterior": antes,
                "atual": depois,
            }

    return {
        "importacao_anterior_id": anterior["importacao_id"],
        "importacao_atual_id": atual["importacao_id"],
        "houve_alteracao": bool(alteracoes),
        "alteracoes": alteracoes,
        "observacao": (
            "Comparacao entre snapshots publicados. "
            "Nao comprova mudanca operacional no SSW."
        ),
    }


def consultar_historico(
    termo: str,
    *,
    tipo: str = "auto",
    limite: int = 500,
) -> dict:
    """
    Consulta historico de importacoes publicadas.

    Limite se refere a linhas brutas retornadas.
    """
    termo = _texto(termo)
    tipo = _texto(tipo).lower()

    if not termo or len(termo) > 80:
        raise HistoricoInvalido(
            "Informe CTRC ou NF com ate 80 caracteres."
        )

    if tipo not in ("auto", "ctrc", "nf"):
        raise HistoricoInvalido(
            "Tipo deve ser auto, ctrc ou nf."
        )

    if (
        isinstance(limite, bool)
        or not isinstance(limite, int)
        or not 1 <= limite <= 5000
    ):
        raise HistoricoInvalido(
            "Limite deve estar entre 1 e 5000."
        )

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT importacao_ativa_id
                FROM incidentes_estado
                WHERE chave = %s
                """,
                ("atual",),
            )

            estado = cur.fetchone()

            ativa_id = (
                estado["importacao_ativa_id"]
                if estado else None
            )

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
                    i.id AS importacao_id,
                    i.origem_arquivo,
                    i.iniciada_em,
                    i.publicada_em,
                    r.id,
                    r.linha_origem,
                    r.ctrc,
                    r.nota_fiscal,
                    r.codigo_ocorrencia,
                    r.regra_debito,
                    r.status_debito,
                    r.data_ocorrencia,
                    r.payload_json
                FROM incidentes_registros r
                JOIN incidentes_importacoes i
                  ON i.id = r.importacao_id
                WHERE i.status = %s
                  AND {filtro}
                ORDER BY i.publicada_em, i.id, r.id
                LIMIT %s
            """

            cur.execute(
                sql,
                tuple(
                    ["PUBLICADA", *parametros, limite + 1]
                ),
            )

            encontrados = cur.fetchall()

        truncado = len(encontrados) > limite
        registros = encontrados[:limite]

        por_importacao = defaultdict(list)
        metadados = {}

        for registro in registros:
            importacao_id = registro["importacao_id"]

            por_importacao[importacao_id].append(
                registro
            )

            if importacao_id not in metadados:
                metadados[importacao_id] = {
                    "id": importacao_id,
                    "origem_arquivo": registro[
                        "origem_arquivo"
                    ],
                    "iniciada_em": registro[
                        "iniciada_em"
                    ],
                    "publicada_em": registro[
                        "publicada_em"
                    ],
                    "ativa": importacao_id == ativa_id,
                }

        versoes = [
            _resumir_versao(
                metadados[importacao_id],
                linhas,
            )
            for importacao_id, linhas
            in por_importacao.items()
        ]

        comparacoes = [
            _comparar_versoes(
                versoes[indice - 1],
                versoes[indice],
            )
            for indice in range(1, len(versoes))
        ]

        return {
            "consulta": {
                "termo": termo,
                "tipo": tipo,
                "fonte": "snapshots_mysql",
                "tempo_real_ssw": False,
                "limite": limite,
                "truncado": truncado,
                "comparacao_completa": not truncado,
            },
            "importacao_ativa_id": ativa_id,
            "resumo": {
                "linhas_encontradas": len(registros),
                "versoes_encontradas": len(versoes),
                "comparacoes": len(comparacoes),
            },
            "versoes": versoes,
            "comparacoes": comparacoes,
            "avisos": [
                (
                    "Ausencia em uma importacao posterior "
                    "nao significa resolucao do incidente."
                ),
                (
                    "Grupos operacionais sao agrupamentos "
                    "provisorios, nao contagem oficial "
                    "de eventos ou debitos."
                ),
            ],
        }

    finally:
        conn.close()
