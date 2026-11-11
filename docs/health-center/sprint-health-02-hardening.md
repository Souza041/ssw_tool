# Sprint Health 02 — hardening local e preparação do Nexus

**Estado: implementação local parcial; ativação bloqueada.** A auditoria Sprint 1 e a auditoria arquitetural foram lidas integralmente via `git show feat/ssw-health-center:docs/health-center/...`. O checkout atual continha o núcleo recuperado, mas não a instrumentação dos entrypoints OC73. Nenhuma conexão externa, bot, migration operacional, SMTP, SSW, commit ou deploy foi executado.

## Achados A2–A6

| Achado | Alteração e evidência | Estado |
| --- | --- | --- |
| A2 — `RUNNING` antigo | `HealthRepository.suspicious_running()` aplica limiar `HEALTH_RUNNING_SUSPECT_MINUTES` (padrão 120, 1–10080) sobre UTC; `stale_running()` usa transação `START TRANSACTION READ ONLY`. Retorna `SUSPECTED_INTERRUPTION` sem `UPDATE` ou inferência de resultado. | Implementado offline; duração real precisa de homologação antes de calibrar limiar. |
| A3 — dados livres | `privacy.py` permite somente contadores OC73 conhecidos e `dry_run` booleano em JSON (máximo 2048 bytes); rejeita chaves desconhecidas, strings, listas, negativos e contadores acima de 1 bilhão. `repository.py` valida parâmetros vazios, textos de catálogo limitados, resumo OC73 formatado ou mensagem estática de exceção, erro apenas por classe, usuário responsável e referência de log não permitidos nesta fase. | Implementado e testado com fakes. Não usar esses campos para detalhes operacionais. |
| A4 — InnoDB real | `tests/test_health_center_mysql_integration.py` contém migration repetida, verificação de schema, UUID concorrente, fechamento condicional, rollback e fechamento de conexões. Requer confirmação explícita, schema `hc_disposable_*` confirmado e host loopback; caso contrário faz skip/recusa antes de conectar. | **Pendente**: três testes ignorados; nenhum MySQL/MariaDB descartável foi disponibilizado ou acessado. |
| A5 — catálogo versus operação | Cadastro novo grava `enabled=0`; a coluna é somente de catálogo, não um gate de execução. Leitura administrativa retorna `operational_state=unverified` e `next_execution=null`; não usa `schedule_source` para inferir horário. | Separação implementada. Registros antigos com `enabled=1` não comprovam bot ativo. |
| A6 — origem do flag | O núcleo lê somente o ambiente efetivo do processo por `monitor.enabled()`, no momento da chamada; ausência/valor desconhecido significa `false`. A fábrica de banco só é alcançada após o gate. | Política do núcleo definida, mas **alinhamento de bootstrap pendente**: o cron atual usa `load_dotenv(..., override=True)` e o web possui carregamento próprio. Não ativar antes de demonstrar igualdade do ambiente efetivo nos dois processos. `.env` e scheduler não foram alterados. |

## API administrativa preparada

`modules/health_center/routes.py` registra `/admin/health-center/status`, `/bots`, `/executions` e `/suspicious-running`. Cada handler exige sessão e `web.admin_access.is_admin()` no backend. Com o flag desligado, apenas `/status` responde `enabled=false`; as demais rotas retornam 503 antes de construir o repositório. As consultas operacionais são `SELECT` com limite até 100 em transação read-only; respostas excluem JSON, logs, usuário responsável, `schedule_source` e detalhes de SSW. Erros de banco viram 503 sem mensagem do driver. O router é incluído em `web/main.py` apenas fora do modo QA. Nenhuma rota de escrita foi criada.

## Política de configuração e pré-condição A6

Fonte pretendida: `HEALTH_CENTER_ENABLED` no ambiente do processo, com padrão desativado. Os valores aceitos como verdadeiro continuam `1`, `true`, `yes`, `sim` e `on`, sem distinção de caixa. O núcleo não carrega `.env` para decidir o gate. Entretanto, o cron já carrega `.env` com `override=True` antes de qualquer futura chamada ao monitor; portanto, um valor nesse arquivo pode substituir o ambiente injetado. **A6 não está homologado entre processos.** Antes de conectar a instrumentação OC73, revisar e aprovar um ajuste de bootstrap que preserve o valor do flag do ambiente externo sem mudar os gates operacionais. Qualquer alteração no scheduler deve ter diff e justificativa aprovados primeiro.

## Homologação segura proposta

1. Revisar o diff local, a política de privacidade, a autenticação administrativa e a ausência de alterações nas regras OC73.
2. Preparar MySQL/MariaDB **descartável local**, com schema de nome `hc_disposable_*`, usuário exclusivo e backup/snapshot do laboratório. Fornecer `HEALTH_TEST_DB_DISPOSABLE=YES_I_CONFIRM_DISPOSABLE`, `HEALTH_TEST_DB_HOST=127.0.0.1`, `HEALTH_TEST_DB_NAME`, `HEALTH_TEST_DB_CONFIRM_NAME` idêntico ao nome, porta/usuário/senha de teste. Só então executar os testes opt-in. Não apontar `AUTOMATION_DB_*` do ambiente operacional para o laboratório.
3. Validar DDL, idempotência, FK/JSON, concorrência, `rowcount`, rollback e fechamento em MySQL 5.7 e MariaDB compatível. Se falhar, manter A4 bloqueado e não aplicar SQL na VPS.
4. Em processos locais separados, verificar qual valor efetivo de `HEALTH_CENTER_ENABLED` é observado pelo cron e pelo web quando a variável do ambiente difere do `.env`, sem executar OC73. Resolver A6 antes de integração.
5. Testar as rotas com sessão administrativa e não administrativa sintética, flag desligado e repositório falso. Homologar a autorização no backend antes de expor dados reais.

## Pré-condições para ativação na VPS

- A4 aprovado em banco descartável da família/versão relevante; migration revisada, backup e aplicação manual autorizada.
- A6 comprovado nos ambientes efetivos de cron e web; flag continua `false` até autorização de ativação separada.
- Autorização administrativa validada com sessão SSW e allowlist aprovada; nenhum endpoint público de detalhe/log.
- Limiar de `RUNNING` calibrado com duração observada e fuso UTC verificado.
- Retenção, backup, permissões, carga e timeouts do banco aprovados; a arquitetura síncrona não possui teto total rígido de latência.
- Instrumentação dos entrypoints OC73 revisada em diff próprio. O scheduler operacional não foi modificado nesta Sprint.

## Testes e riscos residuais

Testes isolados de núcleo, privacidade, API e regressões OC73 selecionadas: **61 passed, 3 skipped, 5 deselected, 14 subtests passed**. Os três skips são os testes MySQL opt-in; A4 permanece pendente. Os cinco testes deselecionados são os de entrypoint OC73. A suíte antiga completa `tests/test_health_center_ocorrencia_73.py` apresentou **5 falhas e 3 aprovações**: seus testes de entrypoint esperam instrumentação da OC73 que não existe neste checkout. Não se alterou cron/scheduler para mascarar a diferença.

O código ainda não prova disponibilidade, custo ou compatibilidade do banco real. O catálogo não prova habilitação da automação nem próximo disparo. O processo web e o cron podem divergir no flag até resolver A6. Não ativar `HEALTH_CENTER_ENABLED` nem aplicar a migration na VPS nesta etapa.
