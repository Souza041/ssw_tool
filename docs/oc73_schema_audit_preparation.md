# OC73 — preparação da auditoria estrutural do MySQL

Esta entrega prepara a coleta; **nenhuma consulta ao banco operacional foi executada**. A fonte executável das instruções fixas e da interpretação é `scripts/diagnostico_oc73_operacional_readonly.py`, agora com `--apenas-schema`. Esse modo não executa as consultas agregadas de integridade nem lê linhas de CTRCs ou referências.

## Contrato derivado do runtime

| Tabela | Colunas essenciais | Restrições mínimas |
| --- | --- | --- |
| `oc73_execucoes` | `id`, `data_referencia`, `triggered_by`, `status`, `dry_run`, `iniciado_em`, `finalizado_em`, `total_relatorio`, `total_filtrado`, `total_lancado`, `total_erros`, `mensagem_erro` | InnoDB; `PRIMARY KEY (id)` com `AUTO_INCREMENT`; tipos e nulabilidade segundo `CONTRATO_COLUNAS`. |
| `oc73_tentativas` | `id`, `execucao_id`, `serie`, `numero`, `codigo_ocorrencia`, `fluxo`, `data_inicial_pesquisa`, `data_final_pesquisa`, `status`, `reservado_em`, `referencia`, `envio_iniciado_em`, `confirmado_em`, `resposta_ssw`, `mensagem_erro` | InnoDB; `PRIMARY KEY (id)` com `AUTO_INCREMENT`; FK `execucao_id → oc73_execucoes.id` com tipos compatíveis; `UNIQUE uq_oc73_ctrc (serie, numero, codigo_ocorrencia)` nessa ordem; `UNIQUE uq_oc73_referencia (referencia)`; collation binária na referência. |

O nome `uq_oc73_ctrc` integra o contrato: o repository reconhece o erro 1062 por esse nome. Um índice equivalente com outro nome impede a duplicação no banco, mas o código aborta a reserva ao receber o conflito desconhecido. O diagnóstico classifica essa situação como não conforme. O mesmo rigor é aplicado a `uq_oc73_referencia`.

A migration versionada `migrations/oc73/001_referencia_lab.sql` só adiciona a referência e seu índice em **laboratório isolado**. Ela não constitui DDL completo das tabelas. A existência da estrutura operacional permanece **não verificada**. Não há baseline oficial das regras `ON UPDATE`/`ON DELETE`; a presença e compatibilidade de tipos da FK são verificáveis, mas a conformidade dessas regras permanece inconclusiva.

## Inventário SQL do modo `--apenas-schema`

Executar na ordem indicada, por sessão dedicada. O script usa SQL fechado, sem interpolar nomes de tabela recebidos do operador. As consultas abaixo são apenas de identidade e `information_schema`/DDL; não consultam registros operacionais.

```sql
START TRANSACTION READ ONLY;
SELECT @@version AS versao, @@version_comment AS distribuicao,
       @@server_uuid AS server_uuid, @@hostname AS hostname,
       @@port AS porta, DATABASE() AS banco;
```

**Parar após a identidade** se banco, porta, versão ou UUID não corresponderem aos valores previamente aprovados. A versão aceita pelo script é MySQL 5.7.32; MariaDB ou outra versão exigem revisão do diagnóstico antes da coleta. A identificação não deve depender apenas do hostname.

```sql
SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'automationx'
  AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY TABLE_NAME;

SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE,
       IS_NULLABLE, COLUMN_DEFAULT, EXTRA, CHARACTER_SET_NAME,
       COLLATION_NAME, GENERATION_EXPRESSION
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'automationx'
  AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY TABLE_NAME, ORDINAL_POSITION;

SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX,
       COLUMN_NAME, SUB_PART, COLLATION, CARDINALITY, INDEX_TYPE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'automationx'
  AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;

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
ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION;

SELECT CONSTRAINT_NAME, TABLE_NAME, REFERENCED_TABLE_NAME,
       UPDATE_RULE, DELETE_RULE
FROM information_schema.REFERENTIAL_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA = 'automationx'
  AND TABLE_NAME IN ('oc73_execucoes', 'oc73_tentativas')
ORDER BY TABLE_NAME, CONSTRAINT_NAME;

SHOW CREATE TABLE automationx.oc73_execucoes;
SHOW CREATE TABLE automationx.oc73_tentativas;
ROLLBACK;
```

`TABLE_ROWS` é uma estimativa em InnoDB. `SHOW CREATE TABLE` retorna DDL, não conteúdo de linhas. O script sempre tenta `ROLLBACK` e fecha a conexão, inclusive diante de erro.

## Procedimento futuro, sujeito a nova autorização

1. DBA confirma uma conta com privilégios **somente leitura** para o schema esperado e fornece o UUID aprovado por fonte independente. Não colocar credenciais em argumento de linha de comando, documentação, logs ou repositório; fornecer `AUTOMATION_DB_*` apenas ao ambiente do processo autorizado. O script não modifica `.env` nem verifica sozinho os privilégios da conta.
2. Em janela aprovada, revisar endpoint, schema, porta, versão e UUID esperados. Confirmar que a conexão usa o schema explicitamente identificado. Não prosseguir se a identidade não puder ser comprovada.
3. Somente após a nova autorização, executar `python -m scripts.diagnostico_oc73_operacional_readonly --somente-leitura --apenas-schema --server-uuid-esperado <UUID_APROVADO>`. Essa chamada **não faz parte desta entrega**.
4. Conferir `conformidade_schema.contrato_minimo`. O modo retorna código 2 quando o contrato mínimo não é conforme ou quando há erro seguro; ausência de coleta e resultado inconclusivo não liberam operação. Guardar somente o resultado de metadados no local aprovado pelo DBA.
5. Tratar divergências fora do diagnóstico, mediante mudança e migration separadas. Nunca aplicar correção automática nem ativar OC73 por causa de um resultado isolado.

## Bloqueios para liberação operacional

- Banco, porta, versão ou UUID inesperado ou não verificável; falha de conexão ou metadados incompletos.
- Uma das tabelas ausente, engine diferente de InnoDB, PK `id` ausente/inválida ou sem `AUTO_INCREMENT`.
- Coluna essencial ausente, tipo/capacidade/nulabilidade incompatível, inclusive `referencia` curta ou sem collation binária.
- `uq_oc73_ctrc` ou `uq_oc73_referencia` ausente, não único, por prefixo (`SUB_PART`), com colunas diferentes ou em ordem diferente. Índice equivalente com outro nome não satisfaz o contrato do repository.
- FK mínima `oc73_tentativas.execucao_id → oc73_execucoes.id` ausente ou com tipos incompatíveis.

A ausência de UNIQUE por fluxo/data em `oc73_execucoes` é registrada separadamente: o scheduler utiliza `GET_LOCK`, e a proposta de backstop ainda exige decisão própria. A verificação de duplicidades reais e estados persistidos está fora do modo de schema e precisará de autorização distinta.
