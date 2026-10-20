# OC73 — Arquitetura de autorização transacional (Sprint 3.15.2)

Data da revisão: 2026-10-16. Escopo: arquitetura e protótipo offline. Nenhuma
integração operacional, migration, conexão MySQL ou chamada ao SSW foi feita.

## 1. Parecer executivo

| Objeto | Parecer | Fundamentação |
|---|---|---|
| Arquitetura proposta | **ADEQUADA COM PRÉ-CONDIÇÕES** | Consumo da autorização, auditoria e transição `reservado -> envio_iniciado` ficam no mesmo commit; o callback externo só pode ser obtido depois do commit confirmado. |
| Protótipo Modo A | **GO PARA EXPERIMENTAÇÃO OFFLINE** | Os invariantes e falhas conservadoras são exercitados por testes, inclusive disputa entre dois processos. |
| Planejamento de integração | **NO-GO** | Não há provedor corporativo/imutável de identidade e papéis comprovado, nem DDL aplicado e validado em laboratório MySQL. |
| Homologação real | **NO-GO** | Esta sprint não autoriza efeito externo, e a camada não está integrada ao runtime. |
| Produção automática | **NO-GO** | Além dos bloqueios acima, falta prova real da preservação integral da referência no histórico OP101. |

A transação MySQL não é atômica com o POST ao SSW. O desenho reduz a janela ao
estado conservador `envio_iniciado`; em perda de processo, timeout ou resposta
ambígua, a tentativa não é reaberta nem reenviada automaticamente.

## 2. Evidências examinadas

- `web/routes.py`: o login cria `SSWClient`, executa login/menu no SSW e guarda
  apenas um identificador de sessão no cookie assinado.
- `web/session_store.py`: cliente SSW e instante de criação residem em memória do
  processo; expiração local é de oito horas; logout remove o cliente e limpa a
  sessão.
- `web/session_config.py`: chave de sessão externalizada e validada, mas isso
  autentica a integridade do cookie, não atribui papéis corporativos.
- `web/main.py`: `SessionMiddleware` define chave e duração, porém não explicita
  `https_only=True`; a proteção efetiva de transporte/cookie depende da
  configuração externa e deve ser comprovada antes de reutilizar a sessão em
  qualquer fluxo sensível.
- `modules/ocorrencia_73/routes.py`: observabilidade exige sessão SSW presente e
  reavalia a allowlist a cada requisição.
- `modules/ocorrencia_73/observabilidade.py`: a allowlist é exata, normalizada e
  fail-closed quando vazia; ela concede apenas leitura do painel.
- `modules/ocorrencia_73/repository.py`: a referência é criada na reserva e
  persistida antes de `iniciar_envio`; a transição atual ocorre em transação
  separada e não contém aprovação humana.
- `modules/ocorrencia_73/gate.py`: só chama o callback após `iniciar_envio`; estados
  existentes ou inseguros bloqueiam nova tentativa.
- `prototypes/oc73_homologacao_supervisionada.py`: prova anterior de token e uso
  único local; não prova identidade corporativa nem atomicidade multi-host.

## 3. Diagnóstico de identidade, papéis e auditoria

| Propriedade | Classificação | Diagnóstico |
|---|---|---|
| Login SSW efetivamente tentado | **COMPROVADA NO CÓDIGO** | O formulário cria um cliente com as credenciais informadas e chama `login()` e `open_menu()`. |
| Sessão SSW local válida/expirada | **COMPROVADA NO CÓDIGO** | O identificador aponta para cliente em memória e expira localmente; reinício ou troca de processo perde a sessão. |
| Identidade corporativa imutável | **PENDENTE** | `client.usuario` é texto fornecido ao login; não foi encontrado `subject_id` corporativo assinado e estável. |
| Pessoa legal distinta por conta | **PENDENTE** | Não existe `person_id` emitido por IdP capaz de impedir duas contas da mesma pessoa. |
| Papel técnico | **PENDENTE** | Não existe RBAC de aprovação técnica. |
| Papel operacional | **PENDENTE** | Não existe RBAC de aprovação operacional. |
| Allowlist de observabilidade como aprovação | **NÃO CONFORME PARA ESTE USO** | A allowlist só autoriza visualização e não representa papel, independência ou aceite transacional. |
| Reutilização da sessão web como aceite | **NÃO CONFORME PARA ESTE USO** | Uma sessão SSW local não comprova reautenticação, papel nem intenção sobre uma tentativa específica. |
| Trilha atual de dois aceites | **PENDENTE** | Não há entidade operacional de aprovação humana. |
| Referência persistida antes do envio | **COMPROVADA NO CÓDIGO** | Reserva gera/persiste referência antes da transição de envio. |
| Erro de login sanitizado | **NÃO CONFORME PARA ALTA GARANTIA** | `login_run` entrega `str(exc)` ao template; a integração futura não pode propagar exceções do IdP/SSW. |

Conclusão: o runtime atual não pode ser usado para fabricar os dois aprovadores.
A integração deve receber identidades autenticadas de um provedor corporativo que
emita, no mínimo, `subject_id`, `person_id`, papéis e instante de autenticação. A
ausência desse provedor é bloqueio, não uma lacuna a preencher com strings do
formulário, variáveis de ambiente ou a allowlist do painel.

## 4. Contrato de autorização

Uma autorização é de uso único, com prazo máximo de 15 minutos, e vincula:

- `autorizacao_id` e `tentativa_id` exatos;
- série, número, `seq_ctrc`, família e local do CTRC;
- `REF:OC73-` seguida dos 20 hexadecimais maiúsculos persistidos;
- revisão exata do código;
- aprovador técnico e operacional autenticados;
- contas (`subject_id`) e pessoas (`person_id`) diferentes;
- papéis exatos `oc73_aprovador_tecnico` e
  `oc73_aprovador_operacional`;
- emissão, expiração, revogação, consumo e eventos de auditoria.

Qualquer divergência, expiração, revogação, ausência de auditoria ou falha de
identidade encerra o fluxo sem callback. Uma autorização consumida nunca volta a
`pendente`. Uma tentativa em `envio_iniciado`, `incerto` ou `confirmado` nunca é
automaticamente reenviada.

## 5. Modelo experimental MySQL 5.7/InnoDB

O DDL abaixo é uma **proposta auditável**, não uma migration e não foi executado.
Ele pressupõe que os tipos exatos de `oc73_tentativas.id` sejam confirmados antes
de criar a FK.

```sql
CREATE TABLE oc73_autorizacoes_homologacao (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  autorizacao_uuid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  tentativa_id BIGINT UNSIGNED NOT NULL,
  serie VARCHAR(10) NOT NULL,
  numero VARCHAR(32) NOT NULL,
  seq_ctrc VARCHAR(32) NOT NULL,
  familia VARCHAR(32) NOT NULL,
  local_ssw VARCHAR(10) NOT NULL,
  referencia VARCHAR(25) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  revisao_codigo VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  status ENUM('pendente','revogada','consumida') NOT NULL,
  emitida_em DATETIME(6) NOT NULL,
  expira_em DATETIME(6) NOT NULL,
  revogada_em DATETIME(6) NULL,
  consumida_em DATETIME(6) NULL,
  versao INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uq_oc73_auth_uuid (autorizacao_uuid),
  UNIQUE KEY uq_oc73_auth_tentativa (tentativa_id),
  KEY ix_oc73_auth_status_expira (status, expira_em),
  CONSTRAINT fk_oc73_auth_tentativa
    FOREIGN KEY (tentativa_id) REFERENCES oc73_tentativas (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE oc73_aprovacoes_homologacao (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  autorizacao_id BIGINT UNSIGNED NOT NULL,
  papel ENUM('tecnico','operacional') NOT NULL,
  subject_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  person_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  username_exibicao VARCHAR(191) NOT NULL,
  provedor VARCHAR(80) NOT NULL,
  autenticada_em DATETIME(6) NOT NULL,
  aprovada_em DATETIME(6) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_oc73_aprovacao_papel (autorizacao_id, papel),
  UNIQUE KEY uq_oc73_aprovacao_conta (autorizacao_id, subject_id),
  UNIQUE KEY uq_oc73_aprovacao_pessoa (autorizacao_id, person_id),
  CONSTRAINT fk_oc73_aprovacao_auth
    FOREIGN KEY (autorizacao_id)
    REFERENCES oc73_autorizacoes_homologacao (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE oc73_autorizacao_auditoria (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  autorizacao_id BIGINT UNSIGNED NOT NULL,
  tentativa_id BIGINT UNSIGNED NOT NULL,
  evento VARCHAR(64) NOT NULL,
  ocorrido_em DATETIME(6) NOT NULL,
  detalhe_saneado VARCHAR(255) NULL,
  PRIMARY KEY (id),
  KEY ix_oc73_auth_auditoria (autorizacao_id, id),
  CONSTRAINT fk_oc73_auditoria_auth
    FOREIGN KEY (autorizacao_id)
    REFERENCES oc73_autorizacoes_homologacao (id),
  CONSTRAINT fk_oc73_auditoria_tentativa
    FOREIGN KEY (tentativa_id) REFERENCES oc73_tentativas (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

MySQL 5.7 aceita mas ignora `CHECK`; por isso não se atribui a ele a garantia de
15 minutos ou de coerência temporal. Essas regras são revalidadas na transação e
nos testes, enquanto `ENUM`, `NOT NULL`, `UNIQUE` e FKs InnoDB sustentam parte das
invariantes. Referência oficial: [MySQL 5.7 — ALTER TABLE e CHECK](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/alter-table.html).

Uma autorização por `tentativa_id` é deliberadamente conservadora. Revogação não
libera uma nova linha; qualquer novo ciclo exigiria decisão explícita e novo
desenho auditado, nunca atualização improvisada.

## 6. Transação crítica proposta

Todos os caminhos que alterem tentativa/autorização devem obter locks na mesma
ordem: **tentativa por PK -> autorização por PK -> aprovações por
`(autorizacao_id, papel)`**.

```sql
START TRANSACTION;

SELECT id, serie, numero, referencia, status
FROM oc73_tentativas
WHERE id = %s
FOR UPDATE;

SELECT id, tentativa_id, referencia, revisao_codigo, status,
       emitida_em, expira_em, versao
FROM oc73_autorizacoes_homologacao
WHERE id = %s AND tentativa_id = %s
FOR UPDATE;

SELECT papel, subject_id, person_id, provedor, autenticada_em
FROM oc73_aprovacoes_homologacao
WHERE autorizacao_id = %s
ORDER BY papel
FOR UPDATE;

-- aplicação valida estado, vínculo exato, prazo, papéis e independência

INSERT INTO oc73_autorizacao_auditoria (...)
VALUES (...);

UPDATE oc73_autorizacoes_homologacao
SET status = 'consumida', consumida_em = %s, versao = versao + 1
WHERE id = %s AND status = 'pendente' AND versao = %s;

UPDATE oc73_tentativas
SET status = 'envio_iniciado', envio_iniciado_em = %s
WHERE id = %s AND status = 'reservado' AND referencia = %s;

COMMIT;
```

Cada `UPDATE` deve afetar exatamente uma linha; caso contrário, `ROLLBACK`. O
callback só é entregue após retorno inequívoco do `COMMIT`. `SELECT ... FOR
UPDATE` exige transação/autocommit desabilitado e mantém os locks até commit ou
rollback, conforme o [manual MySQL 5.7](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/innodb-locking-reads.html).

Deadlock, timeout de lock, perda de conexão e commit ambíguo **não têm retry
automático neste portão**. Embora o manual recomende que aplicações em geral
sejam capazes de repetir transações vítimas de deadlock, aqui a política mais
restritiva exige nova avaliação humana para evitar que incerteza seja convertida
em POST. Timeout de lock pode reverter só a instrução, conforme configuração;
portanto o cliente deve executar `ROLLBACK` defensivo e descartar a conexão.
Referência: [tratamento de erros InnoDB](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/innodb-error-handling.html).

## 7. Fronteira transacional e estados de recuperação

1. Antes do commit: nenhum callback; qualquer erro provoca rollback e fechamento.
2. Commit confirmado: autorização está `consumida` e tentativa está
   `envio_iniciado`; somente então o chamador pode solicitar um POST.
3. Commit ambíguo: nenhum callback, mesmo que o commit talvez tenha ocorrido;
   consulta humana posterior decide o estado, sem reenvio.
4. Queda após commit e antes do POST: permanece `envio_iniciado`; não há reenvio.
5. Timeout/falha após entrar no transporte: marcar `incerto` quando possível; se
   isso falhar, preservar `envio_iniciado`; ambos bloqueiam nova tentativa.
6. HTTP positivo não confirma. Somente ocorrência 73 com referência exata no
   histórico OP101 autoriza `confirmado`.

Não existe two-phase commit com o SSW. Um outbox não elimina a incerteza do POST;
se um worker automático consumir o outbox novamente, recria exatamente o risco de
duplicidade que esta arquitetura proíbe.

## 8. Modos experimentais

### Modo A — executado

`prototypes/oc73_autorizacao_transacional.py` usa um snapshot único sob lock e
troca atômica do snapshot como ponto de commit modelado. Os testes usam estado em
memória e, no cenário multiprocessos, arquivo temporário com lock do SO. Não há
rede, banco, importação do runtime ou transporte SSW.

Limites: o fake não prova isolamento, durabilidade, recuperação, deadlock ou
semântica de commit de MySQL real. Falhas são injeções determinísticas.

### Modo B — não executado e pendente

O protótipo contém apenas a validação preventiva da allowlist literal:
`127.0.0.1:3308`, schema `oc73_autorizacao_transacional_test`, usuário
`oc73_tester`, flag explícita e UUID esperado não vazio. Isso **não verifica o
servidor** e não abre conexão.

Antes de qualquer futura criação de tabela de laboratório será necessário:

1. autorização específica da execução e do DDL exato;
2. confirmar endpoint, porta, usuário efetivo, `DATABASE()`, versão,
   `@@server_uuid`, `@@datadir` e privilégios;
3. rejeitar socket local, schema diferente e especialmente `automationx`;
4. comprovar que o serviço e o schema isolados já existem; o teste não os cria;
5. executar concorrência real, quedas e rollback; destruir apenas artefatos do
   schema de laboratório dentro da autorização recebida.

Como essa autorização/verificação não ocorreu nesta sprint, concorrência MySQL
real permanece **PENDENTE DE VALIDAÇÃO EM LABORATÓRIO**.

## 9. Modelo de ameaças

| Ameaça | Controle proposto | Risco residual |
|---|---|---|
| Reuso da mesma autorização | `UNIQUE tentativa_id`, status consumido e update condicional | Operador com DML poderia adulterar estado; privilégios mínimos são necessários. |
| Dois processos simultâneos | locks de linha por PK, ordem determinística e updates condicionais | Pendente de prova MySQL real. |
| Duas contas da mesma pessoa | `person_id` imutável e UNIQUE por autorização | Depende da qualidade do IdP; hoje inexistente. |
| Roubo/replay de sessão | autorização curta e vinculada; reautenticação/IdP | Sessão web atual não basta e está em memória. |
| Aprovação de CTRC/referência errados | snapshot exato e comparação sob lock | Dados de origem ainda exigem revisão humana. |
| Alteração de código entre aprovação e envio | revisão vinculada | Estratégia de revisão/deploy precisa ser definida. |
| Falha da auditoria | auditoria no mesmo commit; falha fecha | Disponibilidade da tabela de auditoria afeta homologação. |
| Commit ambíguo | proibição absoluta de callback/retry | Pode deixar autorização consumida sem POST; requer intervenção. |
| POST aceito com timeout | estado `incerto`, reconciliação consultiva | SSW não oferece transação distribuída/idempotency key comprovada. |
| Insider/credencial DB com escrita | segregação de funções e privilégios mínimos | Não resolvido pelo protótipo. |
| Vazamento de dados na trilha | eventos saneados, IDs técnicos, sem segredo/cookie | Política de retenção e acesso ainda deve ser aprovada. |

## 10. Alterações e isolamento

- Criado `prototypes/oc73_autorizacao_transacional.py`.
- Criado `tests/test_oc73_autorizacao_transacional_prototipo.py`.
- Criados este documento e a matriz de falhas associada.
- Nenhum arquivo de runtime, gate, scheduler, OP101, `.env` ou migration foi
  alterado para esta arquitetura.

## 11. Bloqueios para qualquer integração

- provedor corporativo que comprove identidade e `person_id` distintos;
- definição/aprovação dos dois papéis e segregação de funções;
- revisão de privacidade, retenção e acesso à auditoria;
- revisão e aplicação autorizada do DDL somente em laboratório;
- testes MySQL 5.7 reais de concorrência, deadlock, timeout, perda de conexão e
  commit ambíguo;
- desenho operacional para commit ambíguo e processo interrompido;
- nova revisão de segurança da integração exata no gate;
- autorização separada para homologação real e prova OP101 da referência integral.

