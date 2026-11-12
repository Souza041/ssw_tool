-- Sprint Health 03. Aplicação exclusivamente manual no schema automationx confirmado.
-- Compatível com MySQL/Percona 5.7; não altera tabelas existentes.
-- DATETIME(6) armazena UTC. Não há eventos, triggers ou execução automática.

CREATE TABLE IF NOT EXISTS health_dependencies (
    identifier VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    name VARCHAR(160) NOT NULL,
    dependency_type VARCHAR(32) NOT NULL,
    target_host VARCHAR(253) NOT NULL,
    target_port SMALLINT UNSIGNED NOT NULL,
    target_scheme VARCHAR(16) NOT NULL,
    target_path VARCHAR(500) NOT NULL DEFAULT '/',
    enabled TINYINT(1) NOT NULL DEFAULT 0,
    maintenance TINYINT(1) NOT NULL DEFAULT 0,
    probes_json JSON NOT NULL,
    config_json JSON NULL,
    created_at_utc DATETIME(6) NOT NULL,
    updated_at_utc DATETIME(6) NOT NULL,
    PRIMARY KEY (identifier),
    KEY idx_health_dependency_enabled (enabled, identifier),
    KEY idx_health_dependency_updated (updated_at_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS health_dependency_checks (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    dependency_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    probe_type VARCHAR(16) NOT NULL,
    observed_state VARCHAR(16) NOT NULL,
    checked_at_utc DATETIME(6) NOT NULL,
    latency_ms DECIMAL(16, 3) NULL,
    reason_code VARCHAR(80) NOT NULL,
    technical_detail VARCHAR(500) NOT NULL,
    http_status SMALLINT UNSIGNED NULL,
    metadata_json JSON NULL,
    PRIMARY KEY (id),
    KEY idx_health_dep_check_period (dependency_id, checked_at_utc),
    KEY idx_health_dep_check_state (observed_state, checked_at_utc),
    CONSTRAINT fk_health_dep_check_catalog FOREIGN KEY (dependency_id)
        REFERENCES health_dependencies (identifier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS health_dependency_incidents (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    dependency_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    state VARCHAR(16) NOT NULL,
    opened_at_utc DATETIME(6) NOT NULL,
    recovered_at_utc DATETIME(6) NULL,
    opening_check_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    recovery_check_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    reason_code VARCHAR(80) NOT NULL,
    technical_detail VARCHAR(500) NOT NULL,
    active_key VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    last_internal_alert_at_utc DATETIME(6) NULL,
    internal_alert_count INT UNSIGNED NOT NULL DEFAULT 0,
    created_at_utc DATETIME(6) NOT NULL,
    updated_at_utc DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_health_dep_active_incident (active_key),
    KEY idx_health_dep_incident_period (dependency_id, opened_at_utc),
    KEY idx_health_dep_incident_state (state, updated_at_utc),
    CONSTRAINT fk_health_dep_incident_catalog FOREIGN KEY (dependency_id)
        REFERENCES health_dependencies (identifier),
    CONSTRAINT fk_health_dep_incident_open_check FOREIGN KEY (opening_check_id)
        REFERENCES health_dependency_checks (id),
    CONSTRAINT fk_health_dep_incident_recovery_check FOREIGN KEY (recovery_check_id)
        REFERENCES health_dependency_checks (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS health_dependency_state (
    dependency_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    current_state VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN',
    consecutive_failures INT UNSIGNED NOT NULL DEFAULT 0,
    consecutive_successes INT UNSIGNED NOT NULL DEFAULT 0,
    last_check_at_utc DATETIME(6) NULL,
    last_success_at_utc DATETIME(6) NULL,
    last_failure_at_utc DATETIME(6) NULL,
    latency_ms DECIMAL(16, 3) NULL,
    reason_code VARCHAR(80) NULL,
    technical_detail VARCHAR(500) NULL,
    current_incident_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    stale_after_utc DATETIME(6) NULL,
    updated_at_utc DATETIME(6) NOT NULL,
    PRIMARY KEY (dependency_id),
    KEY idx_health_dep_state_status (current_state, updated_at_utc),
    KEY idx_health_dep_state_stale (stale_after_utc),
    CONSTRAINT fk_health_dep_state_catalog FOREIGN KEY (dependency_id)
        REFERENCES health_dependencies (identifier),
    CONSTRAINT fk_health_dep_state_incident FOREIGN KEY (current_incident_id)
        REFERENCES health_dependency_incidents (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS health_execution_dependency_incidents (
    execution_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    incident_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    linked_at_utc DATETIME(6) NOT NULL,
    PRIMARY KEY (execution_id, incident_id),
    KEY idx_health_execution_dep_incident (incident_id, linked_at_utc),
    CONSTRAINT fk_health_execution_dep_execution FOREIGN KEY (execution_id)
        REFERENCES health_executions (id),
    CONSTRAINT fk_health_execution_dep_incident FOREIGN KEY (incident_id)
        REFERENCES health_dependency_incidents (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
