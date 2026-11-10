-- Aplicar manualmente, depois de confirmar schema, permissões e backup.
-- DATETIME(6) armazena UTC; a conversão para America/Sao_Paulo pertence à leitura.
-- Não há DROP, ALTER de tabelas existentes ou criação automática no startup.

CREATE TABLE IF NOT EXISTS health_bots (
    identifier VARCHAR(100) NOT NULL,
    name VARCHAR(160) NOT NULL,
    description VARCHAR(500) NOT NULL DEFAULT '',
    category VARCHAR(80) NOT NULL DEFAULT 'geral',
    -- Catálogo cadastrado; não representa habilitação operacional do bot.
    enabled TINYINT(1) NOT NULL DEFAULT 0,
    schedule_source VARCHAR(80) NULL,
    parameters_json JSON NULL,
    created_at_utc DATETIME(6) NOT NULL,
    updated_at_utc DATETIME(6) NOT NULL,
    PRIMARY KEY (identifier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS health_executions (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    bot_id VARCHAR(100) NOT NULL,
    bot_name VARCHAR(160) NOT NULL,
    origin VARCHAR(40) NOT NULL,
    responsible_user VARCHAR(160) NULL,
    status VARCHAR(16) NOT NULL,
    started_at_utc DATETIME(6) NOT NULL,
    finished_at_utc DATETIME(6) NULL,
    duration_seconds DECIMAL(16, 6) NULL,
    processed_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    success_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    error_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    summary VARCHAR(500) NULL,
    error_message VARCHAR(500) NULL,
    log_ref VARCHAR(500) NULL,
    metadata_json JSON NULL,
    correlation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    code_version VARCHAR(100) NULL,
    PRIMARY KEY (id),
    KEY idx_health_bot_started (bot_id, started_at_utc),
    KEY idx_health_status_started (status, started_at_utc),
    KEY idx_health_correlation (correlation_id),
    CONSTRAINT fk_health_execution_bot FOREIGN KEY (bot_id)
        REFERENCES health_bots (identifier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
