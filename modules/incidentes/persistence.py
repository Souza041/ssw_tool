"""Persistência versionada da aba AUDITORIA no automationx (MySQL 5.7)."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from database.automation import transaction


class ImportacaoInvalida(ValueError):
    pass


def _normalizar(valor: Any) -> Any:
    if valor is None:
        return None
    if isinstance(valor, (pd.Timestamp, datetime, date)):
        return None if pd.isna(valor) else valor.isoformat()
    if isinstance(valor, dict):
        return {str(k): _normalizar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_normalizar(v) for v in valor]
    if hasattr(valor, 'item'):
        try:
            return _normalizar(valor.item())
        except (TypeError, ValueError):
            pass
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(valor, float) and not math.isfinite(valor):
        return None
    if isinstance(valor, (str, int, float, bool)):
        return valor
    return str(valor)


def _texto(valor: Any, limite: int) -> str | None:
    valor = _normalizar(valor)
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    if len(texto) > limite:
        raise ImportacaoInvalida(f'Campo excede {limite} caracteres: {texto[:60]!r}')
    return texto


def _data(valor: Any) -> date | None:
    valor = _normalizar(valor)
    if valor is None or valor == '':
        return None
    try:
        return pd.to_datetime(valor, errors='raise').date()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ImportacaoInvalida(f'Data de ocorrência inválida: {valor!r}') from exc


def _hash_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open('rb') as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b''):
            h.update(bloco)
    return h.hexdigest()


def _registros(base: pd.DataFrame):
    for numero, valores in enumerate(base.itertuples(index=False, name=None), start=2):
        item = {coluna: _normalizar(valor) for coluna, valor in zip(base.columns, valores)}
        regra = item.get('AN_REGRA_DEBITO') or item.get('REGRA_VALIDACAO_DEBITO')
        status = item.get('AN_STATUS_DEBITO') or item.get('STATUS_VALIDACAO_DEBITO')
        codigo = item.get('AN_CODIGO_OCORRENCIA') or item.get('COD_OCOR')
        yield (
            numero,
            _texto(item.get('AN_CTRC') or item.get('CTRC'), 80),
            _texto(item.get('NRO_NOTA_FISCAL'), 80),
            _texto(codigo, 20),
            _texto(regra, 120),
            _texto(status, 100),
            _texto(item.get('AN_UNIDADE') or item.get('UNID_OCOR'), 60),
            _texto(item.get('AN_GRUPO_CLIENTE') or item.get('GRUPO_CLIENTE'), 180),
            _data(item.get('AN_DATA') or item.get('DATA_OCOR')),
            json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(',', ':')),
        )


def importar_auditoria(caminho: str | Path, *, publicar: bool = False,
                       dry_run: bool = True, lote: int = 100) -> dict:
    """Importa snapshot integral; publicação é atômica. Dry-run não acessa MySQL."""
    caminho = Path(caminho).resolve()
    if not caminho.is_file() or caminho.suffix.lower() != '.xlsx':
        raise ImportacaoInvalida('Informe um arquivo XLSX existente.')
    if not 1 <= lote <= 500:
        raise ImportacaoInvalida('Lote deve estar entre 1 e 500.')

    sha = _hash_arquivo(caminho)
    identificador = f'op930-{sha[:64]}'
    base = pd.read_excel(caminho, sheet_name='AUDITORIA')
    base.columns = [str(c).strip().upper() for c in base.columns]
    if base.columns.duplicated().any():
        raise ImportacaoInvalida('Colunas duplicadas na aba AUDITORIA.')
    if 'CTRC' not in base.columns and 'AN_CTRC' not in base.columns:
        raise ImportacaoInvalida('A aba AUDITORIA não possui coluna CTRC.')
    if base.empty:
        raise ImportacaoInvalida('A aba AUDITORIA está vazia.')
    total = len(base)
    resumo = {'identificador': identificador, 'sha256': sha,
              'arquivo': str(caminho), 'total': total, 'dry_run': dry_run,
              'publicar': publicar}

    if dry_run:
        for _ in _registros(base):
            pass  # valida serialização e campos sem gravar nada
        return {**resumo, 'status': 'SIMULADO'}

    # Uma transação engloba importação, conferência e troca do ponteiro.
    # Em caso de falha, não fica uma importação parcialmente gravada.
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, status FROM incidentes_importacoes '
                        'WHERE identificador=%s FOR UPDATE', (identificador,))
            anterior = cur.fetchone()
            if anterior:
                raise ImportacaoInvalida(
                    f'Arquivo já importado: id={anterior["id"]}, status={anterior["status"]}. '
                    'Nenhuma alteração realizada.')
            cur.execute(
                'INSERT INTO incidentes_importacoes '
                '(identificador, origem_arquivo, sha256_arquivo, status, total_registros) '
                'VALUES (%s,%s,%s,%s,%s)',
                (identificador, caminho.name, sha, 'PROCESSANDO', total),
            )
            importacao_id = cur.lastrowid
            sql = (
                'INSERT INTO incidentes_registros '
                '(importacao_id, linha_origem, ctrc, nota_fiscal, codigo_ocorrencia, '
                'regra_debito, status_debito, unidade, grupo_cliente, data_ocorrencia, payload_json) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
            )
            lote_atual = []
            for registro in _registros(base):
                lote_atual.append((importacao_id, *registro))
                if len(lote_atual) >= lote:
                    cur.executemany(sql, lote_atual)
                    lote_atual.clear()
            if lote_atual:
                cur.executemany(sql, lote_atual)
            cur.execute('SELECT COUNT(*) AS total FROM incidentes_registros '
                        'WHERE importacao_id=%s', (importacao_id,))
            persistidos = cur.fetchone()['total']
            if persistidos != total:
                raise RuntimeError(f'Contagem divergente: {persistidos} != {total}')
            status = 'PUBLICADA' if publicar else 'VALIDADA'
            cur.execute(
                'UPDATE incidentes_importacoes SET status=%s, total_persistidos=%s, '
                'finalizada_em=NOW(), publicada_em=IF(%s, NOW(), NULL) WHERE id=%s',
                (status, persistidos, int(publicar), importacao_id),
            )
            if publicar:
                # Estado singleton; lock de escrita serializa publicações concorrentes.
                cur.execute('INSERT INTO incidentes_estado (chave, importacao_ativa_id) '
                            'VALUES (%s, %s) ON DUPLICATE KEY UPDATE '
                            'importacao_ativa_id=VALUES(importacao_ativa_id)',
                            ('atual', importacao_id))
    return {**resumo, 'status': status, 'importacao_id': importacao_id,
            'persistidos': persistidos}


def publicar_importacao(
    importacao_id: int,
    *,
    dry_run: bool = True,
) -> dict:
    """
    Publica uma importação existente e validada.

    - Simula por padrão.
    - Confere a integridade dos registros.
    - Serializa publicações concorrentes.
    - Atualiza o ponteiro ativo em transação.
    - Preserva a versão anterior em caso de falha.
    """
    from database.automation import get_connection

    if (
        isinstance(importacao_id, bool)
        or not isinstance(importacao_id, int)
        or importacao_id < 1
    ):
        raise ImportacaoInvalida(
            "ID de importação inválido."
        )

    conn = get_connection(autocommit=False)
    bloqueado = False

    try:
        with conn.cursor() as cur:
            # Bloqueio exclusivo entre publicadores.
            cur.execute(
                "SELECT GET_LOCK(%s, %s) AS adquirido",
                ("incidentes_publicacao", 10),
            )

            bloqueado = (
                cur.fetchone()["adquirido"] == 1
            )

            if not bloqueado:
                raise ImportacaoInvalida(
                    "Não foi possível obter o bloqueio "
                    "de publicação."
                )

            cur.execute(
                """
                SELECT
                    id,
                    status,
                    total_registros,
                    total_persistidos
                FROM incidentes_importacoes
                WHERE id = %s
                FOR UPDATE
                """,
                (importacao_id,),
            )

            importacao = cur.fetchone()

            if not importacao:
                raise ImportacaoInvalida(
                    f"Importação #{importacao_id} "
                    "não encontrada."
                )

            if importacao["status"] not in (
                "VALIDADA",
                "PUBLICADA",
            ):
                raise ImportacaoInvalida(
                    "Somente importações validadas "
                    "podem ser publicadas."
                )

            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM incidentes_registros
                WHERE importacao_id = %s
                """,
                (importacao_id,),
            )

            persistidos = int(
                cur.fetchone()["total"]
            )

            esperado = int(
                importacao["total_registros"]
            )

            registrado = int(
                importacao["total_persistidos"]
            )

            if (
                esperado <= 0
                or persistidos != esperado
                or registrado != esperado
            ):
                raise ImportacaoInvalida(
                    "Integridade divergente: "
                    "publicação cancelada."
                )

            cur.execute(
                """
                SELECT importacao_ativa_id
                FROM incidentes_estado
                WHERE chave = %s
                FOR UPDATE
                """,
                ("atual",),
            )

            estado = cur.fetchone()

            anterior = (
                estado["importacao_ativa_id"]
                if estado
                else None
            )

            resultado = {
                "importacao_id": importacao_id,
                "importacao_anterior_id": anterior,
                "registros": persistidos,
                "dry_run": dry_run,
                "ja_ativa": anterior == importacao_id,
            }

            if (
                not dry_run
                and anterior != importacao_id
            ):
                cur.execute(
                    """
                    INSERT INTO incidentes_estado
                        (chave, importacao_ativa_id)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE
                        importacao_ativa_id =
                            VALUES(importacao_ativa_id)
                    """,
                    ("atual", importacao_id),
                )

                cur.execute(
                    """
                    UPDATE incidentes_importacoes
                    SET
                        status = %s,
                        publicada_em =
                            COALESCE(
                                publicada_em,
                                NOW()
                            )
                    WHERE id = %s
                    """,
                    ("PUBLICADA", importacao_id),
                )

            resultado["status"] = (
                "SIMULADO"
                if dry_run
                else (
                    "JA_ATIVA"
                    if anterior == importacao_id
                    else "PUBLICADA"
                )
            )

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

        return resultado

    except Exception:
        conn.rollback()
        raise

    finally:
        try:
            if bloqueado:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT RELEASE_LOCK(%s)",
                        ("incidentes_publicacao",),
                    )
        finally:
            conn.close()
