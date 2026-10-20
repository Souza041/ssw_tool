# OC73 — Laboratório transacional (Sprint 3.15.3)

Documento de preparação. Nesta sprint não houve conexão, criação de serviço,
schema, tabela, migration nem execução do DDL.

> Nota posterior: a Sprint 3.15.4 endureceu grants, identidade MySQL/OIDC,
> fábrica de conexão e DDL. Para o estado atual, consultar
> `docs/oc73_hardening_pre_laboratorio_3_15_4.md`.

## 1. Escopo e arquitetura

O laboratório futuro deve ser descartável, exclusivamente local e separado do
runtime:

```text
testes autorizados -> adaptador MySQL de laboratório -> 127.0.0.1:3308
                                               |
                         oc73_autorizacao_transacional_test
                                               |
                    somente tabelas oc73_lab_* em allowlist fechada
```

O verificador `prototypes/oc73_laboratorio_verificador.py` é independente,
somente consultivo e recebe uma fábrica de conexão explicitamente. Ele não lê
`.env`, não importa PyMySQL/runtime, não cria conexões sozinho e não oferece SQL
arbitrário.

## 2. Contrato de isolamento

Antes de abrir uma conexão, o verificador exige exatamente:

- flag de autorização verdadeira;
- host literal `127.0.0.1`;
- porta `3308` — `3306` é rejeitada;
- schema `oc73_autorizacao_transacional_test`;
- usuário `oc73_tester`;
- ausência de socket;
- UUID e datadir esperados, fornecidos pela autorização do laboratório;
- nenhum parâmetro/DSN adicional.

Depois de conectar e antes de qualquer futura escrita, exige correspondência de:

- `DATABASE()`, `@@port`, versão MySQL `5.7.32*` sem marcador MariaDB;
- `@@server_uuid`, `@@datadir` e usuário efetivo;
- tabelas existentes como subconjunto da allowlist `oc73_lab_tentativas`,
  `oc73_lab_autorizacoes`, `oc73_lab_aprovacoes`, `oc73_lab_auditoria`.

Qualquer divergência causa rollback defensivo, fechamento e erro sanitizado. A
validação não prova isolamento sozinha: o administrador ainda deve comprovar que
o processo na porta 3308 e seu datadir são dedicados e que não usam credenciais,
volumes ou replicação operacionais.

## 3. Inventário SQL fechado do verificador

```sql
SELECT DATABASE() AS schema_efetivo, @@hostname AS hostname,
       @@port AS porta, @@version AS versao,
       @@version_comment AS comentario_versao,
       @@server_uuid AS server_uuid, @@datadir AS datadir,
       CURRENT_USER() AS usuario_efetivo, USER() AS usuario_conexao

SELECT table_name
FROM information_schema.tables
WHERE table_schema = %s
ORDER BY table_name

SHOW GRANTS FOR CURRENT_USER
```

Não existem `GET_LOCK`, procedures, múltiplas instruções, DML ou DDL no
verificador. `SHOW GRANTS` é coletado apenas como contagem no relatório do
protótipo; credenciais e DSN não são exibidos.

## 4. DDL experimental não executado

O arquivo
`prototypes/sql/oc73_autorizacao_lab_mysql57.sql` contém quatro `CREATE TABLE`
InnoDB independentes das tabelas operacionais:

- `oc73_lab_tentativas`: CTRC/referência únicos e estado;
- `oc73_lab_autorizacoes`: uma autorização por tentativa, prazo e versão;
- `oc73_lab_aprovacoes`: papéis e identidades, com unicidade de papel, conta e
  pessoa por autorização;
- `oc73_lab_auditoria`: eventos da transição.

O DDL usa PK, FK, `UNIQUE`, índices, `ENUM`, `NOT NULL`, collation ASCII binária
para IDs/referência e `DATETIME(6)`. Não usa `CHECK`, pois MySQL 5.7 o ignora. A
validade de 15 minutos, revisão, papéis e transições permanecem invariantes da
aplicação revalidadas sob lock.

O DDL não referencia nem altera `oc73_execucoes`, `oc73_tentativas`, OP535 ou
qualquer tabela operacional. Ele não é migration e exige revisão/autorização
específica antes de eventual execução.

## 5. Transação que deverá ser testada

Ordem determinística em todos os caminhos:

1. `START TRANSACTION` com autocommit desligado;
2. tentativa por PK `FOR UPDATE`;
3. autorização por PK/tentativa `FOR UPDATE`;
4. aprovações pelo índice `(autorizacao_id, papel) FOR UPDATE`;
5. revalidar prazo, status, referência, revisão, papéis, pessoas e executor;
6. inserir auditoria;
7. update condicional da autorização `pendente -> consumida`;
8. update condicional da tentativa `reservado -> envio_iniciado`;
9. exigir `rowcount == 1` nos dois updates;
10. commit; liberar callback somente após confirmação inequívoca.

`SELECT ... FOR UPDATE` mantém locks até commit/rollback em uma transação ativa,
conforme o [manual MySQL 5.7](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/innodb-locking-reads.html).
Deadlock, timeout, conexão perdida ou commit ambíguo não disparam retry nem
callback automático neste portão.

## 6. Plano de testes futuros

| Cenário | Mock offline | MariaDB isolado | MySQL 5.7 isolado |
|---|:---:|:---:|:---:|
| Dois processos, mesma autorização | aprovado | pendente | pendente |
| Duas autorizações, mesma tentativa | aprovado | pendente | pendente |
| Mesma pessoa em contas distintas | aprovado | pendente | pendente |
| Expirada/revogada | aprovado | pendente | pendente |
| Revisão/referência divergente | aprovado | pendente | pendente |
| Falha de auditoria/rollback | aprovado por injeção | pendente | pendente |
| Deadlock/lock wait timeout | aprovado por injeção | pendente | pendente |
| Perda de conexão/commit ambíguo | aprovado por injeção | pendente | pendente |
| Crash após commit | aprovado por injeção | pendente | pendente |
| Nenhum segundo callback/reenvio | aprovado | pendente | pendente |

MariaDB pode revelar erros do adaptador e concorrência básica, mas não comprova
compatibilidade MySQL 5.7: engine/versionamento, códigos de erro, metadados,
defaults, locking, deadlock e comportamento do driver podem divergir. Evidências
devem sempre identificar produto e versão efetivos.

## 7. Autorização necessária antes de testar banco

A solicitação futura deve anexar:

- código e hash/revisão exatos do verificador, adaptador, DDL e testes;
- responsável, janela e procedimento de interrupção;
- endpoint/schema/usuário e UUID/datadir esperados;
- prova administrativa de isolamento do serviço/volume;
- privilégios concedidos ao usuário e ausência de credencial operacional;
- autorização separada para executar o DDL experimental;
- limites, timeout, número de processos e plano de limpeza somente do schema lab.

O teste deve parar antes do DDL se qualquer identidade divergir, se houver tabela
fora da allowlist, se a porta estiver ocupada por serviço desconhecido ou se não
for possível comprovar isolamento. Durante a execução: interromper em carga
anormal, deadlock inesperado recorrente, perda de conexão, commit ambíguo ou
qualquer indício de acesso fora do schema. Preservar logs saneados e não tentar
“corrigir” estado com DML manual.

## 8. Evidências esperadas

- relatório consultivo de identidade, sem segredo;
- versão, UUID/datadir e usuário efetivo aprovados;
- DDL e `SHOW CREATE TABLE` das quatro tabelas;
- engine/collation, PK/FK/UNIQUE/índices;
- barreira sincronizada de dois processos e resultados individuais;
- snapshots agregados de estado antes/depois;
- logs de deadlock/timeout saneados e contagem de callbacks falsos;
- rollback/close confirmados em todas as falhas;
- prova de zero rede SSW e zero importação de runtime.

## 9. Limitações e Go/No-Go

O verificador e o fake provam fail-closed do código offline. Não comprovam a
existência do laboratório, isolamento físico, privilégios, durabilidade, locks ou
recuperação real do MySQL.

- Pacote para **solicitar autorização de laboratório: PRONTO**.
- Execução do laboratório: **NO-GO** até autorização específica, identidade
  esperada e revisão do adaptador MySQL ainda não implementado.
- Compatibilidade MySQL 5.7 real: **PENDENTE**.
- Integração ao runtime: **NO-GO**.
- Homologação SSW e produção automática: **NO-GO**.

