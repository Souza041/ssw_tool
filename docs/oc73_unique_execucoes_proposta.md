# Proposta condicional de unicidade das execuções OC73

Esta é uma análise, não uma migration executável. Nenhuma alteração deve ser
aplicada antes do diagnóstico do esquema e da revisão operacional.

## Motivação

O `GET_LOCK` serializa os processos cooperantes, mas a unicidade da reserva por
fluxo/data também pode ser protegida pelo banco. A proposta só é necessária se o
diagnóstico confirmar ausência de índice equivalente.

Chave candidata: `(data_referencia, triggered_by)`, restrita semanticamente aos
valores `scheduler:CWB` e `scheduler:BIG`. Como a tabela também aceita execuções
manuais, uma chave direta sobre essas duas colunas pode impedir múltiplas
execuções manuais no mesmo dia. Esse impacto precisa ser decidido antes de criar
a migration.

## Verificação prévia

- listar índices de `oc73_execucoes`;
- consultar grupos duplicados de `data_referencia, triggered_by` para origens do
  scheduler, com limite;
- confirmar se outros produtores gravam `triggered_by` com prefixo `scheduler:`;
- definir tratamento manual para qualquer duplicidade, sem exclusão automática.

## Alternativas

1. Coluna gerada anulável que produza uma chave somente para origens agendadas,
   seguida de índice UNIQUE. É a opção mais restritiva, mas precisa ser validada
   contra a versão exata do MySQL 5.7.
2. Colunas explícitas `fluxo_scheduler` e `data_scheduler`, preenchidas somente
   para reservas agendadas. É mais clara, mas exige mudança de escrita e backfill.
3. Manter apenas `GET_LOCK`. Não requer schema, porém depende de todos os
   produtores respeitarem o protocolo da aplicação.

## Implantação e rollback futuros

Gerar uma migration separada, validar em réplica/laboratório, medir duração e
lock da alteração, executar backup e janela de manutenção, e abortar diante de
duplicidades. O rollback deve remover somente o novo índice/colunas criados pela
mesma migration. Não executar qualquer etapa automaticamente.
