# Diagnóstico MySQL operacional OC73 — somente leitura

Data: 16/10/2026

Script: `scripts/diagnostico_oc73_operacional_readonly.py`

> **Atualização Sprint 3.6.3:** os comandos históricos abaixo que não incluem
> `--apenas-schema` ou `--verificacao` agora falham antes de conectar. A execução
> operacional completa em sequência foi desativada. Cada agregação exige um
> identificador da allowlist e `--confirmar-agregacao`, executando somente uma
> verificação por processo. A sintaxe vigente e seus limites estão em
> [`oc73_sprint_3_6_3_diagnostico_fracionado.md`](oc73_sprint_3_6_3_diagnostico_fracionado.md).

O modo `--apenas-schema` preparado na Sprint 3.4 limita a coleta a identidade,
metadados e DDL, sem consultas agregadas às linhas operacionais. O procedimento
específico está em [`oc73_schema_audit_preparation.md`](oc73_schema_audit_preparation.md).
Nenhuma coleta operacional foi executada nessa sprint.

## Parecer final de prontidão

O código está **APTO COM RESTRIÇÕES para execução consultiva controlada**. Isso é
um parecer de prontidão técnica, não uma autorização de conexão. A execução em
produção continua bloqueada até haver aprovação humana do diff, aprovação
operacional separada, janela aprovada por DBA/operação e aceite formal dos riscos
residuais. Este documento não autoriza conexão com produção.

Para este ambiente, o responsável técnico autorizou uma exceção documentada ao
requisito de confirmação independente do `server_uuid`. A exceção, suas fontes,
limitações e riscos estão registrados em
[`oc73_excecao_identidade_mysql_2026-10-16.md`](oc73_excecao_identidade_mysql_2026-10-16.md).
Ela não autoriza a execução e não remove a comparação exata de UUID feita pelo
script.

A comparação completa da FK com o DDL oficial permanece inconclusiva enquanto a
baseline integral não for fornecida. Esse fato não é mascarado: aparece no JSON e
deve ser tratado como pendência no parecer produzido após a coleta.

Mocks demonstram o comportamento do cliente, não a segurança nem a identidade do
banco real.

### Identidade esperada para a futura execução

- endpoint configurado: `automationx.mysql.dbaas.com.br:3306`;
- schema efetivo: `automationx`;
- versão observada e aceita pelo validador: `5.7.32-35-log` (família 5.7.32);
- hostname interno observado: `myshared2014` (registrado no relatório, mas não
  usado isoladamente como prova de identidade);
- UUID esperado: `22516a2b-8484-11eb-8da3-566fe1410021`, sujeito à exceção
  documental de identidade e ainda comparado exatamente pelo script.

Nenhum desses valores autoriza conexão. Usuário e senha não são documentados.

## Modelo de segurança

- módulo independente; não importa aplicação web, serviço, scheduler, gate,
  repositório OC73, cliente SSW ou operações;
- importação não abre conexão e não executa o diagnóstico;
- lê somente `AUTOMATION_DB_HOST`, `AUTOMATION_DB_PORT`, `AUTOMATION_DB_NAME`,
  `AUTOMATION_DB_USER` e `AUTOMATION_DB_PASSWORD` do ambiente do processo;
- não carrega nem modifica `.env`;
- exige `--somente-leitura` e `--server-uuid-esperado`;
- exige `automationx`, porta efetiva igual à configurada, versão `5.7.32` e UUID
  exato antes de consultar metadados das tabelas;
- inicia `START TRANSACTION READ ONLY`, usa apenas o inventário fechado abaixo e
  sempre chama `rollback()` e `close()`;
- não habilita `CLIENT.MULTI_STATEMENTS`;
- não possui opção de SQL, tabela, filtro ou coluna informada pelo operador;
- nunca acessa `op535_cnpj_cache`;
- não retorna CTRCs, referências, nomes, documentos ou registros completos;
- limita grupos anormais a 101 internamente, reporta no máximo 100 e solicita ao
  servidor `MAX_EXECUTION_TIME(5000)` nas cinco consultas agregadas.

### Alcance do `MAX_EXECUTION_TIME(5000)` no MySQL 5.7.32

Segundo o manual oficial do MySQL 5.7, quando o hint aparece logo após o primeiro
`SELECT`, seu escopo é global para a instrução: portanto alcança a instrução
composta inteira, inclusive a subquery derivada. O hint se aplica a `SELECT` de
somente leitura.

Isso **não** deve ser interpretado como limite rígido de cinco segundos de tempo
de parede. É um timeout de execução solicitado ao servidor para aquela instrução;
não limita abertura da conexão, transporte de rede, serialização do resultado ou
as demais consultas do inventário. O cliente mantém ainda `read_timeout=15`, que
também não é um SLA de duração total. Timeout ou cancelamento é resultado
**inconclusivo**, nunca evidência de ausência de duplicidades.

Referência: [MySQL 5.7 — Optimizer Hints](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/optimizer-hints.html).

Como regra geral, o UUID esperado deve vir de CMDB, inventário do DBA ou evidência
independente previamente aprovada. A única exceção registrada para este ambiente
é a autorização técnica de 16/10/2026, que aceita o UUID observado no phpMyAdmin
sem tratá-lo como confirmação independente. O valor continua sendo fornecido ao
script antes da execução e comparado exatamente com `@@server_uuid`.

## Inventário exato de SQL

A fonte normativa é o mapping somente leitura `SQL_FIXO`. Não existe
função que execute texto SQL fornecido externamente.

### Controle transacional e identidade

```sql
START TRANSACTION READ ONLY
```

```sql
SELECT @@version AS versao, @@version_comment AS distribuicao,
       @@server_uuid AS server_uuid, @@hostname AS hostname,
       @@port AS porta, DATABASE() AS banco
```

### Metadados restritos às duas tabelas OC73

```sql
SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'automationx'
  AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY TABLE_NAME
```

```sql
SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE,
       IS_NULLABLE, COLUMN_DEFAULT, EXTRA, CHARACTER_SET_NAME,
       COLLATION_NAME, GENERATION_EXPRESSION
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'automationx'
  AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY TABLE_NAME, ORDINAL_POSITION
```

```sql
SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX,
       COLUMN_NAME, COLLATION, CARDINALITY, INDEX_TYPE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'automationx'
  AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
```

```sql
SELECT tc.TABLE_NAME, tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE,
       kcu.ORDINAL_POSITION, kcu.COLUMN_NAME,
       kcu.REFERENCED_TABLE_NAME, kcu.REFERENCED_COLUMN_NAME
FROM information_schema.TABLE_CONSTRAINTS AS tc
LEFT JOIN information_schema.KEY_COLUMN_USAGE AS kcu
  ON kcu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
 AND kcu.TABLE_NAME = tc.TABLE_NAME
 AND kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
WHERE tc.CONSTRAINT_SCHEMA = 'automationx'
  AND tc.TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
```

```sql
SELECT CONSTRAINT_NAME, TABLE_NAME, REFERENCED_TABLE_NAME,
       UPDATE_RULE, DELETE_RULE
FROM information_schema.REFERENTIAL_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA = 'automationx'
  AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY TABLE_NAME, CONSTRAINT_NAME
```

```sql
SHOW CREATE TABLE automationx.oc73_execucoes
```

```sql
SHOW CREATE TABLE automationx.oc73_tentativas
```

### Integridade agregada sem chaves operacionais na saída

```sql
SELECT /*+ MAX_EXECUTION_TIME(5000) */
       COUNT(*) AS grupos_ate_101,
       COALESCE(SUM(d.total - 1), 0) AS linhas_excedentes_ate_101
FROM (
  SELECT COUNT(*) AS total
  FROM automationx.oc73_execucoes
  WHERE triggered_by IN ('scheduler:CWB', 'scheduler:BIG')
  GROUP BY data_referencia, triggered_by
  HAVING COUNT(*) > 1
  LIMIT 101
) AS d
```

```sql
SELECT /*+ MAX_EXECUTION_TIME(5000) */
       COUNT(*) AS grupos_ate_101,
       COALESCE(SUM(d.total - 1), 0) AS linhas_excedentes_ate_101
FROM (
  SELECT COUNT(*) AS total
  FROM automationx.oc73_tentativas
  GROUP BY serie, numero, codigo_ocorrencia
  HAVING COUNT(*) > 1
  LIMIT 101
) AS d
```

```sql
SELECT /*+ MAX_EXECUTION_TIME(5000) */
       COUNT(*) AS grupos_ate_101,
       COALESCE(SUM(d.total - 1), 0) AS linhas_excedentes_ate_101
FROM (
  SELECT COUNT(*) AS total
  FROM automationx.oc73_tentativas
  WHERE referencia IS NOT NULL AND referencia <> ''
  GROUP BY referencia
  HAVING COUNT(*) > 1
  LIMIT 101
) AS d
```

```sql
SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) AS total_ate_101
FROM (
  SELECT t.id
  FROM automationx.oc73_tentativas AS t
  LEFT JOIN automationx.oc73_execucoes AS e ON e.id = t.execucao_id
  WHERE e.id IS NULL
  LIMIT 101
) AS o
```

```sql
SELECT /*+ MAX_EXECUTION_TIME(5000) */
       (SELECT COUNT(*)
          FROM automationx.oc73_execucoes
         WHERE status IS NULL
            OR BINARY status NOT IN
               ('executando', 'concluido',
                'concluido_com_pendencias', 'erro'))
         AS execucoes_status_invalido,
       (SELECT COUNT(*)
          FROM automationx.oc73_tentativas
         WHERE status IS NULL
            OR BINARY status NOT IN
               ('reservado', 'envio_iniciado', 'incerto', 'confirmado'))
         AS tentativas_status_invalido
```

Essa quinta consulta é a única inclusão SQL desta revisão. Ela retorna somente
duas contagens agregadas. Não retorna IDs, CTRCs, referências ou amostras de
status. Dependendo dos índices e da distribuição dos dados, pode varrer as duas
tabelas; o hint de 5 s é uma solicitação ao servidor, não um limite rígido.

Além dessas instruções, `connection.rollback()` é chamado defensivamente no
`finally`; o driver emite o controle transacional correspondente. Não há commit.

## Avaliação de conformidade do schema

O relatório avalia, sem alterar o banco:

- engine `InnoDB` nas duas tabelas;
- presença de todas as colunas consumidas pelo runtime, seus tipos compatíveis,
  capacidades mínimas e nulabilidade;
- chave primária simples `id` e `AUTO_INCREMENT` nas duas tabelas;
- compatibilidade de tipo base e atributo `UNSIGNED` entre
  `oc73_tentativas.execucao_id` e `oc73_execucoes.id`; a largura de exibição de
  inteiros do MySQL 5.7 não é tratada como diferença de tipo;
- UNIQUE de CTRC em `oc73_tentativas (serie, numero, codigo_ocorrencia)` na
  ordem indicada e com o nome `uq_oc73_ctrc` exigido pelo repository;
- UNIQUE de referência em `oc73_tentativas (referencia)` com o nome
  `uq_oc73_referencia` exigido pelo tratamento de colisão;
- relação mínima de FK
  `oc73_tentativas.execucao_id -> oc73_execucoes.id`, incluindo as regras
  `UPDATE_RULE` e `DELETE_RULE` encontradas;
- presença ou ausência de UNIQUE por fluxo/data em `oc73_execucoes`, aceitando a
  forma composta e detectando backstop baseado em coluna gerada.

O contrato estrutural exige, em resumo:

- `oc73_execucoes`: `id`, `data_referencia`, `triggered_by`, `status`, `dry_run`,
  `iniciado_em`, `finalizado_em`, `total_relatorio`, `total_filtrado`,
  `total_lancado`, `total_erros` e `mensagem_erro`;
- `oc73_tentativas`: `id`, `execucao_id`, `serie`, `numero`,
  `codigo_ocorrencia`, `fluxo`, `data_inicial_pesquisa`,
  `data_final_pesquisa`, `status`, `reservado_em`, `referencia`,
  `envio_iniciado_em`, `confirmado_em`, `resposta_ssw` e `mensagem_erro`;
- `referencia` com capacidade mínima de 25 caracteres e collation binária
  (`*_bin`), para que a unicidade preserve diferença de caixa;
- os totais omitidos pelo `INSERT` inicial devem aceitar `NULL` ou possuir
  `DEFAULT`; campos de conclusão/erro ainda não conhecidos devem aceitar `NULL`;
- domínio observado de execuções: `executando`, `concluido`,
  `concluido_com_pendencias`, `erro`; domínio de tentativas: `reservado`,
  `envio_iniciado`, `incerto`, `confirmado`.

Coluna obrigatória ausente, tipo/capacidade/nulabilidade incompatível, PK inválida,
`AUTO_INCREMENT` ausente, UNIQUE ausente, collation inadequada ou FK incompatível
produzem `nao_conforme`. Metadado necessário ausente ou ilegível produz
`inconclusivo`; nunca é convertido em conformidade.

Não foi localizado no repositório um DDL oficial completo das duas tabelas. Por
isso, a relação mínima da FK pode ser verificada, mas a comparação de nomes e das
regras `ON UPDATE`/`ON DELETE` com o “DDL oficial” permanece explicitamente
`inconclusivo`; o script não inventa essa baseline. A migration versionada de
referência comprova apenas `uq_oc73_referencia`.

`information_schema.TABLES.TABLE_ROWS` para InnoDB é uma estimativa aproximada,
não uma contagem exata. O JSON acrescenta `TABLE_ROWS_EXATO: false` e
`TABLE_ROWS_NATUREZA: estimativa_aproximada_innodb`. O manual alerta que essa
estimativa pode divergir significativamente da contagem real. Referência:
[MySQL 5.7 — The INFORMATION_SCHEMA TABLES Table](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/information-schema-tables-table.html).

## Semântica dos resultados de integridade

- `completo`: a consulta terminou e retornou menos de 101 ocorrências; a
  quantidade é exata dentro da consulta definida;
- `limitado`: o limite sentinela de 101 foi atingido; o relatório afirma apenas
  “pelo menos 101”, reporta no máximo 100 e deixa `quantidade_exata` como `null`;
- `inconclusivo`: ocorreu timeout/erro ou uma consulta deixou de ser executada
  depois do primeiro erro; `quantidade_exata` permanece `null`.

Assim, ausência de linha após falha nunca é convertida em zero duplicidades. As
consultas de integridade restantes deixam de ser executadas após o primeiro erro,
preservando o diagnóstico parcial sem aumentar carga ou criar conclusões falsas.

A consulta de domínios não usa o limite sentinela: quando termina, suas duas
contagens são completas para o snapshot da transação. Qualquer erro ou timeout
torna o item `integridade_estados` inconclusivo, com contagens `null`.

## Checklist para autorização futura

Finalidade exclusiva: confirmar, por metadados e contagens agregadas, se as duas
tabelas OC73 atendem ao contrato do runtime. Não é ferramenta de correção.

- [ ] diff exato do script e desta documentação revisado e aprovado;
- [ ] autorização de execução separada, com responsável e horário registrados;
- [ ] janela de baixa carga definida com DBA/operação e observação disponível;
- [ ] endpoint aprovado, porta `3306`, schema `automationx`, versão
  `5.7.32-35-log` e UUID esperado conferidos antes da conexão;
- [ ] usuário dedicado com `SELECT` e acesso de leitura a `information_schema`
  quando disponível; se for usada a conta atual com escrita, risco formalmente
  aceito e sessão exclusiva;
- [ ] credenciais fornecidas apenas ao ambiente do processo, sem linha de comando,
  log ou arquivo versionado;
- [ ] diretório de saída privado, com ACL/`umask 077`, retenção definida e canal
  seguro para revisão;
- [ ] critérios de interrupção abaixo aceitos sem retentativa automática.

Impacto estimado: consultas de `information_schema` e dois `SHOW CREATE TABLE`,
mais cinco agregações somente leitura. As agregações podem fazer scans e consumir
CPU/I/O; não há garantia de conclusão em 5 s. Executar somente na janela acordada.

Em divergência, preservar o JSON, interromper sem flexibilizar identidade ou
allowlist e encaminhar ao DBA/responsável da OC73. Correção de schema ou dados
exige plano, autorização e tarefa separados; este script nunca a realiza.

## Execução segura futura — Windows PowerShell

**NÃO EXECUTADO — DEPENDE DE AUTORIZAÇÃO.**

Pré-condições: diff aprovado, dependências instaladas, UUID aprovado conforme a
política normal ou a exceção registrada, autorização separada de execução e
janela autorizada. Usar uma sessão administrativa controlada e variáveis apenas
no processo; não colocar senha na linha de comando nem salvar no histórico.

```powershell
$env:AUTOMATION_DB_HOST = '<host-aprovado>'
$env:AUTOMATION_DB_PORT = '3306'
$env:AUTOMATION_DB_NAME = 'automationx'
$env:AUTOMATION_DB_USER = '<usuario-existente>'
$seguro = Read-Host 'Senha MySQL' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
try {
    $env:AUTOMATION_DB_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    python scripts/diagnostico_oc73_operacional_readonly.py `
      --somente-leitura `
      --server-uuid-esperado '<uuid-aprovado>'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Item Env:AUTOMATION_DB_PASSWORD -ErrorAction SilentlyContinue
}
```

O relatório JSON contém DDL e identidade operacional; se for salvo, aplicar ACL
restritiva e política de retenção. Não colar a saída em canais públicos.

## Execução segura futura — VPS Ubuntu

**NÃO EXECUTADO — DEPENDE DE AUTORIZAÇÃO.**

Não editar `.env` nem colocar senha no comando. Carregar host/porta/schema/usuário
do mecanismo aprovado e ler a senha silenciosamente para o ambiente do processo:

```bash
export AUTOMATION_DB_HOST='<host-aprovado>'
export AUTOMATION_DB_PORT='3306'
export AUTOMATION_DB_NAME='automationx'
export AUTOMATION_DB_USER='<usuario-existente>'
read -r -s -p 'Senha MySQL: ' AUTOMATION_DB_PASSWORD
export AUTOMATION_DB_PASSWORD
printf '\n'
umask 077
python scripts/diagnostico_oc73_operacional_readonly.py \
  --somente-leitura \
  --server-uuid-esperado '<uuid-aprovado>' \
  > oc73_diagnostico_$(date +%Y%m%dT%H%M%S).json
unset AUTOMATION_DB_PASSWORD
```

Antes da execução, confirmar que o Python/venv é o mesmo revisado. Depois,
proteger e transferir o JSON pelo canal operacional autorizado.

## Critérios de interrupção

Interromper sem nova tentativa se ocorrer qualquer um:

- schema, porta, versão ou UUID divergente;
- conjunto de tabelas diferente das duas esperadas;
- timeout, conexão perdida ou resposta de metadados incompleta;
- erro de `START TRANSACTION READ ONLY`;
- necessidade de flexibilizar a allowlist ou aumentar limites sem nova revisão;
- solicitação de consultar registros completos ou OP535.

## Riscos residuais

| Risco | Severidade | Tratamento |
|---|---:|---|
| A conta existente conserva grants de escrita | Alta | A garantia é do código/processo, não do servidor. Preferir conta dedicada somente leitura assim que disponível. |
| Código/ambiente diferente do revisado | Alta | Conferir revisão/hash e venv imediatamente antes da execução. |
| Consultas de duplicidade podem fazer varredura relevante | Média | O hint solicita timeout de 5 s para a instrução inteira, mas não é limite rígido ponta a ponta; manter janela aprovada e observação pelo DBA. |
| `information_schema` e DDL podem mudar durante a coleta | Média | Registrar horário e repetir somente com nova autorização se houver DDL concorrente. |
| DDL oficial completo não está versionado | Média | A relação mínima da FK é auditada; regras referenciais permanecem inconclusivas até o DBA fornecer a baseline oficial. |
| `TABLE_ROWS` InnoDB é estimado | Baixa | Rotular explicitamente como estimativa; não usar esse campo como contagem de integridade. |
| UUID não possui confirmação independente neste ambiente | Alta | Exceção de 16/10/2026 documenta a aceitação limitada; correspondência exata permanece obrigatória e execução requer autorização separada. |
| UUID muda após restore/clone legítimo | Média | Falhar fechado e exigir nova evidência ou nova decisão formal de exceção. |
| Saída contém DDL, hostname e UUID | Média | Arquivo com ACL/umask restritos e retenção controlada. |
| Rollback falha após perda de conexão | Baixa | `close()` explícito; nenhuma instrução mutável existe na allowlist. |

Uma credencial dedicada somente leitura continua oferecendo garantia superior:
ela mantém a proteção mesmo diante de regressão de código, biblioteca comprometida
ou comando acidental fora deste inventário.
