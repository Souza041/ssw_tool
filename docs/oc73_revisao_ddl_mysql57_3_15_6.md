# OC73 — Revisão estática do DDL MySQL 5.7 — Sprint 3.15.6

Revisão exclusivamente **offline**. Não executa SQL e não comprova aceitação pelo
parser, InnoDB, versão real do servidor ou comportamento transacional.

## Resultado estático

| Item | Conclusão estática | Limitação real |
|---|---|---|
| Ordem | tentativas, autorizações, aprovações, consumos, auditoria | criação real Pendente |
| Engine | cinco declarações `ENGINE=InnoDB` | engine efetiva deve ser conferida |
| Charset/collation | `utf8mb4`/`utf8mb4_unicode_ci` explícitos; identidades críticas `ascii_bin` | defaults/DDL efetivo devem ser comparados |
| PKs | `BIGINT UNSIGNED AUTO_INCREMENT`, uma por tabela | alocação/rollback não ensaiados |
| Contexto | UNIQUE e FK composta tentativa/referência/revisão | execução da FK Pendente |
| Consumo/auditoria | FK composta autorização/tentativa | rejeição real Pendente |
| Identidade | UNIQUE por papel, conta namespaced e pessoa namespaced | executor distinto exige aplicação |
| Expiração/revogação | campos temporais e estados presentes | regras temporais exigem transação/aplicação |
| Estados | `ENUM` fechado para tentativa, autorização e papel | transições não são impostas pelo ENUM |
| Compatibilidade pretendida | sem `CHECK`, JSON, generated columns ou sintaxe de MySQL 8 | validação MySQL 5.7.32 real Pendente |

## Correção aplicada nesta sprint

O DDL fixava charset, mas herdava a collation default do servidor. Isso permitia
estruturas efetivas diferentes em duas instâncias. As cinco tabelas agora declaram
`COLLATE=utf8mb4_unicode_ci`; referências, revisões, UUIDs, subjects, pessoas e
hashes continuam com `ascii_bin` para comparação exata.

## Índices e dependências

- `oc73_lab_tentativas`: PK, candidato composto de contexto, UNIQUE de CTRC
  hipotético e UNIQUE binário de referência;
- `oc73_lab_autorizacoes`: uma por tentativa, UUID único, chave candidata
  `(id,tentativa_id)`, índice de status/expiração e FK composta ao contexto;
- `oc73_lab_aprovacoes`: uma aprovação por papel, conta e pessoa em cada
  autorização, com FK simples para autorização;
- `oc73_lab_consumos`: um consumo por autorização e tentativa, FK composta para
  impedir cruzamento e FK direta à tentativa;
- `oc73_lab_auditoria`: índice de consulta por autorização e FKs compostas que
  preservam o contexto autorização/tentativa.

Todos os componentes das FKs possuem tipos/signedness compatíveis no texto. Os
índices compostos declarados ficam abaixo dos limites clássicos de chave mesmo
sem presumir `innodb_large_prefix`, pois os maiores campos indexados usam ASCII.

## Regras não declarativas

MySQL 5.7 não deve depender de `CHECK`. Permanecem na aplicação/transação:
executor diferente dos aprovadores, validade do vínculo pessoa-conta, prazo da
autorização, revogação, transições permitidas, ordem de locks, auditoria antes do
commit e tratamento de commit ambíguo. Nenhum trigger foi introduzido.

## Privilégios e rollback

A conta de Provisionamento/DDL é separada e temporária. A conta transacional não
recebe DDL/DELETE. Com InnoDB, DML dentro da mesma transação deve reverter em
conjunto, mas DDL no MySQL 5.7 possui commits implícitos e não integra o rollback
dos cenários. Por isso provisionamento e Testes DML têm autorizações e janelas
distintas.

## Bloqueios antes da revisão de execução

- confirmar MySQL Community 5.7.32 e configuração InnoDB;
- validar o arquivo exato com ferramenta/servidor aprovado sem aplicá-lo primeiro;
- confirmar a hipótese de unicidade `(serie, numero)` no domínio;
- revisar grants e conta administrativa temporária;
- planejar reação a criação parcial, já que DDL não é atomicamente revertido;
- obter autorização específica de Provisionamento/DDL.
