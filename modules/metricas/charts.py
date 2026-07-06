from modules.metricas import repository as repo


def obter_cards(run_id):
    return repo.fetch_one("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status_prazo='NO_PRAZO' THEN 1 ELSE 0 END) AS no_prazo,
            SUM(CASE WHEN status_prazo='ATRASADO' THEN 1 ELSE 0 END) AS atrasados,
            SUM(CASE WHEN ocorrencia_73=1 THEN 1 ELSE 0 END) AS ocorrencia_73
        FROM dashboard_records
        WHERE run_id=%s
    """, (run_id,))


def obter_grafico_unidades(run_id):
    return repo.fetch_all("""
        SELECT
            COALESCE(unidade_receptora, unidade, 'SEM UNIDADE') AS unidade,
            COUNT(*) AS total,
            SUM(CASE WHEN status_prazo='NO_PRAZO' THEN 1 ELSE 0 END) AS no_prazo,
            SUM(CASE WHEN status_prazo='ATRASADO' THEN 1 ELSE 0 END) AS atrasados
        FROM dashboard_records
        WHERE run_id=%s
        GROUP BY COALESCE(unidade_receptora, unidade, 'SEM UNIDADE')
        ORDER BY total DESC
        LIMIT 30
    """, (run_id,))


def obter_grafico_ocorrencia_73_dia(run_id):
    return repo.fetch_all("""
        SELECT
            COALESCE(dia_emissao, 0) AS dia,
            COUNT(*) AS total
        FROM dashboard_records
        WHERE run_id=%s
          AND ocorrencia_73=1
        GROUP BY COALESCE(dia_emissao, 0)
        ORDER BY dia
    """, (run_id,))