CREATE DATABASE IF NOT EXISTS documentosgce
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE documentosgce;

CREATE TABLE IF NOT EXISTS document_imports (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source VARCHAR(30) NOT NULL,
    period_start DATE NULL,
    period_end DATE NULL,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(1000) NULL,
    file_hash CHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING',
    total_rows INT UNSIGNED NOT NULL DEFAULT 0,
    inserted_rows INT UNSIGNED NOT NULL DEFAULT 0,
    updated_rows INT UNSIGNED NOT NULL DEFAULT 0,
    rejected_rows INT UNSIGNED NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_document_import_source_hash (source, file_hash),
    KEY idx_document_import_period (source, period_start, period_end),
    KEY idx_document_import_status (status, started_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ssw_documents (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    ctrc VARCHAR(30) NOT NULL,
    ctrc_raw VARCHAR(60) NOT NULL,
    cte_number VARCHAR(50) NULL,
    document_type VARCHAR(40) NULL,
    issue_date DATE NULL,
    issuing_unit VARCHAR(20) NULL,
    receiving_unit VARCHAR(20) NULL,
    payer_cnpj CHAR(14) NULL,
    payer_name VARCHAR(255) NULL,
    recipient_cnpj CHAR(14) NULL,
    recipient_name VARCHAR(255) NULL,
    recipient_state CHAR(2) NULL,
    remittance_cover_number VARCHAR(100) NULL,
    archive_package_number VARCHAR(100) NULL,
    scanned TINYINT(1) NOT NULL DEFAULT 0,
    scanned_at DATETIME NULL,
    origin_ctrc VARCHAR(60) NULL,
    expedition_map VARCHAR(100) NULL,
    reception_map VARCHAR(100) NULL,
    volumes VARCHAR(255) NULL,
    customer_shipment VARCHAR(255) NULL,
    source_import_id BIGINT UNSIGNED NOT NULL,
    first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ssw_document_ctrc_raw (ctrc_raw),
    KEY idx_ssw_document_ctrc (ctrc),
    KEY idx_ssw_document_issue_date (issue_date),
    KEY idx_ssw_document_recipient_cnpj (recipient_cnpj),
    KEY idx_ssw_document_package (archive_package_number),
    CONSTRAINT fk_ssw_document_import
        FOREIGN KEY (source_import_id) REFERENCES document_imports (id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ssw_document_invoices (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    document_id BIGINT UNSIGNED NOT NULL,
    invoice_number VARCHAR(60) NOT NULL,
    invoice_series VARCHAR(30) NOT NULL DEFAULT '',
    access_key VARCHAR(80) NULL,
    is_primary TINYINT(1) NOT NULL DEFAULT 0,
    source_import_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ssw_invoice_document (document_id, invoice_number, invoice_series),
    KEY idx_ssw_invoice_number (invoice_number),
    KEY idx_ssw_invoice_access_key (access_key),
    CONSTRAINT fk_ssw_invoice_document
        FOREIGN KEY (document_id) REFERENCES ssw_documents (id) ON DELETE CASCADE,
    CONSTRAINT fk_ssw_invoice_import
        FOREIGN KEY (source_import_id) REFERENCES document_imports (id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ssw_document_orders (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    document_id BIGINT UNSIGNED NOT NULL,
    order_number VARCHAR(80) NOT NULL,
    order_type VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN',
    source_import_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ssw_order_document (document_id, order_number),
    KEY idx_ssw_order_number (order_number),
    KEY idx_ssw_order_type (order_type),
    CONSTRAINT fk_ssw_order_document
        FOREIGN KEY (document_id) REFERENCES ssw_documents (id) ON DELETE CASCADE,
    CONSTRAINT fk_ssw_order_import
        FOREIGN KEY (source_import_id) REFERENCES document_imports (id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ssw_occurrences (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    document_id BIGINT UNSIGNED NULL,
    ctrc VARCHAR(30) NOT NULL,
    ctrc_raw VARCHAR(60) NOT NULL,
    invoice_number VARCHAR(60) NOT NULL DEFAULT '',
    invoice_series VARCHAR(30) NOT NULL DEFAULT '',
    occurrence_code VARCHAR(10) NOT NULL,
    occurrence_description VARCHAR(255) NULL,
    occurrence_complement TEXT NULL,
    occurrence_at DATETIME NULL,
    included_at DATETIME NULL,
    occurrence_user VARCHAR(100) NULL,
    occurrence_company VARCHAR(30) NULL,
    occurrence_unit VARCHAR(30) NULL,
    delivery_date DATE NULL,
    payer_cnpj CHAR(14) NULL,
    payer_name VARCHAR(255) NULL,
    canceled TINYINT(1) NOT NULL DEFAULT 0,
    source_import_id BIGINT UNSIGNED NOT NULL,
    source_hash CHAR(64) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ssw_occurrence_hash (source_hash),
    KEY idx_ssw_occurrence_ctrc (ctrc),
    KEY idx_ssw_occurrence_document (document_id),
    KEY idx_ssw_occurrence_invoice (invoice_number),
    KEY idx_ssw_occurrence_code_date (occurrence_code, occurrence_at),
    CONSTRAINT fk_ssw_occurrence_document
        FOREIGN KEY (document_id) REFERENCES ssw_documents (id) ON DELETE SET NULL,
    CONSTRAINT fk_ssw_occurrence_import
        FOREIGN KEY (source_import_id) REFERENCES document_imports (id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS portal_documents (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    report_type VARCHAR(30) NOT NULL,
    carrier_cnpj CHAR(14) NULL,
    warehouse_ctrc VARCHAR(30) NOT NULL DEFAULT '',
    warehouse_ctrc_raw VARCHAR(60) NOT NULL DEFAULT '',
    invoice_number VARCHAR(60) NOT NULL,
    invoice_series VARCHAR(30) NOT NULL DEFAULT '',
    transport_number VARCHAR(80) NULL,
    issue_date DATE NULL,
    recipient_name VARCHAR(255) NULL,
    recipient_cnpj CHAR(14) NULL,
    recipient_state CHAR(2) NULL,
    pending_at DATETIME NULL,
    pending_user VARCHAR(100) NULL,
    pending_reason VARCHAR(255) NULL,
    source_import_id BIGINT UNSIGNED NOT NULL,
    source_key CHAR(64) NOT NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_portal_document_source_key (source_key),
    KEY idx_portal_document_invoice (invoice_number),
    KEY idx_portal_document_issue_date (issue_date),
    KEY idx_portal_document_status (report_type, active),
    KEY idx_portal_document_transport (transport_number),
    CONSTRAINT fk_portal_document_import
        FOREIGN KEY (source_import_id) REFERENCES document_imports (id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS document_matches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    portal_document_id BIGINT UNSIGNED NOT NULL,
    ssw_document_id BIGINT UNSIGNED NULL,
    status VARCHAR(20) NOT NULL,
    score INT NOT NULL DEFAULT 0,
    candidate_count INT UNSIGNED NOT NULL DEFAULT 0,
    matched_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_document_match_portal (portal_document_id),
    KEY idx_document_match_ssw (ssw_document_id),
    KEY idx_document_match_status (status),
    CONSTRAINT fk_document_match_portal
        FOREIGN KEY (portal_document_id) REFERENCES portal_documents (id) ON DELETE CASCADE,
    CONSTRAINT fk_document_match_ssw
        FOREIGN KEY (ssw_document_id) REFERENCES ssw_documents (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS document_alerts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    portal_document_id BIGINT UNSIGNED NOT NULL,
    ssw_document_id BIGINT UNSIGNED NULL,
    alert_type VARCHAR(30) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    delivery_date DATE NULL,
    deadline_date DATE NULL,
    days_remaining INT NULL,
    details TEXT NULL,
    opened_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_document_alert_opening (portal_document_id, alert_type, deadline_date),
    KEY idx_document_alert_status (status, alert_type),
    KEY idx_document_alert_deadline (deadline_date),
    CONSTRAINT fk_document_alert_portal
        FOREIGN KEY (portal_document_id) REFERENCES portal_documents (id) ON DELETE CASCADE,
    CONSTRAINT fk_document_alert_ssw
        FOREIGN KEY (ssw_document_id) REFERENCES ssw_documents (id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS notification_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    alert_id BIGINT UNSIGNED NOT NULL,
    channel VARCHAR(20) NOT NULL DEFAULT 'EMAIL',
    recipient VARCHAR(255) NOT NULL,
    subject VARCHAR(255) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    sent_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_notification_alert (alert_id),
    KEY idx_notification_status (status, created_at),
    CONSTRAINT fk_notification_alert
        FOREIGN KEY (alert_id) REFERENCES document_alerts (id) ON DELETE CASCADE
) ENGINE=InnoDB;

