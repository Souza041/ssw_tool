# Exceção de identidade MySQL — diagnóstico consultivo OC73

Data do registro: 16/10/2026

Projeto: `ssw_tool`

Escopo: preparação do diagnóstico consultivo das tabelas OC73 no schema
`automationx`

## Decisão registrada

O responsável técnico autorizou expressamente, para este diagnóstico, uma
exceção ao requisito de confirmação independente do `server_uuid`. A identidade
observada abaixo poderá ser usada como fingerprint esperado somente depois das
demais aprovações operacionais exigidas pelo procedimento:

- endpoint administrativo informado:
  `automationx.mysql.dbaas.com.br:3306`;
- schema esperado: `automationx`;
- versão observada: `5.7.32-35-log`;
- hostname interno observado: `myshared2014`;
- `server_uuid` observado:
  `22516a2b-8484-11eb-8da3-566fe1410021`.

Esta autorização é uma aceitação excepcional de risco. **Ela não constitui
confirmação independente de que o UUID pertence exclusivamente à instância
acessada pelo endpoint informado.**

## Origem e limitações das evidências

- versão, hostname interno e UUID foram observados no phpMyAdmin associado ao
  ambiente administrativo informado;
- o endpoint e o schema foram fornecidos no contexto operacional do projeto;
- em 16/10/2026, a Locaweb informou que não dispõe de documentação ou mecanismo
  adicional para comprovar independentemente a correspondência exclusiva desse
  UUID à instância;
- não foi realizada conexão pelo script de diagnóstico durante a elaboração
  deste registro;
- este documento registra as informações e a autorização recebidas; não produz
  evidência técnica adicional sobre a identidade do servidor.

## Controles que permanecem obrigatórios

A exceção não altera o código nem reduz as validações do diagnóstico:

- `--server-uuid-esperado` continua obrigatório;
- o UUID retornado por `@@server_uuid` deve corresponder exatamente ao UUID
  esperado, após a normalização já implementada pelo script;
- schema efetivo, porta e versão continuam sendo validados;
- qualquer divergência deve encerrar o diagnóstico de forma segura;
- o inventário SQL fechado, a transação read-only, o rollback defensivo e o
  fechamento explícito da conexão permanecem inalterados;
- não há autorização para DML, DDL, `GET_LOCK`, procedures, múltiplas instruções
  ou acesso à tabela `op535_cnpj_cache`.

## Limites da autorização

Esta autorização cobre exclusivamente a exceção de identidade. Ela não autoriza
conexão ou execução consultiva em produção. Antes de executar ainda são
necessários, separadamente:

1. aprovação do código exato que será executado;
2. aprovação explícita da execução consultiva;
3. definição de janela com DBA/operação;
4. confirmação do ambiente e das variáveis do processo;
5. aceite dos riscos operacionais residuais;
6. definição de interrupção e tratamento para timeout, perda de conexão ou
   identidade divergente.

## Riscos residuais aceitos ou pendentes

| Risco | Severidade | Situação e tratamento |
|---|---:|---|
| UUID sem comprovação independente | Alta | Exceção aceita pelo responsável técnico somente para preparar o diagnóstico; correspondência exata continua obrigatória e não prova exclusividade do endpoint. |
| Credencial existente pode possuir privilégios de escrita | Alta | O código é estritamente consultivo, mas a garantia seria mais forte com conta dedicada somente leitura. A credencial e seus grants devem ser aceitos formalmente antes da execução. |
| Consultas agregadas podem impor carga | Média | Executar apenas em janela aprovada. `MAX_EXECUTION_TIME(5000)` é proteção de melhor esforço no servidor, não limite rígido de duração ponta a ponta. |
| Endpoint pode ser redirecionado ou a instância restaurada/clonada | Média | Interromper diante de qualquer divergência de UUID, porta, schema ou versão e exigir nova decisão técnica. |
| Resultado pode ser limitado ou inconclusivo | Média | Não interpretar limite, timeout ou falha como ausência de duplicidades; observar os estados estruturados do relatório. |

## Parecer desta exceção

**EXCEÇÃO DE IDENTIDADE REGISTRADA, SEM AUTORIZAÇÃO DE EXECUÇÃO.**

O requisito de correspondência exata do UUID foi preservado. A ausência de
confirmação independente está conscientemente documentada como risco residual e
não deve ser omitida do parecer emitido após eventual execução consultiva.
