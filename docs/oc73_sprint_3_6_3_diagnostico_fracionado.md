# OC73 Sprint 3.6.3 — diagnóstico fracionado e fail-closed

**Parecer técnico: APTO PARA AVALIAÇÃO CONTROLADA DO PROCEDIMENTO; consulta operacional ainda BLOQUEADA.** A implementação e os testes ocorreram somente com fakes locais. Não houve conexão MySQL, `EXPLAIN`, agregação real, automação OC73 ou alteração de gates.

## Contrato do CLI

| Argumentos de modo | Comportamento | Saída |
| --- | --- | --- |
| `--apenas-schema` | Mantém identidade, metadados, DDL, avaliação do contrato mínimo; nenhuma agregação. | Resumo por padrão; `--completo` explícito ainda permite relatório estrutural integral. |
| `--verificacao NOME --confirmar-agregacao` | Após identidade e contrato mínimo conformes, executa **somente** o SQL fixo de `NOME`. | Resumo obrigatório. A selecionada é conforme, não conforme, inconclusiva ou não executada; as outras quatro aparecem como `nao_executada`. |
| Sem modo, sem confirmação, nome fora da allowlist, seleção repetida ou combinação com `--apenas-schema` | Recusa antes de carregar configuração ou conectar. O modo completo histórico não executa mais as cinco consultas. | Código 3 e mensagem controlada no stderr. |
| `--verificacao ... --completo` | Recusa antes da conexão para impedir saída de identidade/DDL numa consulta operacional. | Código 3. |

Em todos os modos efetivos, `--somente-leitura` e `--server-uuid-esperado` continuam obrigatórios. Os únicos valores de `NOME` são `duplicidades_scheduler`, `duplicidades_ctrc`, `duplicidades_referencia`, `tentativas_orfas` e `integridade_estados`. Código 0 significa apenas que o **escopo selecionado** terminou conforme; as outras verificações continuam `nao_executada` e não recebem aprovação implícita. Código 2 indica não conformidade ou inconclusividade do escopo; código 3 indica argumentos, configuração, conexão, execução ou encerramento da sessão sem comprovação. `--help` retorna 0 sem diagnóstico.

O script valida schema configurado antes de conectar, abre uma sessão dedicada, inicia `START TRANSACTION READ ONLY`, confere versão, porta, banco e UUID, lê o contrato estrutural e só então considera o agregado selecionado. Schema ausente, não conforme ou inconclusivo impede o agregado. A allowlist SQL não mudou; não há SQL dinâmico, lista de seleções, laço operacional nem retry. `rollback()` e `close()` são tentados no `finally`; falha no encerramento de uma execução sem outro erro impede código 0.

## Limites e custo

| Limite atual | Valor | Alcance real |
| --- | --- | --- |
| `connect_timeout` PyMySQL | 5 s | Abertura da conexão; não cobre DNS/ambiente inteiro com prazo rígido. |
| `read_timeout` PyMySQL | 15 s | Leituras de rede por operação, inclusive metadados; não é orçamento total. |
| `write_timeout` PyMySQL | 5 s | Escritas de protocolo do cliente, mesmo em transação read-only. |
| `MAX_EXECUTION_TIME` solicitado em cada SELECT agregado | 5.000 ms | Hint do servidor para a instrução; não garante limite de tempo de parede, CPU/I/O ou ausência de metadata locks. |
| `LIMIT 101` em quatro agregações | Até 101 grupos/IDs anormais internos | Não limita linhas examinadas para provar ausência de anomalias. |

O fracionamento evita cinco agregações no mesmo processo, mas cada consulta ainda pode ser cara e a sequência de identidade/metadados tem custo próprio. A transação read-only pode reter metadata locks até o rollback. Nenhum timeout é um teto rígido de custo ou latência total. A matriz por consulta e as provas ainda faltantes estão em [`oc73_sprint_3_6_2_avaliacao_custo.md`](oc73_sprint_3_6_2_avaliacao_custo.md).

UNIQUE de CTRC/referência e FK podem tornar algumas contagens fisicamente redundantes **sob condições específicas**, mas não comprovam normalização lógica do CTRC, semântica de referência ou integridade de importações históricas. Nenhuma verificação foi removida ou substituída nesta Sprint.

## Exemplos futuros — NÃO EXECUTAR NESTA SPRINT

Pré-condições para qualquer exemplo: autorização operacional nova, conta dedicada somente leitura, identidade esperada obtida por fonte aprovada, gates de envio bloqueados, janela e orçamento aprovados pelo DBA. Fornecer `AUTOMATION_DB_*` apenas ao ambiente do processo por mecanismo seguro. Não registrar o valor do UUID ou credenciais em logs. Não encadear comandos nem repetir automaticamente após erro.

```powershell
# ETAPA ESTRUTURAL FUTURA — NÃO EXECUTAR AGORA
python -m scripts.diagnostico_oc73_operacional_readonly --somente-leitura --apenas-schema --resumo --server-uuid-esperado $env:OC73_SERVER_UUID_APROVADO
```

```powershell
# UMA AGREGAÇÃO FUTURA, SOMENTE APÓS APROVAÇÃO ESPECÍFICA — NÃO EXECUTAR AGORA
python -m scripts.diagnostico_oc73_operacional_readonly --somente-leitura --verificacao duplicidades_scheduler --confirmar-agregacao --resumo --server-uuid-esperado $env:OC73_SERVER_UUID_APROVADO
```

Outro identificador exige decisão e invocação manual separadas. Não automatizar uma sequência das cinco verificações. Os exemplos históricos sem seletor em `oc73_diagnostico_operacional_readonly.md` e `oc73_sprint_3_6_1_blindagem.md` agora são recusados antes da conexão.

## Verificações offline

`python -m pytest -q tests/test_diagnostico_oc73_operacional_readonly.py`: **77 passed**. Fakes verificam cinco seleções isoladas, argumentos inválidos/repetidos/conflitantes, ausência de confirmação, modo histórico bloqueado antes da conexão, identidade, contrato de schema, timeout, permissão negada, resultado inconclusivo, seleção não executada sem aprovação, saída sanitizada, regressão `--apenas-schema`, rollback e fechamento. Nenhum teste abre conexão real.

Permanecem pendentes: plano e custo medidos pelo DBA, grants reais da futura conta, comportamento de metadata locks e avaliação de dados históricos. Código 0 de uma seleção não libera OC73 nem substitui o restante da auditoria.
