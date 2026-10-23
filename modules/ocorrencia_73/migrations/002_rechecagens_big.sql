-- MySQL 5.7 / InnoDB. Aplicar no mesmo banco das tabelas oc73_*.
-- Não altera nem limpa execuções/tentativas existentes.
CREATE TABLE IF NOT EXISTS oc73_rechecagens_big (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    execucao_id BIGINT UNSIGNED NOT NULL,
    data_referencia DATE NOT NULL,
    serie VARCHAR(10) NOT NULL,
    numero VARCHAR(30) NOT NULL,
    item_json LONGTEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'aguardando',
    limite_envios INT UNSIGNED NOT NULL,
    proxima_consulta_em DATETIME NOT NULL,
    limite_em DATETIME NOT NULL,
    consultas INT UNSIGNED NOT NULL DEFAULT 0,
    ultima_consulta_em DATETIME DEFAULT NULL,
    ultimo_codigo VARCHAR(10) DEFAULT NULL,
    detalhe VARCHAR(2000) DEFAULT NULL,
    execucao_envio_id BIGINT UNSIGNED DEFAULT NULL,
    tentativa_id BIGINT UNSIGNED DEFAULT NULL,
    criado_em DATETIME NOT NULL,
    atualizado_em DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_oc73_rechecagem_big (data_referencia, serie, numero),
    KEY idx_oc73_rechecagem_due (status, proxima_consulta_em),
    KEY idx_oc73_rechecagem_exec (execucao_id),
    KEY idx_oc73_rechecagem_envio (execucao_envio_id),
    CONSTRAINT fk_oc73_rechecagem_exec FOREIGN KEY (execucao_id)
        REFERENCES oc73_execucoes(id),
    CONSTRAINT fk_oc73_rechecagem_envio FOREIGN KEY (execucao_envio_id)
        REFERENCES oc73_execucoes(id),
    CONSTRAINT fk_oc73_rechecagem_tent FOREIGN KEY (tentativa_id)
        REFERENCES oc73_tentativas(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
