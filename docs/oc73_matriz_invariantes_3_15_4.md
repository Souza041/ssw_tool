# OC73 — Matriz de invariantes (Sprint 3.15.4)

“COMPROVADO” limita-se a propriedade estática/offline diretamente observada.
“SIMULADO” identifica comportamento de fake/mock/injeção. Nenhum item MySQL ou
SSW real é promovido com base nesses testes.

| Invariante | Camada | Enforcement | Evidência offline | MySQL real | SSW real | Estado |
|---|---|---|---|---|---|---|
| Protótipos não são importados pelo runtime | estrutura | AST/import scan | testes de isolamento | n/a | n/a | **COMPROVADO** |
| Nenhuma conexão na importação/construção | adaptador | driver injetado; closure inerte | driver falso com zero chamadas | pendente | n/a | **COMPROVADO** offline |
| Host/porta/schema/usuário literais | verificador | tipos exatos e allowlist | casos adversariais | pendente | n/a | **COMPROVADO** offline |
| Socket sempre proibido | verificador | exige `None` exato | socket path e vazio rejeitados | pendente | n/a | **COMPROVADO** offline |
| UUID canônico | verificador | regex + `uuid.UUID` | UUID vazio/inválido rejeitado | pendente | n/a | **COMPROVADO** offline |
| Datadir não ambíguo | verificador | política lexical absoluta | vazio/relativo/divergente rejeitados | pendente | n/a | **COMPROVADO** offline |
| Produto exato MySQL 5.7.32 | verificador | versão e distribuição exatas | 8.0/MariaDB rejeitados | pendente | n/a | **COMPROVADO** offline |
| Hostname esperado | verificador | comparação exata quando definido | divergência rejeitada | pendente | n/a | **COMPROVADO** offline |
| Conta MySQL completa | verificador | `CURRENT_USER()` integral | `@localhost` vs `@127.0.0.1` rejeitado | pendente | n/a | **COMPROVADO** offline |
| Identidade de conexão completa | verificador | `USER()` integral | divergência rejeitada | pendente | n/a | **COMPROVADO** offline |
| Sem privilégio global perigoso | grants | somente `USAGE` em `*.*` | grants globais/ALL/admin rejeitados | pendente | n/a | **COMPROVADO** no parser |
| Conta transacional sem DDL/DELETE | grants | allowlist SELECT/INSERT/UPDATE | DDL/DELETE rejeitados | pendente | n/a | **COMPROVADO** no parser |
| Grant só no schema lab | grants | escopo literal | automationx/outro schema rejeitados | pendente | n/a | **COMPROVADO** no parser |
| Grant/role desconhecido falha fechado | grants | gramática fechada | formatos desconhecidos rejeitados | pendente | n/a | **COMPROVADO** no parser |
| Multi-statements não solicitado | adaptador | `client_flag=0`, SQL fixo | kwargs do driver falso | pendente | n/a | **COMPROVADO** na chamada; driver **PENDENTE** |
| Erro não expõe senha/DSN | adaptador | mensagem fixa, sem causa | exceção falsa adversarial | pendente | n/a | **COMPROVADO** offline |
| Conta identificada por issuer+subject | identidade/DDL | comparação/UNIQUE composto | emissores distintos testados | n/a | n/a | **COMPROVADO** no modelo |
| Pessoa possui namespace e vínculo verificado | identidade/DDL | issuer, flag e expiração | ausente/falso/expirado rejeitado | n/a | n/a | **COMPROVADO** no modelo; origem **PENDENTE** |
| Dois aprovadores são pessoas distintas | identidade/transação | comparação de chave de pessoa | mesma pessoa rejeitada | pendente | n/a | **SIMULADO** |
| Executor é terceira pessoa | identidade/consumo | revalidação no consumo | executor-aprovador rejeitado | pendente | n/a | **SIMULADO** |
| Escopo contém tentativa/ref/revisão | identidade/autorização | igualdade exata | divergências rejeitadas | pendente | pendente | **SIMULADO** |
| Prazo máximo de 15 minutos | aplicação | validação temporal | prazo de 16 minutos rejeitado | pendente | n/a | **COMPROVADO** no modelo |
| Uma autorização por tentativa | armazenamento/DDL | lock + UNIQUE | duas concorrentes: uma vence | pendente | n/a | **SIMULADO** |
| Consumo e envio_iniciado no mesmo commit | transação/DDL | updates condicionais | snapshot fake | pendente | n/a | **SIMULADO** |
| Falha de auditoria aborta consumo | transação | auditoria antes do commit | injeção e snapshot inalterado | pendente | n/a | **SIMULADO** |
| Commit ambíguo não libera callback | orquestração | fail-closed, sem retry | dois desfechos injetados | pendente | n/a | **SIMULADO** |
| Incerto não reenvia | gate/modelo | estado terminal automático | callback falso uma vez | pendente | pendente | **SIMULADO** |
| Referência é única | DDL | UNIQUE binário | inspeção DDL | pendente | pendente | **PENDENTE** de banco/SSW |
| CTRC é único por série+número | DDL | UNIQUE hipotético | inspeção DDL | pendente | pendente | **PENDENTE** de domínio SSW |
| OP101 preserva referência integral | SSW | histórico exato | somente fixture/fake | n/a | pendente | **PENDENTE** |
| Contexto de autorização corresponde à tentativa | DDL | FK composta `(tentativa_id, referencia, revisao_codigo)` | inspeção estática | pendente | n/a | **COMPROVADO** no texto do DDL; execução **PENDENTE** |
| Consumo/auditoria não cruzam autorização A com tentativa B | DDL | FK composta `(autorizacao_id, tentativa_id)` | inspeção estática | pendente | n/a | **COMPROVADO** no texto do DDL; execução **PENDENTE** |
| Isolamento físico e ausência de replicação | administração | contrato de evidência assinado e temporal | validação offline do contrato | pendente | n/a | **PENDENTE** de evidência administrativa real |
| Driver corresponde ao inventário aprovado | adaptador | pacote, versão, origem, SHA-256 e capabilities exatos | contrato sintético e driver falso | pendente | n/a | **COMPROVADO** no bloqueio offline; inventário real **PENDENTE** |
