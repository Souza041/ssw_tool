# Nexus Health Center — implementação consolidada e auditoria final local

**Data:** 2026-11-10. **Branch:** `feat/nexus-health-center-integration`.
**Parecer:** interface e núcleo implementados para revisão local; **ativação e consulta ao banco operacional BLOQUEADAS**. O Health Center permanece desativado por padrão. Nenhum bot, portal, banco operacional, migration operacional, envio, deploy, push ou commit foi executado nesta entrega.

## 1. Arquitetura implementada

`modules/health_center/monitor.py` observa apenas a chamada agendada da OC73, após a reserva existente em `modules/ocorrencia_73/scheduler.py:137`. Com `HEALTH_CENTER_ENABLED` ausente ou falso, `run_monitored()` chama a operação uma vez e não constrói repositório Health. Com o flag ligado, registra `RUNNING` antes do serviço e tenta finalizar com `SUCCESS`, `PARTIAL` ou `FAILED`; falhas de observabilidade são isoladas e não repetem a operação. O scheduler conserva os quatro argumentos de `Ocorrencia73Service.executar()`, o retorno e seu `except Exception` original. O CLI manual `scripts/testar_ocorrencia_73.py` não foi instrumentado.

`database.py` usa uma conexão e uma transação por escrita, com timeouts próprios de conexão, leitura e gravação, inicialmente 2 s cada, sem retry. Leituras usam `START TRANSACTION READ ONLY`, rollback e close. `repository.py:100-196` faz cadastro e início idempotente por UUID, com finalização condicional `WHERE status = 'RUNNING'`. `suspicious_running()` em `repository.py:213` apenas sinaliza `RUNNING` antigo; não altera o status persistido. O limiar padrão é 120 minutos e deve ser calibrado com durações reais.

`privacy.py` permite somente contadores e `dry_run` no JSON, limita bytes/tipos e rejeita texto livre sensível. A leitura administrativa em `repository.py:29` usa uma allowlist de campos e redige resumo, erro e metadados legados fora do contrato. Não expõe payload, conteúdo de relatório, log bruto, usuário responsável ou credenciais. A classificação OC73 considera erros técnicos, estados incertos e bloqueios mesmo quando o serviço retorna `success=True`; contadores por etapa que se sobrepõem não são somados duas vezes.

`health_bots` é catálogo; `enabled` na tabela não prova que o bot opera. Somente o identificador OC73 é marcado como instrumentado no código atual; observação exige uma execução registrada. Agendamento fica `schedule_verified=false` até verificação independente, e `next_execution=null`. O painel não cadastra outras automações automaticamente. O histórico MySQL Health e as métricas SQLite de `web/automation_metrics.py`/`web/jobs.py` permanecem fontes distintas; a página não soma suas populações.

## 2. Arquivos criados e alterados

| Arquivo | Estado e finalidade |
| --- | --- |
| `modules/health_center/repository.py` | Núcleo recuperado; ampliado com visão geral, catálogo, atividade por bot, histórico paginado, detalhe e allowlist de leitura. |
| `modules/health_center/routes.py` | Router administrativo ampliado com visão geral, detalhe por bot/execução, filtros e página HTML. |
| `modules/health_center/monitor.py`, `database.py`, `models.py`, `privacy.py`, `ocorrencia_73.py`, `migrations/001_create_health_tables.sql` | Núcleo recuperado e hardening local das entregas anteriores; SQL continua apenas proposto. |
| `modules/ocorrencia_73/scheduler.py` | Instrumentação local da execução agendada, já presente no checkout no início desta entrega; chamada operacional preservada. |
| `web/routes.py` | Página administrativa existente recebe contexto Health e filtros próprios. |
| `web/templates/admin_observabilidade.html` | Seção Health responsiva integrada ao painel de jobs existente, com estados e fontes separados. |
| `web/templates/health_bot_detail.html`, `health_execution_detail.html` | Detalhes administrativos por automação e execução. |
| `tests/test_health_center_api.py` | Novos testes sintéticos para autorização, leituras, filtros, paginação, estados, templates e privacidade. |
| `tests/test_health_center_hardening.py` | Contrato de rotas/catálogo atualizado para as novas leituras. |
| `web/main.py`, `web/session_config.py`, `scripts/testar_ocorrencia_73.py` e outros arquivos não rastreados | Alterações locais anteriores preservadas. Nenhum gate operacional, cron, Supervisor ou `.env` foi alterado nesta entrega. |

## 3. Rotas e matriz de permissões

| Rota GET | Conteúdo | Sem sessão | Sessão não ADMIN | ADMIN com flag desligado | ADMIN com flag ligado |
| --- | --- | --- | --- | --- | --- |
| `/admin/observabilidade` | Jobs SQLite + seção Health | redireciona ao login | 403 | página Health desativada, sem MySQL Health | página com leituras Health ou estado de erro |
| `/admin/health-center/status` | Flag e estado não verificado | 401 | 403 | 200, sem MySQL | 200, sem afirmar disponibilidade |
| `/admin/health-center/overview` | Agregados Health | 401 | 403 | 503, sem MySQL | 200 ou 503 sanitizado |
| `/admin/health-center/bots` e `/bots/{bot_id}` | Catálogo e detalhe por bot | 401 | 403 | 503, sem MySQL | 200/404 ou 503 |
| `/admin/health-center/bots/{bot_id}/view` | Página de detalhe por bot | 401 | 403 | 503, sem MySQL | HTML protegido |
| `/admin/health-center/executions` e `/executions/{execution_id}` | Histórico e detalhe | 401 | 403 | 503, sem MySQL | 200/404 ou 503 |
| `/admin/health-center/executions/{execution_id}/view` | Página de detalhe por execução | 401 | 403 | 503, sem MySQL | HTML protegido |
| `/admin/health-center/suspicious-running` | Suspeitas somente leitura | 401 | 403 | 503, sem MySQL | 200 ou 503 |

Todas as rotas Health usam `web/admin_access.py` no backend e somente GET. `executions` valida bot, status e período de 1–90 dias; página 1–1000, tamanho 1–100 e deslocamento máximo de 100.000. A página usa 20 registros por página. Identificadores e valores de filtro são parâmetros SQL. Não há endpoint de início, interrupção, retry, limpeza ou escrita. Evidência: `modules/health_center/routes.py:21-193`, `repository.py:407-468`.

## 4. Achados e riscos por severidade

| Severidade | Achado e evidência | Ação/estado |
| --- | --- | --- |
| **Bloqueador para produção** | A4: nenhum Docker, Podman, MySQL ou MariaDB local foi encontrado no PATH. Os três testes opt-in de `tests/test_health_center_mysql_integration.py` foram ignorados. DDL e SQL real não foram validados em Percona/MySQL 5.7. | Provisionar banco descartável comprovado e executar apenas ali a migration e testes; manter o flag desligado e não aplicar SQL na VPS. |
| **Alto para regressão geral** | Oito testes OC73 falham por expectativas divergentes das regras atuais. Detalhe na seção 6. `parser.py`, `calendario.py` e `observabilidade.py` não têm mudanças locais. As mesmas oito falhas ocorreram antes da implementação desta entrega. | Revisar a decisão de negócio e atualizar testes com justificativa separada; não enfraquecer regras para obter verde. |
| **Médio** | As consultas `overview()` e `catalog()` usam subconsulta de última execução por bot; os agregados de 24 h e `MAX(finished_at_utc)` podem varrer muitas linhas. O esquema tem índices por bot+início e status+início, mas não por início/fim globais (`repository.py:258-405`, SQL da migration). | Medir planos e latência somente em banco descartável/consulta autorizada; limitar uso antes de ativar. Os timeouts de socket não garantem teto rígido de custo ou duração total. |
| **Médio** | Autorização ADMIN depende de sessão SSW em memória e allowlist `NEXUS_ADMIN_USERS` (`web/admin_access.py`). O teste HTTP ASGI de ponta a ponta travou no ambiente Windows; os handlers, declarações FastAPI, templates e autorização foram testados offline diretamente. | Homologar login e papel efetivos no ambiente isolado antes de expor rotas em produção. |
| **Médio** | Apenas o scheduler OC73 está instrumentado. Jobs web seguem em SQLite, LG POD e demais bots não possuem registros Health. Nenhuma fonte confiável confirma o agendamento real na VPS. | Manter rótulos de fonte e `schedule_verified=false`; não inferir disponibilidade ou sucesso de ausência de registros. |
| **Baixo** | O uso preexistente de `TemplateResponse(name, context)` em `web/routes.py` e `on_event` em `web/main.py` gera avisos de depreciação. | Atualizar em revisão própria; não altera o monitoramento local. |

## 5. Estado dos achados A2–A6

| Achado | Estado local | Limite de evidência |
| --- | --- | --- |
| A2 — `RUNNING` antigo | Implementado: consulta read-only e estado suspeito, sem transição automática. | Limiar ainda sem calibração com duração real. |
| A3 — privacidade | Allowlist de escrita/leitura, limites e redação de legado testados offline. | Revisão de dados reais deve ocorrer só com autorização e processo seguro. |
| A4 — MySQL/MariaDB | Testes opt-in preparados; **pendente**. | Sem banco descartável/versão compatível disponível, 3 skips. |
| A5 — cadastro versus operação | Separado: registrado, instrumentado, observado e agendamento verificado são campos distintos. | Verificação real de scheduler/cron na VPS ainda pendente. |
| A6 — flag | Ambiente do processo tem precedência, valor ausente/inválido desativa. Bootstraps cron/web testados com `.env` sintético. | Conferir ambiente efetivo de cada processo antes de ativar na VPS. |

## 6. Análise das oito falhas OC73

| Grupo | Testes e expectativa | Contrato executável atual | Classificação |
| --- | --- | --- | --- |
| BIG (5) | `test_ocorrencia_73_parser.py:13` e `test_ocorrencia_73_novas_regras.py:30,39,45,52` esperam Palhoça/SC ou apenas UF+OC64; algumas fixtures têm receptora CWB. | `parser.py:389-394,493-500` exige **destinatário Florianópolis**, **receptora BIG** e OC64. O teste de cliente divergente também usa Palhoça e não isola a regra de cliente. | Expectativas/fixtures incompatíveis com a regra atual; nenhum indício de regressão causada pelo Health Center. Requer confirmação de negócio antes de corrigir testes. |
| Janela de emissão (2) | `test_oc73_preparacao_lotes.py:200,236` espera data inicial 12/10 e 10/10. | `calendario.py:47-56` usa três dias anteriores ao fim para emissão OP455: 09/10 e 08/10; a janela de ocorrência permanece separada. | Testes desatualizados ante o contrato atual; não alterar filtro de datas sem decisão operacional. |
| Próximo horário (1) | `test_oc73_observabilidade.py:130` espera 23h30 no dia seguinte. | `calendario.py:14-15,23-32` define 23h55; em 15/10 às 23h31, o próximo CWB é no mesmo dia às 23h55. | Expectativa antiga. Não alterar agendamento para satisfazer teste. |

Resultado ampliado: **786 passed, 8 failed, 3 skipped, 1 warning, 125 subtests passed**. Nenhum dos oito testes foi deselecionado ou alterado nesta entrega. A suíte ampliada excluiu explicitamente `test_oc73_mysql_real.py`, que exigiria laboratório MySQL e é fora do escopo offline.

## 7. Testes locais e compatibilidade

- Suíte Health Center, sessão web, scheduler OC73 e integração OC73 com fakes: **142 passed, 3 skipped, 3 warnings, 11 subtests passed**. Os três skips são MySQL descartável opt-in. Nenhum teste conectou ao banco operacional ou acessou o SSW.
- Testes de API verificam autorização em todos os handlers, flag desligado sem construção do repositório, erro de banco sanitizado, filtros e paginação parametrizados, estados do catálogo, execução suspeita, templates com dados sintéticos e redação de dados sensíveis. Um teste chama a rota Nexus existente com snapshot falso.
- `git diff --check`: aprovado. Python local: 3.12.10; PyMySQL instalado: 1.1.1. A migration propõe InnoDB, JSON, `DATETIME(6)`, FK e UUID `CHAR(36) ascii_bin`; o uso real com Percona/MySQL 5.7 continua **não comprovado** sem A4.
- Teste de transporte HTTP ASGI com `TestClient` foi interrompido após bloquear no primeiro request neste ambiente. A cobertura atual é de handlers/contratos/templates, não de servidor HTTP completo.

## 8. Homologação local pendente e ativação controlada

1. Revisar o diff e resolver a decisão de negócio dos oito testes OC73, sem executar bot. Confirmar a política de papéis/allowlist ADMIN com sessão sintética ou ambiente isolado autorizado.
2. Provisionar MySQL/Percona 5.7 descartável em loopback, schema `hc_disposable_*` e conta exclusiva. Confirmar explicitamente o alvo e a natureza descartável antes de qualquer DDL. A fixture exige `HEALTH_TEST_DB_DISPOSABLE=YES_I_CONFIRM_DISPOSABLE`, `HEALTH_TEST_DB_CONFIRM_NAME` igual ao schema e host loopback; nunca usa `AUTOMATION_DB_*` operacional. Executar `python -m pytest -q tests/test_health_center_mysql_integration.py` apenas nesse laboratório. Registrar versão, DDL efetivo, idempotência, concorrência, commit, rollback e fechamento.
3. Medir as consultas de visão geral, catálogo, histórico e detalhe em banco descartável com volumes sintéticos. Revisar índices/planos e tempo total; não inferir baixo custo a partir de `LIMIT` nem dos três timeouts de 2 s.
4. Homologar a página e a API em servidor web **isolado, sem scheduler operacional**, com dados sintéticos e sessão ADMIN falsa/aprovada. Verificar 401/403, 503 com flag desligado, paginação, estados e responsividade. Nenhum dado de produção deve ser usado na demonstração.
5. Para uma futura ativação na VPS, exigir aprovação separada: backup e schema identificados; migration manual revisada e aplicada por responsável autorizado; A4 aprovado; papéis ADMIN confirmados; flag efetivo em web/scheduler conferido; orçamento de latência e volume aprovados; plano de observação e janela de rollback. Só então considerar ligar `HEALTH_CENTER_ENABLED` em ambiente controlado. Esta entrega **não** autoriza esses passos.

## 9. Rollback

Antes de ativação, manter o flag ausente/`false`; as rotas de leitura não acessam MySQL Health e a OC73 continua executando com os mesmos argumentos. Em ativação futura autorizada, o primeiro rollback é desabilitar `HEALTH_CENTER_ENABLED` no ambiente efetivo dos processos e confirmar o bypass do monitor. Se o código precisar ser revertido, remover a inclusão do router Health, a seção de template e o wrapper `run_monitored()` do scheduler, restaurando a chamada direta de quatro argumentos. Preservar as tabelas para auditoria; nenhum `DROP` automático integra o rollback. Qualquer alteração na VPS exige autorização própria.
