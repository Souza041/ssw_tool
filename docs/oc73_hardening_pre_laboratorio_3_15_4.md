# OC73 — Hardening de segurança pré-laboratório (Sprint 3.15.4)

Data: 2026-10-16. Escopo 100% offline. Nenhuma conexão, DDL, DML, migration,
serviço, IdP, SSW ou alteração de runtime foi executada.

## 1. Problemas encontrados antes do hardening

1. `SHOW GRANTS` era apenas contado; privilégios excessivos não bloqueavam.
2. `CURRENT_USER()` perdia o componente `@host`, permitindo equivalência indevida.
3. Porta aceitava conversões, socket vazio não era rejeitado e versão era aceita
   por prefixo.
4. UUID, datadir, distribuição e hostname não tinham contrato estrito completo.
5. Não existia fábrica auditável de conexão; o comportamento do driver não era
   representado.
6. Identidade corporativa dependia de `subject_id`/`person_id` sem namespace e
   sem prova/validade do vínculo pessoa-conta.
7. O DDL não persistia namespace dos identificadores nem executor do consumo.
8. `UNIQUE (serie, numero)` aparecia como garantia sem registrar que ainda é
   hipótese do domínio SSW.

## 2. Alterações implementadas

- parser fail-closed de grants MySQL com conta, host, escopo e privilégios;
- perfis mínimos `consultivo` e `transacional`;
- comparação exata de `CURRENT_USER()` e `USER()` completos;
- configuração tipada e literal, UUID canônico, versão/distribuição exatas,
  hostname opcional explícito e datadir absoluto/canônico por política lexical;
- allowlist ampliada somente para a nova tabela experimental de consumo;
- fábrica inerte em `prototypes/oc73_mysql_lab_adapter.py`, com timeouts,
  autocommit desligado, `client_flag=0`, charset fixo e erros sem causa sensível;
- namespaces `(issuer, subject_id)` e `(person_issuer, person_id)`, flag de vínculo
  verificado e validade desse vínculo;
- executor distinto dos aprovadores;
- tabela `oc73_lab_consumos` e índices compostos binários no DDL;
- testes adversariais de configuração, grants, identidade, fábrica e transação.

## 3. Política de grants

### A. Verificação consultiva — conta dedicada

Allowlist efetiva do verificador:

```sql
GRANT USAGE ON *.* TO 'oc73_tester'@'127.0.0.1'
GRANT SELECT ON `oc73_autorizacao_transacional_test`.*
  TO 'oc73_tester'@'127.0.0.1'
```

`USAGE` é o único privilégio global tolerado. O conjunto `SELECT` deve ser
comprovado integralmente e nenhum privilégio extra é aceito.

### B. Preparação de schema — administrador separado

Não é aceita pelo verificador nem pela fábrica transacional. Uma identidade
administrativa temporária, criada/gerida por administrador autorizado, pode
receber apenas no schema lab os privilégios necessários ao DDL revisado, como
`CREATE`, `ALTER`, `INDEX` e `REFERENCES`. `DROP` só pode constar em autorização
separada de limpeza com alvo validado. Essa conta nunca deve executar os testes.

### C. Testes transacionais — conta dedicada

```sql
GRANT USAGE ON *.* TO 'oc73_tester'@'127.0.0.1'
GRANT SELECT ON `oc73_autorizacao_transacional_test`.*
  TO 'oc73_tester'@'127.0.0.1'
GRANT INSERT, UPDATE ON `oc73_autorizacao_transacional_test`.`oc73_lab_tentativas`
  TO 'oc73_tester'@'127.0.0.1'
GRANT INSERT, UPDATE ON `oc73_autorizacao_transacional_test`.`oc73_lab_autorizacoes`
  TO 'oc73_tester'@'127.0.0.1'
GRANT INSERT ON `oc73_autorizacao_transacional_test`.`oc73_lab_aprovacoes`
  TO 'oc73_tester'@'127.0.0.1'
GRANT INSERT ON `oc73_autorizacao_transacional_test`.`oc73_lab_consumos`
  TO 'oc73_tester'@'127.0.0.1'
GRANT INSERT ON `oc73_autorizacao_transacional_test`.`oc73_lab_auditoria`
  TO 'oc73_tester'@'127.0.0.1'
```

`UPDATE` não é concedido a aprovações, consumos ou auditoria; isso preserva a
trilha append-only dentro do perfil transacional. Não são necessários `DELETE`,
DDL ou privilégios administrativos. São rejeitados:
grants globais além de `USAGE`, `ALL PRIVILEGES`, `GRANT OPTION`, `SUPER`, `FILE`,
`PROCESS`, `CREATE USER`, `RELOAD`, `SHUTDOWN`, DDL, `DELETE`, replicação,
`automationx`, outro schema, outra conta/host, roles/grants indiretos e qualquer
formato não interpretável. O relatório expõe apenas a quantidade validada.

O parser é deliberadamente estreito. Se `SHOW GRANTS` real usar sintaxe não
prevista, o laboratório permanece bloqueado até revisão do formato saneado.

## 4. Contrato de identidade MySQL e configuração

São exigidos antes de qualquer futura escrita:

- `host` `str` exatamente `127.0.0.1`;
- `porta` do tipo exato `int`, valor `3308`;
- schema/usuário literais;
- `unix_socket is None` — inclusive `""` é rejeitado;
- `CURRENT_USER()` exatamente igual à conta autorizada, incluindo `@host`;
- `USER()` exatamente igual à identidade de conexão autorizada;
- UUID canônico versão/variant válidos;
- versão autorizada exata no ramo `5.7.32` e `@@version_comment` MySQL exato;
- MariaDB rejeitado em versão ou distribuição;
- hostname exato quando fornecido no contrato;
- datadir absoluto, com separadores canônicos, barra final, sem `.`/`..`, NUL,
  repetição de separador ou resolução implícita de filesystem;
- tabelas existentes como subconjunto das cinco `oc73_lab_*`.

Converter `\` para `/` é a única normalização de datadir. Não se usa
`resolve()`, case-folding, expansão de symlink ou equivalência de paths, pois isso
poderia transformar identidades diferentes em iguais sem prova do filesystem.

## 5. Fábrica de conexão

`prototypes/oc73_mysql_lab_adapter.py` recebe configuração já validada,
credencial explícita em memória e objeto de driver injetado. Não lê `.env`, não
importa PyMySQL, não conecta na construção e não aceita kwargs extras.

Parâmetros fixos:

- host/porta/usuário/schema sem fallback;
- `utf8mb4`, cursor de dicionário e `autocommit=False`;
- timeouts explícitos de conexão/leitura/escrita;
- `client_flag=0`, sem habilitar `CLIENT.MULTI_STATEMENTS`;
- nenhuma opção de socket;
- erro sanitizado com `raise ... from None`, sem senha, DSN ou endpoint.

Limite: mocks comprovam os kwargs enviados, não os flags efetivos que uma versão
real do PyMySQL negocia. O adaptador permanece bloqueado para uso até inspeção da
versão instalada e teste autorizado do driver no laboratório.

## 6. Contrato OIDC/RBAC endurecido

Uma conta é identificada por `(issuer, subject_id)`. Uma pessoa é identificada
por `(person_issuer, person_id)`, acompanhada por `person_verified=True` e prazo
do vínculo. Issuers de conta e pessoa precisam estar em allowlists confiáveis
fornecidas pela futura integração; não são aceitos do formulário/navegador.

No consumo são revalidados:

- issuer/namespace, sessão, expiração, MFA e autenticação de até 15 minutos;
- vínculo pessoa-conta ainda válido e verificado;
- papéis técnico, operacional e executor;
- dois aprovadores com contas e pessoas distintas;
- executor como terceira pessoa;
- tentativa, referência e revisão idênticas.

Mesmo `subject_id` em issuers diferentes não é automaticamente a mesma conta.
Dois e-mails/usernames diferentes não provam pessoas diferentes. A origem e a
semântica de `person_id` continuam dependentes de contrato corporativo real.

## 7. Hardening do DDL

O DDL experimental, não executado, contém cinco tabelas InnoDB:

- tentativas: CTRC/referência/revisão e estados;
- autorizações: uma por tentativa, prazo, status e versão;
- aprovações: issuer/subject, issuer/person, vínculo e sessão em hash;
- consumos: executor e uso único por autorização/tentativa;
- auditoria: evento ligado à autorização e tentativa.

Garantias de banco propostas: PK/FK, referência única, autorização única por
tentativa, um consumo por autorização/tentativa, papel/conta/pessoa únicos dentro
da autorização e índices binários. Garantias da aplicação: prazo, `person_verified
== 1`, transições, revisão/referência exatas, executor distinto, ordem de locks e
rowcount. Garantias corporativas: autenticidade/estabilidade dos namespaces e
vínculo de pessoa. Garantias SSW: identidade global do CTRC e persistência da
referência.

`UNIQUE (serie, numero)` foi mantido, mas marcado como **hipótese de domínio**.
Se série/número não forem globais no SSW, uma revisão formal deverá substituir a
chave por contexto comprovado, por exemplo `(familia, local_ssw, serie, numero)`
ou outro identificador estável retornado pela OP101. Nenhuma mudança foi feita
silenciosamente.

MySQL 5.7 ignora `CHECK`; portanto nenhuma regra depende dele.

## 8. Modelo de ameaças e riscos residuais

| Ameaça | Controle offline | Risco residual |
|---|---|---|
| Conta DB com poder global | parser de grants fail-closed | sintaxe real ainda não observada |
| Confusão `user@host` | comparação integral de `CURRENT_USER()`/`USER()` | conta real ainda desconhecida |
| Redirecionamento/socket | IP literal e socket proibido | comportamento real do driver pendente |
| Servidor/volume errado | UUID, hostname, versão e datadir exatos | isolamento físico requer prova administrativa |
| Multi-statements | `client_flag=0`, SQL fixo | flags negociados pelo driver pendentes |
| Senha em erro | mensagens fixas e causa removida | política de logging externa ainda deve ser auditada |
| Duas contas da mesma pessoa | namespace/person verificado | contrato IdP/diretório inexistente |
| Executor-aprovador | validação por pessoa no consumo | depende do `person_id` corporativo |
| Reuso/concorrência | locks e updates condicionais modelados | MySQL real não testado |
| Commit ambíguo | nenhum callback/retry | intervenção humana permanece necessária |
| CTRC duplicado | unique hipotético | chave global do SSW pendente |

## 9. Bloqueios

- conta/host MySQL efetivos, UUID, datadir, hostname, versão e distribuição ainda
  não foram autorizados nem observados;
- grants reais não foram capturados/revisados;
- driver PyMySQL e flags efetivos não foram validados;
- DDL e transações não foram executados em MySQL 5.7;
- contrato corporativo de issuer, pessoa, MFA e papéis não existe no repositório;
- identidade global do CTRC e persistência real da referência continuam pendentes;
- nenhuma integração runtime/homologação/produção está autorizada.

