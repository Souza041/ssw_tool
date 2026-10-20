# OC73 — Matriz de falhas transacionais (Sprint 3.15.2)

Esta matriz registra cenários do Modo A offline. “POST observado” significa
somente invocação de callback falso; nenhum transporte ou endpoint SSW foi usado.
O estado inicial padrão é tentativa `reservado` e autorização `pendente`.

| # | Cenário/ação | Estado final esperado | Callback / POST falso | Resultado | Evidência offline |
|---:|---|---|---:|---|---|
| 1 | Dois processos consomem a mesma autorização | uma `consumida`; tentativa `envio_iniciado` | 0 neste teste de commit | **APROVADO** | `test_mesma_autorizacao_em_dois_processos_tem_um_commit` |
| 2 | Segunda autorização para a mesma tentativa | primeira preservada; segunda não inserida | 0 | **APROVADO** | `test_duas_autorizacoes_diferentes_para_mesma_tentativa_sao_bloqueadas` |
| 3 | Segunda tentativa/ID para o mesmo CTRC | reserva original preservada | 0 | **APROVADO** | `test_dois_ids_de_tentativa_para_mesmo_ctrc_sao_bloqueados` |
| 4 | Autorização expirada | inalterado | 0 | **APROVADO** | `test_autorizacao_expirada_falha_fechado` |
| 5 | Autorização revogada | `revogada`; tentativa `reservado` | 0 | **APROVADO** | `test_autorizacao_revogada_falha_fechado` |
| 6 | Referência divergente | inalterado | 0 | **APROVADO** | parametrização `referencia` em `test_referencia_ou_revisao_divergente_bloqueia` |
| 7 | Revisão de código divergente | inalterado | 0 | **APROVADO** | parametrização `revisao_codigo` no mesmo teste |
| 8 | Mesma conta nos dois papéis | inalterado | 0 | **APROVADO** | `test_mesma_conta_nos_dois_papeis_bloqueia` |
| 9 | Mesma pessoa em contas distintas | inalterado | 0 | **APROVADO** | `test_mesma_pessoa_com_contas_diferentes_bloqueia` |
| 10 | Identidade não autenticada | inalterado | 0 | **APROVADO** | `test_identidade_nao_autenticada_bloqueia` |
| 11 | Papel insuficiente | inalterado | 0 | **APROVADO** | `test_papel_insuficiente_bloqueia` |
| 12 | Falha ao inserir auditoria | rollback integral | 0 | **APROVADO** | `test_falha_de_auditoria_reverte_toda_transacao` |
| 13 | Falha antes do commit | rollback integral | 0 | **APROVADO** | caso `antes_commit` do teste parametrizado de falhas |
| 14 | Rollback preserva snapshot | snapshot byte a byte lógico inalterado | 0 | **APROVADO** | asserção do teste parametrizado e de auditoria |
| 15 | Deadlock injetado | sem commit e sem retry automático | 0 | **APROVADO** | caso `deadlock` |
| 16 | Timeout de lock injetado | sem commit e sem retry automático | 0 | **APROVADO** | caso `timeout_trava` |
| 17 | Perda de conexão injetada | sem commit e sem retry automático | 0 | **APROVADO** | caso `conexao_perdida` |
| 18 | Commit ambíguo, servidor não confirmou | estado anterior; callback proibido | 0 | **APROVADO** | `test_commit_ambiguo_sem_confirmacao_nao_invoca_callback` |
| 19 | Commit ambíguo, servidor pode ter confirmado | `consumida` + `envio_iniciado`; callback e repetição proibidos | 0 | **APROVADO** | `test_commit_ambiguo_possivelmente_confirmado_nao_invoca_nem_reabre` |
| 20 | Queda após commit e antes do POST | `consumida` + `envio_iniciado`; repetição bloqueada | 0 | **APROVADO** | `test_queda_apos_commit_antes_do_post_bloqueia_repeticao` |
| 21 | Timeout no callback falso | tentativa `incerto`; autorização consumida | 1 | **APROVADO** | `test_timeout_do_post_falso_marca_incerto_e_nao_reenvia` |
| 22 | Nova chamada após `incerto` | `incerto`, sem alteração | permanece 1 | **APROVADO** | segunda chamada do teste anterior |
| 23 | Callback normal | callback vê commit já persistido | 1 | **APROVADO** | `test_callback_so_recebe_permissao_depois_do_commit` |
| 24 | Importação acidental de runtime/rede/MySQL | nenhuma importação proibida | 0 | **APROVADO** | `test_prototipo_permanece_isolado_de_runtime_rede_e_mysql` |
| 25 | Modo B sem flag ou apontando para `automationx` | falha antes de conexão | 0 | **APROVADO** | `test_modo_mysql_recusa_sem_flag_e_fora_da_allowlist_literal` |

## Interpretação correta

- Os resultados **APROVADO** provam somente o comportamento do modelo fake.
- O cenário multiprocessos prova a exclusão do adaptador temporário de teste no
  Windows; não prova locks, rollback ou durabilidade InnoDB.
- Deadlock, timeout, conexão perdida e commit ambíguo são falhas injetadas, não
  eventos observados em MySQL 5.7 real.
- Callback falso não é POST real, não valida a rede do SSW e não comprova a
  persistência da referência na OP101.
- Modo B, DDL experimental e concorrência MySQL continuam **PENDENTES DE
  VALIDAÇÃO EM LABORATÓRIO**.

## Critério conservador comum

Qualquer falha sem certeza de commit ou de efeito externo impede novo callback
automático. Estados `envio_iniciado`, `incerto` e `confirmado` são terminais para
o disparo automático. Recuperação é consultiva e humana; não consiste em apagar,
retroceder ou recriar reserva/autorização.

