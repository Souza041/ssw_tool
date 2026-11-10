# Sprint Health 02B — A4, A6 e integração OC73

**Estado:** A6 corrigido localmente para o flag; A4 pendente de banco descartável; integração dos entrypoints OC73 interrompida por divergência operacional comprovada. Nenhuma conexão externa, migration, OC73, commit, push ou deploy foi executado.

## A4 — laboratório

O repositório documenta `automationx` em Percona/MySQL `5.7.32-35-log`; `requirements.txt` fixa `PyMySQL==1.1.1`, também instalado localmente. O núcleo usa JSON, `DATETIME(6)`, FK InnoDB e `INSERT ... ON DUPLICATE KEY UPDATE`; a compatibilidade concreta com MySQL 5.7/MariaDB continua dependente de execução isolada.

`docker version` falhou porque `docker` não está disponível no PATH. `Get-Command mysql,mysqld,mariadb,podman` não encontrou executáveis. Os documentos de inventário Windows mencionam um **candidato não comprovado** em loopback, sem prova de isolamento ou autorização para conexão; ele não foi usado. Nenhum banco descartável foi preparado nesta sessão. Os três testes opt-in de `tests/test_health_center_mysql_integration.py` permaneceram `skipped`, antes de qualquer conexão.

Para fechar A4: instalar/iniciar um runtime de contêiner autorizado ou provisionar MySQL 5.7.32/MariaDB descartável em loopback; criar um schema `hc_disposable_*` e conta de teste exclusivos; confirmar nome/host/credenciais no ambiente de teste; então executar somente `python -m pytest -q tests/test_health_center_mysql_integration.py`. A fixture exige `HEALTH_TEST_DB_DISPOSABLE=YES_I_CONFIRM_DISPOSABLE`, `HEALTH_TEST_DB_CONFIRM_NAME` igual ao nome do schema e host `localhost`, `127.0.0.1` ou `::1`. Ela não lê `AUTOMATION_DB_*` nem `.env`. Registrar versão via consulta **apenas nesse laboratório** e resultados de migration, concorrência, idempotência, rollback e fechamento. Não extrapolar o resultado de MariaDB para Percona 5.7.

## A6 — precedência do flag

Política única: **o valor de `HEALTH_CENTER_ENABLED` já presente no ambiente do processo antes do bootstrap tem prioridade**. Ausente ou inválido significa `false`; somente `1`, `true`, `yes`, `sim` e `on` habilitam o monitor. O FastAPI continua carregando `.env` com `override=False` (`web/session_config.py`), mas restaura apenas o flag Health após essa carga. A fábrica Health também usa `load_dotenv` sem override. O script manual `scripts/testar_ocorrencia_73.py` continua carregando `.env` com `override=True` para **todas as variáveis operacionais OC73**, mas agora restaura apenas o valor original de `HEALTH_CENTER_ENABLED` após essa carga. Se o flag não existia no ambiente externo, remove o valor eventualmente trazido do arquivo, preservando o padrão desligado. Nenhum outro gate foi alterado.

Testes com `.env` **sintético em memória**, sem ler o arquivo real, cobrem ausente, inválido, `true` e `false` em ambos os bootstraps, além de confirmar que `OCORRENCIA_73_ENABLED` ainda recebe o override histórico. O monitor continua fazendo bypass antes de construir o repositório quando desligado. A suíte de sessão web permanece aprovada.

## Conflito com a instrumentação original

O commit `bc1d00d` envolvia `Ocorrencia73Service.executar(triggered_by="cron")` em `scripts/testar_ocorrencia_73.py` e `executar(triggered_by="scheduler")` em `modules/ocorrencia_73/scheduler.py`. O checkout atual difere materialmente:

| Entrada atual | Contrato operacional que precisa ser preservado |
| --- | --- |
| `scripts/testar_ocorrencia_73.py` | CLI **manual**, exige exatamente um `--ctrc`, ajusta CTRC e limite antes de importar o serviço, verifica dry-run/envio/autorização manual e chama `executar(triggered_by="homologacao_manual")`. Não é mais o cron genérico do commit. |
| `modules/ocorrencia_73/scheduler.py` | Reserva transacional por fluxo/data antes do serviço, usa lock, chama `executar(data_referencia=..., triggered_by=reserva["triggered_by"], fluxo=..., execucao_id_reservada=...)`, retorna o resultado ou `None` e mantém o tratamento de exceções. Possui rechecagem BIG independente. |

Aplicar literalmente o diff antigo removeria os parâmetros atuais e/ou deslocaria a observação para antes da reserva, mudando regras de idempotência e fluxo. Por instrução desta Sprint, **nenhum dos dois caminhos de execução foi instrumentado**. O scheduler não foi modificado. Os cinco testes de entrypoint recuperados do commit antigo continuam falhando porque esperam a API antiga do script/scheduler; três testes puros do classificador passaram.

### Diff mínimo proposto para revisão — **não aplicado**

Para o scheduler, após a reserva bem-sucedida, substituir apenas a expressão de chamada ao serviço; manter lock, gates, argumentos, `try/except`, retorno e rechecagem:

```diff
 from modules.ocorrencia_73.service import Ocorrencia73Service
+from modules.health_center.monitor import run_monitored
+from modules.health_center.ocorrencia_73 import BOT, classify_result
 ...
-        resultado = service.executar(
-            data_referencia=data_referencia,
-            triggered_by=reserva["triggered_by"],
-            fluxo=fluxo,
-            execucao_id_reservada=reserva["execucao_id"],
+        resultado = run_monitored(
+            bot=BOT,
+            origin=reserva["triggered_by"],
+            operation=lambda: service.executar(
+                data_referencia=data_referencia,
+                triggered_by=reserva["triggered_by"],
+                fluxo=fluxo,
+                execucao_id_reservada=reserva["execucao_id"],
+            ),
+            classify=classify_result,
         )
```

Para o script manual, decidir antes se invocações de homologação devem entrar no Health Center. Se sim, o wrapper deve ficar **após todos os gates manuais** e conservar `triggered_by="homologacao_manual"`, usando `origin="homologacao_manual"`; não usar `origin="cron"` para um fluxo manual. Essa decisão de escopo e a revisão do diff do scheduler são pré-condições para aplicar a integração.

## Testes e próximos gates

Suíte isolada de núcleo, privacidade, configuração, sessão web, scheduler OC73 e regressões selecionadas: **94 passed, 3 skipped, 5 deselected, 2 warnings, 14 subtests passed**. Os warnings são deprecações preexistentes do `on_event` FastAPI. Os três skips são MySQL opt-in sem laboratório; os cinco deselecionados são os entrypoints antigos. Executados separadamente, os cinco entrypoints deram **5 failed** por incompatibilidade com o contrato atual. `git diff --check` passou; o checkout continua com alterações locais não commitadas, inclusive artefatos anteriores à Sprint 02B.

Ativação na VPS exige: A4 aprovado em banco descartável compatível; A6 validado no bootstrap efetivo de cron/web; decisão explícita sobre monitorar o fluxo manual; diff do scheduler e testes atualizados aprovados; migration manual/backup; autorização separada para ligar o flag. Até lá, `HEALTH_CENTER_ENABLED` permanece desativado.
