# OC73 Sprint 3.6.2 — avaliação estática de custo e plano de consulta

**Parecer: APTO PARA PLANEJAR CONSULTA CONTROLADA; execução ainda BLOQUEADA.** Esta análise examinou apenas arquivos locais. Não houve conexão MySQL, `EXPLAIN`, `COUNT(*)`, execução do diagnóstico, nem alteração das cinco verificações. O custo real no Percona/MySQL 5.7 compartilhado continua desconhecido.

## Fontes e evidências disponíveis

- SQL executável: `SQL_FIXO` em `scripts/diagnostico_oc73_operacional_readonly.py`, linhas 145–198. Identificadores exatos: `duplicidades_scheduler`, `duplicidades_ctrc`, `duplicidades_referencia`, `tentativas_orfas`, `integridade_estados`.
- A auditoria de schema informada na Sprint 3.5 confirmou InnoDB, PKs, `uq_oc73_ctrc(serie, numero, codigo_ocorrencia)`, `uq_oc73_referencia(referencia)` com `ascii_bin` e FK `oc73_tentativas.execucao_id → oc73_execucoes.id` com RESTRICT. As colunas da chave CTRC são NOT NULL no contrato validado; `referencia` é nullable. Não há UNIQUE por fluxo/data.
- O inventário de metadados do script lê `TABLE_ROWS` e `CARDINALITY` (`SQL_FIXO`, linhas 98–120); ambos são estimativas do InnoDB, não volume exato ou plano de execução. Não foram apresentados tamanhos, cardinalidades ou tempos reais nesta Sprint.
- A Sprint 3.6.1 corrigiu código de saída e resumo, mas não alterou o SQL. A análise anterior está em `docs/oc73_sprint_3_6_auditoria_estatica.md` e `docs/oc73_sprint_3_6_1_blindagem.md`.

## Matriz das cinco consultas

`N_e` e `N_t` denotam, respectivamente, linhas de execuções e tentativas; são variáveis conceituais, **não medições do banco**. Possibilidade de full scan, temporary table e filesort é hipótese de plano, não observação. Nenhuma alternativa abaixo foi implementada.

| SQL fixo | Tabelas; predicado/agrupamento | Índice comprovado e plano possível | Custo conforme volume e alternativa semanticamente delimitada |
| --- | --- | --- | --- |
| `duplicidades_scheduler` | `oc73_execucoes`; `triggered_by IN ('scheduler:CWB','scheduler:BIG')`; `GROUP BY data_referencia, triggered_by HAVING COUNT(*) > 1`; subconsulta `LIMIT 101`. | PK `id`; nenhum índice composto para filtro/grupo foi comprovado. Full scan dos registros pertinentes, temporary table e filesort são possíveis. | Para provar zero duplicatas, pode examinar até `N_e` linhas; memória/ordenação podem crescer com grupos. Um índice cobrindo `(triggered_by, data_referencia)` poderia reduzir trabalho, mas exige decisão de schema separada. Trocar a contagem por mera existência preservaria somente o gate zero/não zero, **não** as contagens atuais; requer revisão de contrato. |
| `duplicidades_ctrc` | `oc73_tentativas`; sem filtro; `GROUP BY serie, numero, codigo_ocorrencia HAVING COUNT(*) > 1`; `LIMIT 101`. | `uq_oc73_ctrc` cobre as três colunas na ordem auditada. Index scan é possível; full scan, temporary table ou filesort dependem do plano e não estão descartados. | Mesmo usando índice, prova de ausência pode custar até `N_t` entradas. Uma prova estrutural do UNIQUE íntegro e das colunas NOT NULL pode substituir **a prova de duplicata física exata** no mesmo schema/instante, mediante revisão explícita; não substitui auditoria de identidade lógica. |
| `duplicidades_referencia` | `oc73_tentativas`; `referencia IS NOT NULL AND referencia <> ''`; `GROUP BY referencia HAVING COUNT(*) > 1`; `LIMIT 101`. | `uq_oc73_referencia(referencia)` com `ascii_bin`. Index scan/range é possível; temporary table/filesort dependem do plano. | Pode percorrer grande fração de `N_t`. UNIQUE prova unicidade física dos valores não nulos sob sua comparação; múltiplos `NULL` são permitidos e já excluídos do SQL. Prova estrutural pode substituir apenas essa propriedade exata, após revisão explícita. Não prova semântica, origem ou correspondência com SSW. |
| `tentativas_orfas` | `oc73_tentativas t LEFT JOIN oc73_execucoes e ON e.id=t.execucao_id WHERE e.id IS NULL`; subconsulta `LIMIT 101`. | PK de `e.id` e FK auditada. O InnoDB requer suporte de índice no lado filho, mas o índice escolhido e o plano real não foram observados. Scan de `t` com probes em `e` é possível; temporary table depende do plano. | Sem órfãs, pode examinar até `N_t` tentativas. `NOT EXISTS` é semanticamente equivalente dado `e.id` PK NOT NULL, mas não garante menor custo. A FK íntegra e ativa pode servir como prova estrutural condicionada; não assumir que elimina a necessidade de investigar dados históricos, importações com checagem referencial desativada ou divergência entre instantes auditados. |
| `integridade_estados` | Duas subconsultas `COUNT(*)`, uma por tabela; `status IS NULL OR BINARY status NOT IN (...)`; sem `LIMIT`. | PKs comprovadas; índice seletivo em `status` não foi comprovado. Predicado negativo e `BINARY` podem impedir acesso seletivo. Full scan de ambas as tabelas é plausível. | Trabalho potencial da ordem de `N_e + N_t` e contagem integral, sem limite de linhas. Reescrita por distribuição de status em índice só seria equivalente se preservar comparação binária, `NULL` e contagem exata; sem índice/plano comprovados não há redução de custo demonstrada. `EXISTS` com parada no primeiro inválido preservaria apenas conformidade binária, não as duas contagens atuais. |

`LIMIT 101` limita grupos ou IDs **após** filtro, join, agrupamento e `HAVING`; um resultado sem anomalias pode exigir leitura completa. O hint `MAX_EXECUTION_TIME(5000)` de cada SELECT e o `read_timeout` do cliente não formam prazo rígido para a execução inteira: conexão, metadados, espera de metadata lock, rede, cinco instruções, rollback e fechamento ficam fora de um orçamento único. `START TRANSACTION READ ONLY` impede escrita pelo diagnóstico, mas mantém a transação aberta durante as consultas e pode prolongar metadata locks.

## Redundância condicionada e limites das provas

1. **CTRC físico:** o UNIQUE composto, se vigente e íntegro no mesmo schema, impede duas linhas com a mesma chave segundo a comparação/collation de suas três colunas NOT NULL. Isso torna o `GROUP BY` redundante apenas para essa propriedade física. Não comprova que série/número/DV foram normalizados corretamente, que variantes textuais do mesmo CTRC convergem, nem a decisão de identidade entre CWB e BIG. A chave não contém fluxo; mudar essa regra está fora desta Sprint.
2. **Referência física:** o UNIQUE binário impede referências não nulas iguais sob a comparação do índice. `NULL` pode repetir, mas a consulta atual o exclui. Referências diferentes para o mesmo evento, espaços/formatos distintos e validade no SSW não são garantidos. A exclusão de `''` do SQL também deve ser preservada ao comparar contratos.
3. **Órfãos:** FK InnoDB presente e ativa normalmente impede inserções/deleções que deixem órfãos, sob suas regras. A auditoria estática não demonstra que `foreign_key_checks` sempre esteve ativo em importações históricas, nem prova ausência de corrupção ou que o schema não mudou desde a última coleta. Uma substituição formal precisaria demonstrar validade temporal e operacional da FK. Não remover a consulta só porque a definição existe.
4. **Execuções duplicadas:** não há UNIQUE por fluxo/data. `GET_LOCK` protege somente produtores cooperantes; a agregação `duplicidades_scheduler` não tem substituto estrutural equivalente já comprovado.
5. **Estados:** os conjuntos permitidos estão no código, mas o schema não comprova que todas as linhas históricas os respeitam. A contagem segue necessária até existir outra prova de dados equivalente.

**Decisão pendente:** nenhuma das cinco consultas será retirada do contrato atual nesta Sprint. Substituir uma contagem por prova estrutural muda o significado do relatório integral (`quantidade_exata`, `linhas_excedentes_observadas`) e exige proposta de contrato, teste de compatibilidade, revisão do DBA e aprovação explícita.

## Estratégia em estágios, ainda sem execução autorizada

| Etapa | Evidência a obter em autorização futura | Critério de parada |
| --- | --- | --- |
| 1 — identidade, schema e estatísticas | Rodar somente `--apenas-schema --resumo` com conta somente leitura, UUID conhecido por fonte independente e gates bloqueados. O script consulta engine, índices, FK, `TABLE_ROWS` e `CARDINALITY`; a saída resumida não expõe os valores de estatísticas, portanto a coleta e guarda restrita desses metadados exigem procedimento adicional aprovado. Solicitar ao DBA tamanho aproximado, volume, carga, janela, limites de CPU/I/O e política de metadata lock. | Identidade, grants ou schema divergentes; estatísticas ausentes/obsoletas; volume ou janela sem avaliação do DBA. `TABLE_ROWS`/`CARDINALITY` nunca são tratados como contagens exatas. |
| 2 — planos, se suportados e seguros | Com autorização própria e revisão do DBA, obter `EXPLAIN` convencional ou `FORMAT=JSON` para cada SELECT fixo, após repetir checagens de identidade e schema. Não usar `EXPLAIN ANALYZE`, nem executar as consultas. Registrar somente tipo de acesso, índice escolhido, linhas estimadas, `Extra`/temporary/filesort e uso de derived tables, sem valores operacionais. Validar suporte/sintaxe no ambiente aprovado antes. | Plano com varredura ampla, temporary/filesort relevante, estimativa excessiva, demora, lock ou qualquer incerteza de que o mecanismo apenas planejará a instrução. `EXPLAIN` também não mede custo real e pode tomar metadata locks. |
| 3 — agregações indispensáveis | Só após aprovação explícita de plano, volume, janela e orçamento por consulta; conta dedicada somente leitura; uma consulta por vez, ordem e critérios de parada aprovados, sem retries. O script atual executa as cinco agregações em sequência; **não atende sozinho ao fracionamento**. Adaptá-lo e testar offline ou aprovar formalmente o risco da execução integral antes desta etapa. | Timeout, erro/permissão, custo ou duração acima do orçamento, resultado inconclusivo/limitado/não conforme, mudança de identidade/schema ou carga inesperada. Encerrar sessão e não prosseguir automaticamente. |

As cinco instruções podem ser referenciadas pelos identificadores de `SQL_FIXO` acima; não interpolar SQL recebido do operador. A etapa 3 permanece bloqueada porque faltam volume confiável, plano, orçamento e janela aprovados. Um resultado 0 do diagnóstico não valida normalização lógica de CTRC nem autoriza reenvio de tentativas antigas.

## Testes offline preparados para eventual otimização

Nenhuma otimização foi implementada, portanto nenhum teste novo foi necessário nesta Sprint. Antes de uma mudança futura, usar fakes/fixtures sintéticos, sem conexão, para comparar o contrato antigo e o proposto:

| Mudança candidata | Casos mínimos de equivalência e bloqueio |
| --- | --- |
| UNIQUE como prova de ausência de duplicata física | Índice correto, único, sem `SUB_PART`, colunas e ordem corretas, nulabilidade CTRC correta, collation de referência correta; índice ausente, renomeado, parcial, não único, colunas nulas/ambíguas e metadados incompletos devem bloquear. Variantes textuais da mesma identidade devem continuar classificadas como risco lógico, nunca como prova de normalização. |
| FK como prova de ausência de órfãos | FK correta com engine InnoDB, PK e tipos compatíveis; FK ausente, errada ou metadados inconclusivos devem bloquear. Caso histórico sintético com órfão deve demonstrar que a prova estrutural isolada não o detecta; exigir decisão formal sobre essa lacuna. |
| `EXISTS`/reescrita de estados | Zero, um e muitos inválidos; `NULL`, diferenças de caixa e espaços; comparação binária; timeout, permissão negada e resultado parcial. Se o relatório integral ainda prometer contagens exatas, a substituição booleana deve falhar o teste de contrato. |
| Fracionamento da etapa 3 | Identidade e schema antes de qualquer agregado; parada após erro/timeout/anomalia; rollback e fechamento; nenhuma consulta posterior, retry ou conexão operacional no teste. |

## Riscos residuais e decisão necessária

- Não há `EXPLAIN`, cardinalidade real, tamanho exato, tempo de execução ou carga do servidor. É impossível afirmar que qualquer consulta é barata.
- O índice de suporte à FK e os planos escolhidos não foram identificados. Metadados estimativos podem estar desatualizados.
- O diagnóstico atual mantém uma transação read-only por toda a sequência e tenta suprimir erros de rollback/fechamento; custo de locks e liberação efetiva de sessão ainda requerem atenção operacional.
- Substituição de agregações por provas estruturais alteraria o contrato e não cobriria normalização lógica, dados históricos ou alterações entre coletas.
- Registros antigos `reservado`, `envio_iniciado` e `incerto` não se tornam seguros para reenvio por esta auditoria.

**Próxima decisão:** aprovar ou não a coleta controlada da etapa 1, separadamente desta análise; depois revisar seus resultados antes de autorizar `EXPLAIN` ou agregações. Nenhum gate de envio deve ser alterado.
