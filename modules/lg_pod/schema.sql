CREATE TABLE IF NOT EXISTS lg_pod_execucoes (
 id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
 portal_user VARCHAR(100) NOT NULL,
 transportador VARCHAR(40) NOT NULL,
 solicitado_por VARCHAR(100) NOT NULL,
 data_inicial DATE NOT NULL,
 data_final DATE NOT NULL,
 simulacao TINYINT(1) NOT NULL,
 status VARCHAR(30) NOT NULL DEFAULT 'AGUARDANDO',
 mensagem VARCHAR(1000) NOT NULL DEFAULT '',
 iniciado_em DATETIME NOT NULL,
 finalizado_em DATETIME NULL,
 INDEX idx_lg_exec_data (iniciado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS lg_pod_itens (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 execucao_id CHAR(36) CHARACTER SET ascii NOT NULL,
 portal_user VARCHAR(100) NOT NULL,
 transportador VARCHAR(40) NOT NULL,
 pod_id VARCHAR(40) NOT NULL,
 arquivo VARCHAR(255) NOT NULL,
 nf VARCHAR(40) NOT NULL DEFAULT '',
 serie VARCHAR(20) NOT NULL DEFAULT '',
 chave_nota VARCHAR(200) NOT NULL DEFAULT '',
 status_portal VARCHAR(40) NOT NULL DEFAULT '',
 etapa VARCHAR(40) NOT NULL,
 mensagem VARCHAR(1000) NOT NULL DEFAULT '',
 atualizado_em DATETIME NOT NULL,
 UNIQUE KEY uk_lg_exec_pod (execucao_id, pod_id),
 INDEX idx_lg_pod_historico (portal_user, transportador, pod_id, id),
 CONSTRAINT fk_lg_pod_exec FOREIGN KEY (execucao_id) REFERENCES lg_pod_execucoes(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
