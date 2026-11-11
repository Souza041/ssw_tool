# Nexus Health Center — implementação consolidada e auditoria final local

**Data:** 2026-11-10. **Branch:** `feat/nexus-health-center-integration`.
**Parecer de fechamento local em 2026-11-10:** **APROVADO COM RESTRIÇÕES localmente; ativação na VPS BLOQUEADA**. A interface HTTP foi homologada em app FastAPI isolado, sem startup operacional, e as oito expectativas antigas da OC73 foram atualizadas com base no histórico Git. A4 continua pendente por falta de banco descartável comprovado. O Health Center permanece desativado por padrão. Nenhum bot, portal, banco operacional, migration, envio, deploy, push ou commit foi executado nesta entrega.

## 1. Arquitetura implementada

`modules/health_center/monitor.py` observa apenas a chamada agendada da OC73, após a reserva existente em `modules/ocorrencia_73/scheduler.py:137`. Com `HEALTH_CENTER_ENABLED` ausente ou falso, `run_monitored()` chama a operação uma vez e não constrói repositório Health. Com o flag ligado, registra `RUNNING` antes do serviço e tenta finalizar com `SUCCESS`, `PARTIAL` ou `FAILED`; falhas de observabilidade são isoladas e não repetem a operação. O scheduler conserva os quatro argumentos de `Ocorrencia73Service.executar()`, o retorno e seu `except Exception` original. O CLI manual `scripts/testar_ocorrencia_73.py` não foi instrumentado.

`database.py` usa uma conexão e uma transação por escrita, com timeouts próprios de conexão, leitura e gravação, inicialmente 2 s cada, sem retry. Leituras usam `START TRANSACTION READ ONLY`, rollback e close. `repository.py:100-196` faz cadastro e início idempotente por UUID, com finalização condicional `WHERE status = 'RUNNING'`. `suspicious_running()` em `repository.py:213` apenas sinaliza `RUNNING` antigo; não altera o status persistido. O limiar padrão é 120 minutos e deve ser calibrado com durações reais.

`privacy.py` permite somente contadores e `dry_run` no JSON, limita bytes/tipos e rejeita texto livre sensível. A leitura administrativa em `repository.py:29` usa uma allowlist de campos e redige resumo, erro e metadados legados fora do contrato. Não expõe payload, conteúdo de relatório, log bruto, usuário responsável ou credenciais. A classificação OC73 considera erros técnicos, estados incertos e bloqueios mesmo quando o serviço retorna `success=True`; contadores por etapa que se sobrepõem não são somados duas vezes.

`health_bots` é catálogo; `enabled` na tabela não prova que o bot opera. Somente o identificador OC73 é marcado como instrumentado no código atual; observação exige uma execução registrada. Agendamento fica `schedule_verified=false` até verificação independente, e `next_execution=null`. O painel não cadastra outras automações automaticamente. O histórico MySQL Health e as métricas SQLite de `web/automation_metrics.py`/`web/jobs.py` permanecem fontes distintas; a página não soma suas populações.

## 2. Arquivos criados e alterados

| Arquivo | Estado e finalidade |
| --- | --- |
| `modules/health_center/repository.py` | Núcleo recuperado; ampliado com visão geral, catálogo, atividade por bot, histórico paginado, detalhe e allowlist de leitura. |
| `modules/health_center/routes.py` | Router administrativo; a construção do repositório agora converte falha em 503 sanitizado. |
| `modules/health_center/monitor.py`, `database.py`, `models.py`, `privacy.py`, `ocorrencia_73.py`, `migrations/001_create_health_tables.sql` | Núcleo recuperado e hardening local das entregas anteriores; SQL continua apenas proposto. |
| `modules/ocorrencia_73/scheduler.py` | Instrumentação local da execução agendada, já presente no checkout no início desta entrega; chamada operacional preservada. |
| `web/routes.py` | Página administrativa existente recebe contexto Health e filtros próprios. |
| `web/templates/admin_observabilidade.html` | Seção Health responsiva integrada ao painel de jobs existente, com estados e fontes separados. |
| `web/templates/health_bot_detail.html`, `health_execution_detail.html` | Detalhes administrativos por automação e execução. |
| `web/static/nexus-dark-contrast.css` | Contraste de links nos detalhes Health em tema escuro, corrigido na homologação integrada. |
| `tests/test_health_center_api.py` | Novos testes sintéticos para autorização, leituras, filtros, paginação, estados, templates e privacidade. |
| `tests/test_health_center_http.py` | App FastAPI exclusivo de teste, com sessão sintética e sem eventos de startup, banco ou portais. |
| `tests/test_health_center_hardening.py` | Contrato de rotas/catálogo atualizado para as novas leituras. |
| `tests/fixtures/oc73/relatorio_teste.sswweb`, `tests/test_ocorrencia_73_novas_regras.py`, `tests/test_oc73_preparacao_lotes.py`, `tests/test_oc73_observabilidade.py` | Fixtures e expectativas atualizadas para Florianópolis/BIG/OC64, emissão D-3 e 23h55; casos negativos adicionais. Nenhum código operacional foi modificado. |
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
| **Resolvido localmente** | Oito testes OC73 divergiam das regras já presentes nos commits `3d4d585`, `d56a130`, `b36bf05` e `4df75d6`. | Fixtures e expectativas corrigidas com casos negativos; os quatro arquivos de regressão passaram (60 testes). Confirmar separadamente se a versão empresarial corresponde ao checkout antes de qualquer ativação. |
| **Médio** | As consultas `overview()` e `catalog()` usam subconsulta de última execução por bot; os agregados de 24 h e `MAX(finished_at_utc)` podem varrer muitas linhas. O esquema tem índices por bot+início e status+início, mas não por início/fim globais (`repository.py:258-405`, SQL da migration). | Medir planos e latência somente em banco descartável/consulta autorizada; limitar uso antes de ativar. Os timeouts de socket não garantem teto rígido de custo ou duração total. |
| **Médio** | Autorização ADMIN depende de sessão SSW em memória e allowlist `NEXUS_ADMIN_USERS` (`web/admin_access.py`). O HTTP passou com sessão sintética em app isolado, mas a sessão SSW efetiva na VPS não foi testada. | Verificar login e papel efetivos antes de expor rotas em produção. |
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

## 6. Análise e correção das oito falhas OC73

| Grupo | Testes e expectativa | Contrato executável atual | Classificação |
| --- | --- | --- | --- |
| BIG (5) | `test_ocorrencia_73_parser.py:13` e `test_ocorrencia_73_novas_regras.py:30,39,45,52` esperam Palhoça/SC ou apenas UF+OC64; algumas fixtures têm receptora CWB. | `parser.py:389-394,493-500` exige **destinatário Florianópolis**, **receptora BIG** e OC64. O teste de cliente divergente também usa Palhoça e não isola a regra de cliente. | Expectativas/fixtures incompatíveis com a regra atual; nenhum indício de regressão causada pelo Health Center. Requer confirmação de negócio antes de corrigir testes. |
| Janela de emissão (2) | `test_oc73_preparacao_lotes.py:200,236` espera data inicial 12/10 e 10/10. | `calendario.py:47-56` usa três dias anteriores ao fim para emissão OP455: 09/10 e 08/10; a janela de ocorrência permanece separada. | Testes desatualizados ante o contrato atual; não alterar filtro de datas sem decisão operacional. |
| Próximo horário (1) | `test_oc73_observabilidade.py:130` espera 23h30 no dia seguinte. | `calendario.py:14-15,23-32` define 23h55; em 15/10 às 23h31, o próximo CWB é no mesmo dia às 23h55. | Expectativa antiga. Não alterar agendamento para satisfazer teste. |

Os oito casos reproduziram a divergência inicial. O histórico Git e o código do checkout confirmaram as regras atuais; foram corrigidos **somente testes e fixture sintética**, com negativos adicionais para destino e ocorrência e assertivas separadas para emissão e janela da ocorrência. Os quatro arquivos antes divergentes passaram: **60 passed**. Regressão ampliada offline após a correção: **798 passed, 3 skipped, 1 warning, 125 subtests passed**. `test_oc73_mysql_real.py` ficou fora da execução por exigir laboratório MySQL.

## 7. Testes locais e compatibilidade

- Suíte Health Center, sessão web, scheduler OC73 e integração OC73 com fakes: **144 passed, 3 skipped, 3 warnings, 11 subtests passed** na etapa anterior; a regressão ampliada final está na seção 6. Os três skips são MySQL descartável opt-in. Nenhum teste conectou ao banco operacional ou acessou o SSW.
- App FastAPI mínimo de homologação HTTP, com sessão ADMIN sintética, sem `web.main`, startup ou scheduler: **3 passed** em `tests/test_health_center_http.py`. Cobriu todas as rotas protegidas (401/403), flag desligado sem repositório, página, filtros, paginação, detalhes, validação 422, vazio e 503 sanitizado.
- Testes de API verificam autorização em todos os handlers, flag desligado sem construção do repositório, erro de banco sanitizado, filtros e paginação parametrizados, estados do catálogo, execução suspeita, templates com dados sintéticos e redação de dados sensíveis. Um teste chama a rota Nexus existente com snapshot falso.
- `git diff --check`: aprovado. Python local: 3.12.10; PyMySQL instalado: 1.1.1. A migration propõe InnoDB, JSON, `DATETIME(6)`, FK e UUID `CHAR(36) ascii_bin`; o uso real com Percona/MySQL 5.7 continua **não comprovado** sem A4.
- A causa do bloqueio HTTP foi o canal interno de sockets do loop assíncrono Windows sob a sandbox: traceback em `socket._fallback_socketpair()` antes do handler. Fora dessa restrição, `TestClient` e `httpx.ASGITransport` retornaram 200 com app mínimo; não houve incompatibilidade demonstrada entre dependências. O app HTTP de testes também passou fora da sandbox. Nenhum servidor real foi iniciado.

## 8. Homologação local pendente e ativação controlada

1. Revisar o diff dos oito testes OC73 contra os commits que definiram o contrato atual. Confirmar que a versão empresarial da OC73 corresponde ao checkout; se divergir, suspender a reconciliação dos testes até sincronizar. Confirmar a política de papéis/allowlist ADMIN na VPS.
2. Provisionar MySQL/Percona 5.7 descartável em loopback, schema `hc_disposable_*` e conta exclusiva. Confirmar explicitamente o alvo e a natureza descartável antes de qualquer DDL. A fixture exige `HEALTH_TEST_DB_DISPOSABLE=YES_I_CONFIRM_DISPOSABLE`, `HEALTH_TEST_DB_CONFIRM_NAME` igual ao schema e host loopback; nunca usa `AUTOMATION_DB_*` operacional. Executar `python -m pytest -q tests/test_health_center_mysql_integration.py` apenas nesse laboratório. Registrar versão, DDL efetivo, idempotência, concorrência, commit, rollback e fechamento.
3. Medir as consultas de visão geral, catálogo, histórico e detalhe em banco descartável com volumes sintéticos. Revisar índices/planos e tempo total; não inferir baixo custo a partir de `LIMIT` nem dos três timeouts de 2 s.
4. O HTTP com app FastAPI isolado já passou; antes da VPS, ainda falta inspeção visual em navegador nos temas claro/escuro e tamanhos de tela, mais a sessão ADMIN efetiva em ambiente controlado. Não iniciar `web.main` sem neutralizar comprovadamente o startup do scheduler.
5. Para uma futura ativação na VPS, exigir aprovação separada: backup e schema identificados; migration manual revisada e aplicada por responsável autorizado; A4 aprovado; papéis ADMIN confirmados; flag efetivo em web/scheduler conferido; orçamento de latência e volume aprovados; plano de observação e janela de rollback. Só então considerar ligar `HEALTH_CENTER_ENABLED` em ambiente controlado. Esta entrega **não** autoriza esses passos.

## 9. Rollback

Antes de ativação, manter o flag ausente/`false`; as rotas de leitura não acessam MySQL Health e a OC73 continua executando com os mesmos argumentos. Em ativação futura autorizada, o primeiro rollback é desabilitar `HEALTH_CENTER_ENABLED` no ambiente efetivo dos processos e confirmar o bypass do monitor. Se o código precisar ser revertido, remover a inclusão do router Health, a seção de template e o wrapper `run_monitored()` do scheduler, restaurando a chamada direta de quatro argumentos. Preservar as tabelas para auditoria; nenhum `DROP` automático integra o rollback. Qualquer alteração na VPS exige autorização própria.

## 10. Homologação integrada local — Git e interface

`git fetch origin` concluiu. `origin/codex/ssw-tool-consolidado` aponta para `f9df542`, ancestral direto de `e8a50a8`; `HEAD..origin/codex/ssw-tool-consolidado` não contém commits. O diff `origin/codex/ssw-tool-consolidado..HEAD` adiciona o Health Center e altera somente o wrapper do scheduler no escopo OC73; não altera `parser.py`, `calendario.py`, `observabilidade.py`, `service.py` nem OP101. As mudanças recentes de OP101 e OC73 já estão no ancestral consolidado, inclusive `3d4d585` (BIG por Florianópolis e receptora BIG), `d56a130`/`b36bf05` (emissão D-3) e `4df75d6` (23h55). Nenhum merge foi realizado. Os diretórios locais não rastreados `oc73_big_d3/` e `oc73_instrucoes_manifesto/` foram preservados.

O `startup` de `web/main.py:119-134` chama o scheduler operacional da OC73. Por isso a aplicação real não foi iniciada. Um app FastAPI **somente de testes** registrou as rotas administrativas sem eventos de startup, com sessão e repositório sintéticos. Requisições HTTP verificaram `/admin/observabilidade`, detalhes por bot/execução, estados vazio/desativado/erro, filtros, paginação, 401/403/422/503 e sanitização. O modo desativado não constrói `HealthRepository` nem abre conexão Health. O teste encontrou uma falha real: `_repository()` deixava escapar erro de construção; `routes.py` agora o converte em 503 sem detalhes do driver. A execução OC73 continua única e conserva retorno/exceção quando início, término ou classificação do monitor falham.

**Achado visual corrigido:** `web/templates/admin_observabilidade.html:30-35` usava fundo branco e cores fixas que conflitavam com o tema escuro. A seção agora usa `--card`, `--text`, `--border`, `--bg` e `--muted` do Nexus, com contraste próprio para estados e links no escuro. `web/static/nexus-dark-contrast.css` cobre links dos detalhes. Templates e seletores foram verificados por testes sintéticos e inspeção estática; inspeção de pixels em navegador real ainda não foi feita. O transporte HTTP em memória foi homologado, sem servidor real.

## 11. Oito regressões OC73 — diagnóstico individual

| Falha | Evidência do teste | Regra atual e origem | Diagnóstico |
| --- | --- | --- | --- |
| `test_carregar_e_filtrar_relatorio` | Fixture sintética BIG usa Palhoça; espera um CTRC elegível. | `parser.py:389-394` exige Florianópolis e receptora BIG (`3d4d585`). | Teste desatualizado. |
| `test_regras_ocorrencia_73` | Candidato JOI/Palhoça/BIG/OC64 esperado. | Mesma regra Florianópolis/BIG/OC64. | Teste desatualizado. |
| `test_big_oc64_qualquer_emissora` | Candidatos Palhoça têm receptora CWB por padrão no helper. | Exige Florianópolis e receptora BIG. | Fixture não satisfaz dois predicados atuais. |
| `test_big_oc64_cliente_nao_permitido` | Usa Palhoça e receptora CWB para provar regra de cliente. | Falha já no destino; não isola a regra de cliente. | Teste inconclusivo para cliente e desatualizado para destino. |
| `test_big_depende_somente_de_uf_e_ocorrencia[SC-64-1]` | Espera elegibilidade por UF/OC64 com Palhoça e receptora CWB. | UF/OC64 isolados não bastam no contrato atual. | Teste desatualizado. |
| `test_segunda_gera_cwb_e_big` | Espera emissão inicial em 12/10/2026. | `calendario.py:47-56` usa 09/10/2026, D-3 (`d56a130`, `b36bf05`). | Expectativa antiga; janela de ocorrência é separada. |
| `test_domingo_consulta_sabado_e_domingo` | Espera emissão inicial em 10/10/2026. | Emissão usa 08/10/2026, D-3 do fim; ocorrência cobre sábado e domingo. | Expectativa antiga. |
| `test_proximas_execucoes_sao_teoricas_com_scheduler_desligado` | Em 15/10 às 23h31 espera próxima CWB em 16/10 às 23h30. | `calendario.py:14-15` agenda 23h55 (`4df75d6`); próxima é 15/10 às 23h55. | Expectativa antiga. |

Esses oito testes falharam antes da correção. O diff operacional em parser/calendário/OP101 contra a branch consolidada é vazio. As expectativas e fixtures sintéticas foram atualizadas com base nos commits citados; testes negativos adicionais preservam a verificação dos predicados de segurança. `parser.py`, `calendario.py`, `observabilidade.py`, `service.py` e o agendamento não foram modificados. Se a versão empresarial diferir da branch consolidada, a decisão sobre os testes deve ser reaberta antes de ativar.

## 12. Banco, pré-condições para VPS e conclusão

`docker`, `podman`, `mysql`, `mysqld` e `mariadb` não estão disponíveis no PATH. A consulta ao Service Control Manager foi negada; `wsl.exe` existe, mas `wsl --list --quiet` falhou sem identificar distribuição. Não há alvo local comprovadamente descartável nem conta exclusiva: **nenhuma migration foi executada** e os três testes MySQL opt-in ficaram `skipped`. Percona/MySQL 5.7 permanece compatibilidade estática proposta, sem validação real de DDL, concorrência, rollback ou custo das consultas novas.

Checklist obrigatório antes de qualquer ativação na VPS: (1) confirmar que a versão empresarial OC73 corresponde aos commits consolidados e revisar o diff dos testes; (2) homologar MySQL/Percona 5.7 descartável e registrar versão/DDL, concorrência, rollback e fechamento; (3) medir planos e latência das consultas Health; (4) inspecionar layout em navegador nos temas claro/escuro e verificar sessão ADMIN efetiva, sem scheduler operacional; (5) confirmar allowlist, flag efetivo e papéis no ambiente da VPS; (6) obter autorização separada para backup, migration manual, deploy e eventual ativação gradual. O rollback permanece o descrito na seção 9. **Conclusão: APROVADO COM RESTRIÇÕES para homologação local; BLOQUEADO para ativação operacional.**

## 13. Ambiente e causa do bloqueio de transporte HTTP

O comando `python` usa `C:\Users\55419\AppData\Local\Programs\Python\Python312\python.exe`, versão 3.12.10, sem `VIRTUAL_ENV` (`sys.prefix == sys.base_prefix`). Versões instaladas: FastAPI 0.115.12, Starlette 0.46.2, HTTPX 0.27.0 e AnyIO 4.4.0. Nenhum ambiente virtual foi criado porque a incompatibilidade de dependências não foi comprovada; nenhuma instalação global foi feita.

Um `TestClient` com uma única rota `/x` bloqueou dentro da sandbox. `faulthandler` mostrou a thread do AnyIO aguardando a criação do loop em `asyncio.windows_events`, `asyncio.proactor_events._make_self_pipe` e `socket._fallback_socketpair`; a thread principal aguardava `start_blocking_portal`. O mesmo app mínimo retornou **HTTP 200** fora da restrição de sockets da sandbox, tanto por `TestClient` quanto por `httpx.ASGITransport`. Isso atribui o bloqueio ao ambiente de execução local, não ao código Health ou a uma incompatibilidade demonstrada de versões.

`tests/test_health_center_http.py` usa apenas `FastAPI()`, `SessionMiddleware` com chave sintética, rotas administrativas e um endpoint de sessão exclusivo do teste. Não importa nem inicia `web.main`, não registra startup e substitui sessão SSW, SQLite e MySQL por fakes. Rodado fora da restrição de sockets: **3 passed**. A primeira execução revelou que o construtor de `HealthRepository` podia lançar exceção não convertida em 503. A correção localizada em `modules/health_center/routes.py:_repository()` foi verificada com timeout sintético e ausência da mensagem sensível na resposta. Nenhum servidor foi iniciado.
