from modules.metricas.database import get_connection

import json

from pathlib import Path

def execute(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.lastrowid
    finally:
        conn.close()


def fetch_one(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchone()
    finally:
        conn.close()


def fetch_all(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        conn.close()


def criar_execucao(source="OP455", triggered_by="manual", triggered_user_id=None):
    return execute("""
        INSERT INTO dashboard_runs
        (status, source, started_at, triggered_by, triggered_user_id)
        VALUES ('running', %s, NOW(), %s, %s)
    """, (source, triggered_by, triggered_user_id))


def finalizar_execucao_sucesso(run_id, file_name, total_records):
    execute("""
        UPDATE dashboard_runs
        SET status='success',
            file_name=%s,
            total_records=%s,
            finished_at=NOW()
        WHERE id=%s
    """, (file_name, total_records, run_id))


def finalizar_execucao_erro(run_id, error_message):
    execute("""
        UPDATE dashboard_runs
        SET status='error',
            error_message=%s,
            finished_at=NOW()
        WHERE id=%s
    """, (str(error_message), run_id))

    execute("""
        INSERT INTO refresh_logs (run_id, message, level)
        VALUES (%s, %s, 'error')
    """, (run_id, str(error_message)))


def inserir_registro(params):
    execute("""
        INSERT INTO dashboard_records (
            run_id, cte, nota_fiscal, unidade, unidade_receptora,
            cliente, remetente, destinatario, cidade_destino, uf_destino,
            previsao_entrega, data_emissao, dia_emissao,
            status_prazo, status_entrega, dias_atraso,
            ocorrencia, ocorrencia_73, ultima_ocorrencia,
            parceiro, cidade_parceiro, uf_parceiro, endereco_parceiro,
            raw_json
        ) VALUES (
            %(run_id)s, %(cte)s, %(nota_fiscal)s, %(unidade)s, %(unidade_receptora)s,
            %(cliente)s, %(remetente)s, %(destinatario)s, %(cidade_destino)s, %(uf_destino)s,
            %(previsao_entrega)s, %(data_emissao)s, %(dia_emissao)s,
            %(status_prazo)s, %(status_entrega)s, %(dias_atraso)s,
            %(ocorrencia)s, %(ocorrencia_73)s, %(ultima_ocorrencia)s,
            %(parceiro)s, %(cidade_parceiro)s, %(uf_parceiro)s, %(endereco_parceiro)s,
            %(raw_json)s
        )
    """, params)


def obter_ultima_execucao_sucesso():
    return fetch_one("""
        SELECT id, total_records, file_name, started_at, finished_at
        FROM dashboard_runs
        WHERE status='success'
        ORDER BY id DESC
        LIMIT 1
    """)

def salvar_snapshot(run_id, payload):
    output_dir = Path("outputs/metricas_snapshots")
    output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / f"snapshot_{run_id}.json"

    file_path.write_text(
        json.dumps(payload, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    execute("""
        INSERT INTO dashboard_snapshots (run_id, payload_json, payload_path)
        VALUES (%s, NULL, %s)
    """, (
        run_id,
        str(file_path),
    ))


def obter_ultimo_snapshot():
    return fetch_one("""
        SELECT
            s.id,
            s.run_id,
            s.payload_json,
            s.payload_path,
            s.created_at,
            r.file_name,
            r.total_records,
            r.started_at,
            r.finished_at
        FROM dashboard_snapshots s
        INNER JOIN dashboard_runs r ON r.id = s.run_id
        WHERE r.status = 'success'
        ORDER BY s.id DESC
        LIMIT 1
    """)