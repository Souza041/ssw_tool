# OC73 — Provisionamento controlado do laboratório 3.15.6

Plano exclusivamente **offline**. Este documento não executa comandos, não cria
serviços e não concede autorização. Cada mudança futura exige aprovação própria.

## Autorizações independentes

| Escopo | Permite | Não autoriza |
|---|---|---|
| Inventário | coletar evidências administrativas e consultas estritamente consultivas aprovadas | Provisionamento/DDL, Testes DML ou Limpeza |
| Provisionamento/DDL | criar exclusivamente as cinco tabelas revisadas no schema literal | Testes DML, Limpeza ou acesso a sistemas externos |
| Testes DML | executar somente cenários e dados sintéticos aprovados | DDL, Limpeza, SSW ou runtime operacional |
| Limpeza | remover apenas os objetos experimentais explicitamente identificados | qualquer alvo não literal ou reutilização da autorização anterior |

Uma autorização não autoriza as demais, e uma aprovação genérica não substitui
escopo, identidade, janela, revisão e operações explicitamente enumeradas.

## Sequência futura, manual e interrompível

1. Obter autorização específica para a etapa pretendida, com validade e revisão.
2. Confirmar que a instância alegada é dedicada e isolada.
3. Vincular processo, `127.0.0.1:3308`, executável, datadir e volume.
4. Confirmar ausência de arquivos, replicação, serviço e credenciais operacionais.
5. Validar a identidade administrativa externa e sua validade temporal.
6. Com autorização consultiva, validar MySQL Community 5.7.32 e distribuição.
7. Validar separadamente a conta temporária de provisionamento e seu escopo.
8. Conferir hash/revisão do DDL e parecer estático antes da janela.
9. Aplicar somente o DDL autorizado, manualmente e sem script agregador.
10. Conferir `SHOW CREATE TABLE`, colunas, engine, collation, índices e FKs.
11. Revogar a conta de provisionamento e aplicar privilégios mínimos separados.
12. Executar o verificador consultivo apenas sob autorização de inventário.
13. Solicitar e registrar autorização separada para Testes DML transacionais.
14. Coletar evidências saneadas, rowcounts e estados agregados de cada cenário.
15. Encerrar cursores/conexões, executar rollback defensivo quando aplicável e preservar evidências.
16. Executar Limpeza apenas com autorização nova, alvo literal e inventário antes/depois.

Nenhuma etapa avança automaticamente para a seguinte. O executor deve parar e
obter nova decisão quando a autorização muda de classe.

## Privilégios planejados

- administrador temporário de DDL: somente o necessário para criar e conferir as
  cinco tabelas no schema experimental; não reutilizado nos testes;
- conta consultiva: `USAGE` global e `SELECT` no schema experimental;
- conta transacional: perfil table-specific já definido pelo parser, sem
  `DELETE`, DDL, `GRANT OPTION` ou privilégio administrativo;
- conta de limpeza: criada/usada somente na janela específica, sem alvo genérico.

Os grants exatos devem ser revisados antes da execução. Não há senha ou comando
executável neste documento.

## Pontos de interrupção

Interromper antes de qualquer escrita se houver: identidade divergente, evidência
administrativa incompleta/expirada, versão diferente, hash divergente, conta ou
grant excessivo, tabelas preexistentes inesperadas, conexão externa, socket,
replicação, volume compartilhado ou revisão de código diferente.

Durante DDL ou Testes DML, interromper em erro não catalogado, deadlock fora do
cenário, commit ambíguo, perda de conexão, carga inesperada ou falha de auditoria.
Não repetir automaticamente uma operação de resultado indeterminado.

## Conferência e evidências

Guardar, sem segredos: autorização aplicável, responsáveis, timestamps, hashes,
identidades administrativa/SQL, grants, DDL efetivo, resultados agregados,
erros saneados e fechamento das conexões. `SHOW CREATE TABLE` real deverá ser
comparado ao arquivo revisado; diferença é NO-GO até nova revisão.

## Recuperação e limpeza

Falha de provisionamento não autoriza correção ad hoc. Registrar os objetos
efetivamente criados e encerrar. A recuperação requer novo plano aprovado. A
Limpeza não pode usar curingas, descoberta dinâmica como alvo ou conta
transacional; deve preservar previamente as evidências exigidas.
