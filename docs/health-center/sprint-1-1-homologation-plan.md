# Sprint 1.1 — plano de homologação do SSW Health Center

**Estado:** proposta para revisão; nenhuma etapa de integração ou migration foi executada nesta entrega.  
**Base analisada:** `bc1d00d` na branch `feat/ssw-health-center`.  
**Escopo:** apenas o núcleo `modules/health_center/` e a integração passiva dos dois entrypoints da OC73. O Health Center permanece desligado por padrão. Não executar a OC73 real em nenhum cenário abaixo.

## 1. Constatações e pontos a comprovar

| Tema | Evidência no código | Verificação necessária |
| --- | --- | --- |
| DDL e integridade | `migrations/001_create_health_tables.sql` cria duas tabelas InnoDB, FK `bot_id`, UUID textual `CHAR(36)` com `ascii_bin`, `DATETIME(6)`, índices e colunas `JSON`. | Aplicar o DDL **somente** em instância descartável; conferir `SHOW CREATE TABLE`, FK, índices, charset/collation, precisão temporal e modo SQL. `CREATE TABLE IF NOT EXISTS` permite repetição, mas pode ocultar uma tabela preexistente com estrutura divergente: comparar o esquema efetivo com o arquivo. |
| SQL de escrita | `repository.py` usa `ON DUPLICATE KEY UPDATE` no cadastro e na abertura, e finaliza com `UPDATE ... WHERE id = ? AND status = 'RUNNING'`. | Confirmar atomicidade, idempotência e resultado sob concorrência; não inferir sucesso de `rowcount` da abertura. Testar conflitos de UUID e de bot separadamente. |
| Motores | MySQL 8 possui tipo `JSON` nativo; MariaDB trata `JSON` como alias de `LONGTEXT` com validação. `VALUES(col)` no upsert funciona nos dois, mas é depreciado no MySQL desde 8.0.20. | Fixar versão e imagem de cada motor no registro da execução. Aprovar a versão do motor efetivamente usada na VPS antes de interpretar o resultado de compatibilidade; não assumir equivalência de armazenamento ou replicação JSON entre motores. |
| Observabilidade desligada | `monitor.py` retorna `operation()` antes de construir o repositório quando `HEALTH_CENTER_ENABLED` não é verdadeiro; `.env.example` declara `false`. | Com flag ausente/`false`, provar zero abertura de conexão e execução única do callback com mesmo retorno/erro. |
| Latência e falhas | `database.py` usa os três timeouts independentes de socket, padrão 2 s, uma conexão por transação, rollback e fechamento; `monitor.py` ignora falhas de observabilidade. | Medir abertura, escrita, commit e fechamento sob falhas separadas. Os três timeouts **não** formam um teto rígido de latência total: há transações de início e fim, múltiplas operações e possíveis esperas do servidor. |
| Configuração | `scripts/testar_ocorrencia_73.py` chama `load_dotenv(..., override=True)`; o scheduler não faz esse override. | Simular, sem importar nem iniciar o serviço real, as combinações ambiente/arquivo temporário. Documentar o valor efetivo da flag em cada caminho antes de qualquer ativação. |
| Execuções antigas | `repository.stale_running()` só consulta linhas antigas; não há reconciliação automática. | Confirmar que consulta, reinício de processo e falha de finalização não mudam `RUNNING` para `SUCCESS` ou `FAILED`. Exigir análise manual com evidência externa antes de qualquer futura política de reconciliação. |
| Dados sensíveis | O adaptador OC73 persiste somente contadores e `dry_run`; o núcleo aceita `parameters`, `metadata`, `summary` e `error_message` fornecidos por outros chamadores. | Inspecionar o que realmente foi persistido e logado com sentinelas sintéticas. Definir allowlist e limites antes da expansão para outros bots. |

Referências oficiais para a matriz de compatibilidade: [MySQL JSON](https://dev.mysql.com/doc/refman/8.0/en/json.html), [MariaDB JSON](https://mariadb.com/docs/server/reference/data-types/string-data-types/json), [MySQL `ON DUPLICATE KEY UPDATE`](https://dev.mysql.com/doc/refman/8.0/en/insert-on-duplicate.html), [MariaDB `ON DUPLICATE KEY UPDATE`](https://mariadb.com/docs/server/reference/sql-statements/data-manipulation/inserting-loading-data/insert-on-duplicate-key-update), [MySQL FK](https://dev.mysql.com/doc/refman/8.0/en/create-table-foreign-keys.html) e [MySQL `DATETIME(6)`](https://dev.mysql.com/doc/refman/8.0/en/date-and-time-type-syntax.html). A compatibilidade do DDL concreto continua pendente de execução isolada.

## 2. Ambiente descartável e isolamento

**Pré-condições para a próxima entrega, sujeitas a aprovação:** runtime de contêiner local; imagens com versões fixas (preferencialmente digest registrado) de MySQL 8.0 e MariaDB 10.11; versão adicional idêntica à da VPS após confirmação do inventário operacional. Não baixar dados, dump, credenciais ou relatórios de produção. Não usar `database/automation.py`, `.env` do projeto, SSW, SMTP, Playwright, cron nem scheduler reais.

1. Criar um contêiner por motor com nome único, porta publicada somente em `127.0.0.1` e volume **efêmero**. Usar banco, usuário e senha exclusivos de teste, gerados para a sessão. Identificar o recurso como `health-center-homologation` antes de criá-lo; registrar ID, imagem/digest, versão do servidor, porta, `sql_mode`, timezone e collation. Confirmar que host e banco não correspondem a `AUTOMATION_DB_*` de produção.
2. Injetar `AUTOMATION_DB_*` e `HEALTH_DB_*_TIMEOUT` somente no processo dos testes de integração. Manter `HEALTH_CENTER_ENABLED=false`. Um fixture deve rejeitar host que não seja `127.0.0.1`, nome de banco fora do padrão de teste e contêiner sem o identificador acima. Não carregar `.env` real; usar um arquivo temporário sintético somente no teste de precedência de configuração.
3. Aplicar `001_create_health_tables.sql` **somente dentro do banco descartável** pela preparação do teste, depois de validar o alvo. Reaplicar uma vez para testar ausência de erro; comparar `SHOW CREATE TABLE` e `information_schema` após cada aplicação. Não executar DDL em qualquer conexão compartilhada. Registrar `SHOW WARNINGS` quando houver.
4. Executar uma suíte de integração nova, isolada dos testes operacionais, usando `HealthRepository`/`run_monitored` com callbacks sintéticos e IDs aleatórios. Nenhum teste importa `Ocorrencia73Service`. Cada teste deve consultar o banco em conexão nova para provar persistência, isolamento e rollback reais. O nome exato do arquivo de testes será definido na implementação da suíte; ela ainda não existe.
5. Encerrar e remover apenas o contêiner e o volume identificados no passo 1, conferindo o ID antes da remoção. Guardar somente resultados sem segredos: versão, parâmetros do servidor, duração agregada, contagem de linhas, status dos testes e diferenças de DDL. Confirmar que não restaram recursos de teste.

O mesmo roteiro roda separadamente em cada motor. Se a versão da VPS não for MySQL 8.0 nem MariaDB 10.11, acrescentar essa versão à matriz antes do parecer de compatibilidade; não extrapolar a partir de outra versão.

## 3. Casos de teste de integração

| Caso | Procedimento com dados sintéticos | Aceite |
| --- | --- | --- |
| Esquema e tipos | Inspecionar ambas as tabelas, FK, índices, `JSON`, `DATETIME(6)`, `CHAR(36) ascii_bin`; inserir nomes Unicode, JSON válido e um JSON inválido diretamente no banco de teste. | DDL aplica sem adaptação; JSON inválido é rejeitado; FK impede execução com bot inexistente; microsegundos e UUID textual fazem round-trip; segunda aplicação do DDL não altera estrutura. |
| Commit e rollback | Abrir execução e ler de conexão independente; finalizar e reler; em transação isolada, provocar erro após upsert do bot e antes de confirmar execução; provocar erro na finalização antes de commit. | Apenas commits confirmados aparecem na nova conexão; rollback remove todas as escritas da respectiva transação; nenhuma conexão fica com transação aberta após saída do contexto. |
| UUID e duplicidade | Repetir `start` com mesmo UUID e dados divergentes; abrir duas execuções com UUIDs diferentes para o mesmo bot; testar dois `start` concorrentes com o mesmo UUID. | Existe uma linha por UUID; duplicata não reabre nem sobrescreve execução finalizada; FK e catálogo ficam consistentes; eventual deadlock/timeout do teste é registrado, sem retry implícito do monitor. |
| Finalização concorrente | Duas conexões tentam finalizar o mesmo UUID `RUNNING` com resultados diferentes, sincronizadas por barreira. Repetir `finish` após estado terminal. | Exatamente uma transição terminal é confirmada e a outra retorna `False`; dados da vencedora permanecem íntegros. Testar `SUCCESS`, `FAILED` e `PARTIAL`. |
| Interrupção | Confirmar `RUNNING`, simular término abrupto do processo sintético antes de `finish`, consultar `stale_running` com corte UTC anterior/posterior. | Linha permanece `RUNNING`; consulta é somente leitura, respeita limite/ordem e não conclui execução. Estado deve ser investigado manualmente usando logs/correlação externos. |
| Commit ambíguo | Simular perda de resposta após commit no caminho de início e de fim; conferir banco por UUID em nova conexão. | Callback operacional é chamado uma única vez. O monitor não repete a operação nem transforma ausência de confirmação em sucesso/falha presumidos; o registro pode permanecer `RUNNING` ou faltar, e o relatório descreve essa incerteza. |
| Falha de observabilidade | Isolar conexão recusada, DNS/porta inválidos locais, read/write timeout, falha de commit, rollback e close; usar callback que retorna valor e callback que lança exceção sentinela. | Retorno, exceção e código de saída previstos pelo entrypoint são preservados em testes com serviço substituído; nenhuma segunda execução do callback; falhas de banco não vazam credenciais em logs. |
| Banco lento | Introduzir atraso controlado em proxy local ou stub de socket nos pontos de conexão, leitura e escrita; medir duração com relógio monotônico e separar início/fim do monitor. | Durações e contribuição de cada fase são registradas; não declarar limite rígido de 2, 4 ou 6 s. Se o orçamento operacional a ser acordado for excedido, manter ativação bloqueada e propor monitoramento assíncrono/fila local em decisão arquitetural posterior. |
| Flag desligada | Flag ausente, `false` e valores não verdadeiros; injetar fábrica de conexão que falha se chamada. | Zero chamadas à fábrica; callback uma vez; retorno/exceção preservados. |
| Precedência cron/scheduler | Em subprocessos de teste, usar somente ambiente e arquivo temporário sintéticos com valores opostos e iguais da flag; simular o `load_dotenv(..., override=True)` do cron e a leitura direta do scheduler. | Matriz documenta valor efetivo em ambos os caminhos. Nenhum entrypoint, scheduler ou serviço real é iniciado. Divergência deve ser resolvida antes de habilitar produção, por decisão explícita de configuração. |
| Privacidade e tamanho | Inserir somente sentinelas (`token_TESTE`, `cpf_TESTE`, `senha_TESTE`) em campos livres de um chamador sintético; verificar tabelas e logs capturados. Testar objetos JSON grandes, strings nos limites das colunas e contadores fora do intervalo. | Adaptador OC73 persiste apenas allowlist de contadores e `dry_run`; credenciais/identificadores não chegam ao banco ou logs no cenário OC73. Para uso genérico, qualquer persistência de sentinela, truncamento inesperado ou tamanho sem limite mantém expansão bloqueada até definição e teste de política central. |

As falhas de conexão/commit não permitem sempre distinguir escrita confirmada de escrita perdida. A validação por UUID após o incidente é investigação, não autorização para refazer a automação.

## 4. Política de registros `RUNNING` e dados

- Considerar `RUNNING` antigo um estado **indeterminado**. Expor somente consulta por corte UTC e correlação; não usar timeout de idade para marcar `SUCCESS`, `FAILED` ou `PARTIAL`. Registrar operador, horário, evidência externa e decisão separada caso alguma reconciliação manual seja proposta no futuro.
- Antes de adicionar outros bots, aprovar allowlist de chaves escalares para `parameters_json`/`metadata_json`, com limites em bytes UTF-8 e profundidade; proibir payload de relatório, credenciais, cookies, tokens, CPF, documentos e IDs de clientes. Revisar também `responsible_user`, `summary`, `error_message`, `log_ref` e mensagens de exceção. Os `VARCHAR(500)` atuais limitam caracteres no banco, mas não estabelecem uma política de privacidade nem limite de bytes para JSON.
- Proposta de teto para teste, ainda sem implementação: até 4 KiB serializados por JSON, profundidade 2, até 32 chaves; contadores inteiros não negativos dentro de `BIGINT UNSIGNED`; `summary`/`error_message`/`log_ref` até 500 caracteres, sem conteúdo sensível. Ajustar os números somente com decisão registrada e teste de rejeição/truncamento. O núcleo atual não garante esses tetos para todos os chamadores.

## 5. Critérios de aceite e decisão

Homologação **aprovada para a etapa seguinte** somente com evidência de:

1. Mesma suíte de integração passando em cada motor/versão exigido, com DDL efetivo, isolamento InnoDB, FK, JSON e concorrência comprovados; divergências documentadas e corrigidas antes de usar aquele motor.
2. Flag desligada sem conexão, execução única e preservação do resultado original em todas as falhas de observabilidade; nenhum teste operacional real executado.
3. Matriz de precedência cron/scheduler resolvida por configuração inequívoca antes de ativação; `RUNNING` antigo permanece sem reclassificação automática.
4. Orçamento de latência operacional definido pelo responsável e medições aceitas em falhas locais. Como a implementação é síncrona, um limite rígido não está demonstrado; se ele for requisito, elaborar proposta assíncrona separada antes de ativar.
5. Revisão de privacidade do adaptador OC73 aprovada e política central de metadados definida antes de expandir o núcleo a outros bots.
6. Registro de evidências sem dados reais ou segredos e confirmação de remoção dos recursos descartáveis.

Qualquer falha acima bloqueia a ativação correspondente. Este plano **não** autoriza produção: após a homologação, uma revisão separada deve aprovar eventual migration na VPS, configuração, deploy e ativação.

## 6. Rollback do ensaio

Se um teste falhar, interromper somente a suíte sintética, manter `HEALTH_CENTER_ENABLED=false`, guardar logs redigidos e inspecionar pelo UUID antes de qualquer repetição. Reverter qualquer alteração de código de teste em checkout local se ela for descartada. Remover exclusivamente o contêiner e volume identificados como pertencentes ao ensaio, após conferir seus IDs; nunca apontar comando de remoção para host, banco ou volume compartilhado. Como esta entrega cria apenas este documento, seu rollback local é retirar este arquivo. Não há migration nem mudança operacional a reverter agora.

## 7. Próxima decisão solicitada

Aprovar ou ajustar a matriz de motores/versões, a criação de testes de integração isolados e o orçamento de latência desejado. Somente depois implementar a infraestrutura de teste e executar o roteiro em banco descartável local; qualquer mudança estrutural no monitor ou ativação exige decisão própria.
