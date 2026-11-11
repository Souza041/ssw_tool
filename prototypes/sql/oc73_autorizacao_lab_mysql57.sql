-- PROPOSTA NÃO EXECUTADA — somente schema isolado autorizado.
-- Compatível com MySQL 5.7/InnoDB; não usar em automationx.
-- A identidade do servidor deve ser validada antes de qualquer CREATE.

CREATE TABLE oc73_lab_tentativas (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  serie VARCHAR(10) NOT NULL,
  numero VARCHAR(32) NOT NULL,
  seq_ctrc VARCHAR(32) NOT NULL,
  familia VARCHAR(32) NOT NULL,
  local_ssw VARCHAR(10) NOT NULL,
  referencia VARCHAR(25) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  revisao_codigo VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  status ENUM('reservado','envio_iniciado','incerto','confirmado') NOT NULL,
  reservado_em DATETIME(6) NOT NULL,
  envio_iniciado_em DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_oc73_lab_tentativa_contexto
    (id, referencia, revisao_codigo),
  -- HIPÓTESE DE DOMÍNIO: (serie, numero) identifica o CTRC globalmente.
  -- Confirmar no SSW antes de qualquer promoção deste DDL.
  UNIQUE KEY uq_oc73_lab_ctrc (serie, numero),
  UNIQUE KEY uq_oc73_lab_referencia (referencia)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE oc73_lab_autorizacoes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  autorizacao_uuid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  tentativa_id BIGINT UNSIGNED NOT NULL,
  referencia VARCHAR(25) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  revisao_codigo VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  status ENUM('pendente','revogada','consumida') NOT NULL,
  emitida_em DATETIME(6) NOT NULL,
  expira_em DATETIME(6) NOT NULL,
  revogada_em DATETIME(6) NULL,
  consumida_em DATETIME(6) NULL,
  versao INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uq_oc73_lab_auth_uuid (autorizacao_uuid),
  UNIQUE KEY uq_oc73_lab_auth_tentativa (tentativa_id),
  UNIQUE KEY uq_oc73_lab_auth_contexto (id, tentativa_id),
  KEY ix_oc73_lab_auth_status_expira (status, expira_em),
  CONSTRAINT fk_oc73_lab_auth_tentativa
    FOREIGN KEY (tentativa_id, referencia, revisao_codigo)
    REFERENCES oc73_lab_tentativas (id, referencia, revisao_codigo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE oc73_lab_aprovacoes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  autorizacao_id BIGINT UNSIGNED NOT NULL,
  papel ENUM('tecnico','operacional') NOT NULL,
  subject_issuer VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  person_issuer VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  person_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  person_verified TINYINT UNSIGNED NOT NULL,
  person_binding_expires_at DATETIME(6) NOT NULL,
  session_id_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  autenticada_em DATETIME(6) NOT NULL,
  aprovada_em DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_oc73_lab_aprovacao_papel (autorizacao_id, papel),
  UNIQUE KEY uq_oc73_lab_aprovacao_conta
    (autorizacao_id, subject_issuer, subject_id),
  UNIQUE KEY uq_oc73_lab_aprovacao_pessoa
    (autorizacao_id, person_issuer, person_id),
  CONSTRAINT fk_oc73_lab_aprovacao_auth
    FOREIGN KEY (autorizacao_id) REFERENCES oc73_lab_autorizacoes (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE oc73_lab_consumos (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  autorizacao_id BIGINT UNSIGNED NOT NULL,
  tentativa_id BIGINT UNSIGNED NOT NULL,
  executor_subject_issuer VARCHAR(191)
    CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  executor_subject_id VARCHAR(128)
    CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  executor_person_issuer VARCHAR(191)
    CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  executor_person_id VARCHAR(128)
    CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  executor_person_verified TINYINT UNSIGNED NOT NULL,
  executor_person_binding_expires_at DATETIME(6) NOT NULL,
  executor_session_id_hash CHAR(64)
    CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  executor_autenticada_em DATETIME(6) NOT NULL,
  consumida_em DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_oc73_lab_consumo_auth (autorizacao_id),
  UNIQUE KEY uq_oc73_lab_consumo_tentativa (tentativa_id),
  CONSTRAINT fk_oc73_lab_consumo_auth
    FOREIGN KEY (autorizacao_id, tentativa_id)
    REFERENCES oc73_lab_autorizacoes (id, tentativa_id),
  CONSTRAINT fk_oc73_lab_consumo_tentativa
    FOREIGN KEY (tentativa_id) REFERENCES oc73_lab_tentativas (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE oc73_lab_auditoria (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  autorizacao_id BIGINT UNSIGNED NOT NULL,
  tentativa_id BIGINT UNSIGNED NOT NULL,
  evento VARCHAR(64) NOT NULL,
  ocorrido_em DATETIME(6) NOT NULL,
  detalhe_saneado VARCHAR(255) NULL,
  PRIMARY KEY (id),
  KEY ix_oc73_lab_auditoria_auth (autorizacao_id, id),
  CONSTRAINT fk_oc73_lab_auditoria_auth
    FOREIGN KEY (autorizacao_id, tentativa_id)
    REFERENCES oc73_lab_autorizacoes (id, tentativa_id),
  CONSTRAINT fk_oc73_lab_auditoria_tentativa
    FOREIGN KEY (tentativa_id) REFERENCES oc73_lab_tentativas (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
