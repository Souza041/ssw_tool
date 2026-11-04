USE documentos_lg;

CREATE TABLE lg_execucoes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    identificador CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tipo ENUM('historica', 'incremental', 'manual') NOT NULL,
    status ENUM(
        'pendente',
        'executando',
        'concluida',
        'falhou'
    ) NOT NULL DEFAULT 'pendente',

    inicio_periodo DATE NOT NULL,
    fim_periodo DATE NOT NULL,

    iniciada_em DATETIME NULL,
    finalizada_em DATETIME NULL,
    erro TEXT NULL,

    criada_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_lg_execucao_identificador (identificador),
    KEY idx_lg_execucao_status (status, criada_em)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE lg_importacoes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    execucao_id BIGINT UNSIGNED NOT NULL,

    relatorio ENUM('OP435', 'OP455', 'OP930') NOT NULL,
    status ENUM(
        'pendente',
        'importando',
        'concluida',
        'falhou'
    ) NOT NULL DEFAULT 'pendente',

    inicio_periodo DATE NOT NULL,
    fim_periodo DATE NOT NULL,

    nome_arquivo VARCHAR(255) NOT NULL,
    arquivo_sha256 CHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,

    tamanho_bytes BIGINT UNSIGNED NOT NULL,
    filtros_json LONGTEXT NOT NULL,
    versao_leitor VARCHAR(30) NOT NULL,

    registros_lidos BIGINT UNSIGNED NOT NULL DEFAULT 0,
    registros_gravados BIGINT UNSIGNED NOT NULL DEFAULT 0,

    iniciada_em DATETIME NULL,
    finalizada_em DATETIME NULL,
    erro TEXT NULL,

    criada_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    KEY idx_lg_importacao_execucao (execucao_id),
    KEY idx_lg_importacao_arquivo (relatorio, arquivo_sha256),
    KEY idx_lg_importacao_status (status, criada_em),

    CONSTRAINT fk_lg_importacao_execucao
        FOREIGN KEY (execucao_id)
        REFERENCES lg_execucoes (id)
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


CREATE TABLE lg_registros_importados (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    importacao_id BIGINT UNSIGNED NOT NULL,

    numero_linha INT UNSIGNED NOT NULL,

    registro_sha256 CHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,

    ctrc_chave VARCHAR(30)
        CHARACTER SET ascii COLLATE ascii_bin NULL,

    ctrc_original VARCHAR(50) NULL,

    cte_serie VARCHAR(3)
        CHARACTER SET ascii COLLATE ascii_bin NULL,

    cte_numero VARCHAR(9)
        CHARACTER SET ascii COLLATE ascii_bin NULL,

    dados_json LONGTEXT NOT NULL,

    criada_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),

    UNIQUE KEY uq_lg_importacao_linha (
        importacao_id,
        numero_linha
    ),

    KEY idx_lg_registro_ctrc (ctrc_chave),
    KEY idx_lg_registro_cte (cte_numero, cte_serie),
    KEY idx_lg_registro_hash (registro_sha256),

    CONSTRAINT fk_lg_registro_importacao
        FOREIGN KEY (importacao_id)
        REFERENCES lg_importacoes (id)
        ON DELETE RESTRICT
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;
