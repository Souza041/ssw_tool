# OC73 — Solicitação de autorização do laboratório 3.15.6

Modelo exclusivamente **offline**. Não executa ações e não representa aprovação.

> **STATUS: APROVAÇÃO NÃO CONCEDIDA — todos os campos dependem de preenchimento,
> revisão e assinatura explícita do responsável autorizador.**

## Identificação

| Campo | Preenchimento obrigatório |
|---|---|
| Responsável autorizador | A preencher |
| Responsável executor | A preencher |
| Responsável pela instância | A preencher |
| Escopo exato | A preencher: uma única classe de autorização |
| Ambiente permitido | A preencher; somente laboratório isolado |
| Instância | A preencher |
| Host | deve ser `127.0.0.1` |
| Porta | deve ser `3308` |
| Schema | deve ser `oc73_autorizacao_transacional_test` |
| Conta | A preencher, incluindo `user@host` |
| Revisão de código | hash/referência imutável a preencher |
| Janela de execução | início e término com timezone a preencher |
| Validade | expiração explícita a preencher |

## Escopo autorizado

- **Classe solicitada:** marcar exatamente uma: Inventário / Provisionamento/DDL / Testes DML / Limpeza.
- **Operações autorizadas:** enumerar comandos, objetos e cenários literais.
- **Operações proibidas:** tudo que não estiver enumerado, sistemas externos,
  runtime, SSW, IdP, dados reais, credenciais operacionais e execução automática.
- **Volume máximo:** conexões, transações, linhas sintéticas e duração a preencher.

Uma autorização genérica, verbal ou para “laboratório” não concede DDL, DML,
Limpeza nem a etapa seguinte. Inventário não autoriza escrita; Provisionamento/
DDL não autoriza testes; Testes DML não autorizam Limpeza.

## Evidências obrigatórias antes da janela

- contrato administrativo válido de processo, porta, executável, datadir e volume;
- ausência de replicação e compartilhamento com qualquer ambiente real;
- versão/distribuição/UUID/hostname/conta/grants revisados;
- revisão e SHA-256 do DDL, protótipos, driver e testes;
- versão, origem e integridade do PyMySQL;
- confirmação de que não há CTRC, SSW, IdP ou callback real;
- plano de barreiras, limites, evidência, recuperação e Limpeza aplicável.

## Critérios de interrupção

Interromper imediatamente por divergência de identidade/hash/revisão, evidência
expirada, privilégio excessivo, conexão não local, socket, replicação, arquivo
compartilhado, objeto inesperado, erro não catalogado, timeout de lock, deadlock
fora do cenário, perda de conexão, commit ambíguo ou exposição de segredo.

Interromper não concede permissão para rollback destrutivo, correção manual,
repetição, callback ou nova tentativa.

## Recuperação

Registrar estado observado e incertezas, encerrar conexões com rollback defensivo
quando semanticamente possível, preservar evidências e submeter novo plano ao
autorizador. Estado indeterminado não pode ser convertido em reenviável. Nenhuma
recuperação acessa sistemas externos.

## Limpeza

Exige autorização separada, alvo literal, inventário antes/depois, executor e
janela próprios. Não usar curingas, nomes derivados de consulta, conta
transacional ou remoção automática. Evidências exigidas devem ser preservadas.

## Aceite explícito

- decisão: A preencher — APROVAR / REJEITAR;
- classe e operações aprovadas: A preencher;
- condições adicionais: A preencher;
- assinatura/referência verificável: A preencher;
- instante e expiração: A preencher.

Sem todos os campos e correspondência exata da revisão, o resultado é NO-GO.
