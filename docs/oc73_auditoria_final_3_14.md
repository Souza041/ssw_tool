# Sprint 3.14 — Auditoria final de segurança e prontidão OC73

Data da auditoria: 15/10/2026  
Versão auditada: `02b60c0`  
Escopo: análise estática, testes offline e preparação de evidências. Nenhum POST
SSW, acesso a banco, migration, deploy, commit ou push foi executado.

## Parecer executivo

- **APTO COM RESTRIÇÕES para homologação supervisionada**, mas não para execução
  imediata. Antes do único lançamento autorizado, são obrigatórias a validação
  somente leitura do esquema real, a autorização operacional separada e a
  seleção formal do CTRC de teste.
- **NÃO APTO para ativação automática em produção**. Ainda faltam a comprovação
  de que a OP101 preserva integralmente `REF:OC73-...`, a evidência do DDL e das
  constraints reais, e uma decisão formal sobre unicidade de execução no banco.

O parecer não autoriza alteração de flags nem qualquer lançamento.

## Escala de evidência

- **COMPROVADO**: demonstrado no código e/ou por teste offline reproduzido nesta
  auditoria.
- **COMPROVADO EM LABORATÓRIO**: exige execução atual no laboratório isolado; não
  houve item com esta classificação nesta auditoria porque o laboratório não
  estava configurado.
- **PENDENTE DE VALIDAÇÃO REAL**: depende do MySQL/SSW real ou de autorização
  operacional externa.
- **NÃO CONFORME**: requisito violado.

## 1. Banco e concorrência

| Conclusão | Classificação | Evidência e observação |
|---|---|---|
| SQL usado pelo fluxo é compatível, por análise estática, com MySQL 5.7.32 | **COMPROVADO** | Não há CTE, window function, `RETURNING` ou sintaxe exclusiva de MySQL 8. São usados `INSERT`, `UPDATE`, `SELECT ... FOR UPDATE`, `GET_LOCK`, `RELEASE_LOCK` e `information_schema`. A execução contra a versão exata permanece pendente. |
| DDL, engine, collation, PK, FK e índices efetivos | **PENDENTE DE VALIDAÇÃO REAL** | Não existe DDL completo versionado para as duas tabelas e não havia credencial dedicada configurada. O diagnóstico agora coleta `SHOW CREATE TABLE`, metadata, constraints e regras referenciais em `scripts/diagnostico_oc73_mysql.py:159-239`. |
| Reserva de CTRC é atômica quando `uq_oc73_ctrc` existe | **COMPROVADO** | O `INSERT` em `oc73_tentativas` ocorre em transação e a colisão 1062 da constraint esperada bloqueia a nova reserva (`repository.py:293-384`). A existência e o nome da constraint no banco real estão pendentes. |
| Referência é persistida antes do envio | **COMPROVADO** | A referência é criada no `INSERT` da reserva; só depois, em outra transação, ocorre `reservado -> envio_iniciado` (`repository.py:293-411`; `gate.py:42-78`). Falha/commit ambíguo antes do retorno impede o callback de envio. |
| Reserva agendada não possui janela check/insert entre processos cooperantes | **COMPROVADO** | `GET_LOCK`, busca e `INSERT` usam a mesma conexão; o commit ocorre na linha 186 antes do `RELEASE_LOCK` nas linhas 197-208 de `repository.py`. |
| Reinício não repete linha existente, inclusive `executando` | **COMPROVADO** | Qualquer registro existente para `data_referencia + scheduler:fluxo` bloqueia nova reserva, sem reclassificar o estado (`repository.py:144-165`). |
| Unicidade de execução resiste a produtores não cooperantes | **PENDENTE DE VALIDAÇÃO REAL** | Hoje a garantia depende do protocolo `GET_LOCK`. Sem UNIQUE equivalente, outro produtor que ignore o lock pode inserir duplicata. |
| CWB e BIG não se bloqueiam entre si | **COMPROVADO** | Os nomes incluem fluxo e data (`repository.py:119-123`); os jobs e testes são separados. |
| Queda/perda de conexão falha fechada | **COMPROVADO** | Erros provocam rollback/fechamento; conexão fechada libera named lock. Um commit de resultado ambíguo pode deixar reserva persistida, que bloqueia retry — favorece falso bloqueio, não duplicidade. |

### Estrutura esperada inferida pelo código

`oc73_execucoes` precisa conter, no mínimo: `id`, `data_referencia`,
`triggered_by`, `status`, `dry_run`, `iniciado_em`, `finalizado_em`, totais e
`mensagem_erro`. `oc73_tentativas` precisa conter: `id`, `execucao_id`, `serie`,
`numero`, `codigo_ocorrencia`, `fluxo`, datas de pesquisa, estados/timestamps,
`referencia`, `resposta_ssw` e `mensagem_erro`.

As constraints esperadas pelo código são:

- `uq_oc73_ctrc (serie, numero, codigo_ocorrencia)`;
- `uq_oc73_referencia (referencia)` com comparação binária/ASCII;
- PKs em `id` e, preferencialmente, FK de `oc73_tentativas.execucao_id` para
  `oc73_execucoes.id`.

O código interpreta os nomes das duas UNIQUE no texto do erro 1062. Um índice
equivalente com outro nome falha de forma segura, mas interrompe a operação.

### Avaliação de `UNIQUE (data_referencia, fluxo)`

`oc73_execucoes` não recebe uma coluna `fluxo`; o fluxo agendado é codificado em
`triggered_by` como `scheduler:CWB` ou `scheduler:BIG`. Portanto a proposta não é
aplicável ao esquema esperado sem nova coluna e backfill.

Uma UNIQUE direta em `(data_referencia, triggered_by)` também atingiria execuções
manuais, impedindo duas linhas `manual` no mesmo dia. A alternativa mais segura é
uma chave explícita ou coluna gerada anulável somente para linhas do scheduler,
depois de:

1. obter o DDL real;
2. medir duplicidades históricas sem alterá-las;
3. validar geração/índice em MySQL 5.7.32 e no laboratório;
4. definir backfill, janela, impacto de lock e rollback;
5. adaptar/testar o tratamento de 1062 da reserva agendada.

Nenhuma migration foi criada ou executada nesta sprint.

### Limitações de `GET_LOCK`

- é uma trava da sessão e do servidor MySQL, não uma constraint permanente;
- protege somente produtores que usem exatamente o mesmo nome/protocolo;
- não oferece garantia global se conexões forem distribuídas entre servidores
  MySQL independentes;
- commit/rollback não libera a trava; por isso o `finally` e o fechamento da
  conexão são essenciais;
- a perda da sessão libera a trava, mas o registro já commitado continua sendo a
  barreira após reinício;
- falha de `RELEASE_LOCK` é suprimida e seguida de `close()`. Isso é seguro contra
  duplicidade, mas deve gerar alerta operacional se observável.

## 2. Segurança do lançamento e OP101

| Conclusão | Classificação | Evidência e observação |
|---|---|---|
| Formato global `OC73-` + 20 hex maiúsculos | **COMPROVADO** | `referencia.py:5-11`; geração por `secrets.token_hex(10).upper()` em `repository.py:293`. |
| Observação obrigatória, única e até 70 caracteres | **COMPROVADO** | `lancar_ocorrencia_73` recusa mais de 70 caracteres, ausência/duplicidade de `REF:` e referência inválida (`operations/op101/ocorrencias.py:747-774`). A observação produzida tem 40 caracteres (`service.py:909-914`). |
| Nenhum caminho operacional contorna `PortaoOC73` | **COMPROVADO** | A única chamada operacional de `lancar_ocorrencia_73()` parte do callback do portão (`service.py:897-925`). Demais chamadas são testes. |
| HTTP positivo não confirma lançamento | **COMPROVADO** | O portão sempre consulta o histórico após o callback e só confirma OC73 + referência exata (`gate.py:77-96`; `referencia.py:15-40`). |
| Timeout, conexão, resposta ambígua e falha de histórico não causam retry | **COMPROVADO** | O estado vira `incerto`, ou permanece `envio_iniciado` em queda abrupta; reservas existentes e transições repetidas são bloqueadas. Cobertura em `test_oc73_falhas_http.py`, `test_oc73_gate.py` e `test_oc73_reconciliacao.py`. |
| Reconciliação não envia e não libera reenvio | **COMPROVADO** | Somente `envio_iniciado`/`incerto` são consultados e `permite_reenvio` permanece falso (`reconciliacao.py:12-112`). |
| Parser OP101 preserva os campos necessários | **COMPROVADO** | Regressão sintética cobre `f0`, `f1`, `f2`, `f5`, `f6`, `f10` e `f12` em `tests/test_oc73_op101_validacao.py:13-96`. OC73 antiga existe para elegibilidade, mas não confirma uma tentativa nova sem referência. |
| Layout real da OP101 e preservação integral da referência | **PENDENTE DE VALIDAÇÃO REAL** | Não há captura real anonimizada no repositório; a fixture declara ser sintética. Só o roteiro supervisionado pode comprovar que o SSW não trunca nem altera `f6`. |

## 3. Scheduler e regras operacionais

| Conclusão | Classificação | Evidência e observação |
|---|---|---|
| Timezone e horários | **COMPROVADO** | `America/Sao_Paulo`, 23h30; CWB `mon..fri,sun`, BIG `mon..thu` (`calendario.py:6-31`; `scheduler.py:22-25,148-166`). |
| Domingo CWB inclui sábado/domingo; sábado não executa | **COMPROVADO** | `calendario.py:67-90`, com testes de virada de mês/ano. |
| Reinício não dispara passado indevido | **COMPROVADO** | Jobs são recriados em memória com `CronTrigger`; teste confirma próximo disparo futuro. Há tolerância explícita de misfire de 60 segundos, não recuperação de dias anteriores. |
| Scheduler desabilitado por padrão | **COMPROVADO** | Ausência ou valor não verdadeiro de `OCORRENCIA_73_ENABLED` retorna antes de criar jobs (`scheduler.py:29-56,133-139`). |
| Gate de envio independente e desligado por padrão | **COMPROVADO** | Apenas o valor exato `SIM` habilita `OC73_ENVIO_HABILITADO` (`service.py:50-56`); o bloqueio ocorre antes do portão/POST (`service.py:884-897`). |
| DRY_RUN não autoriza POST | **COMPROVADO** | `DRY_RUN` desvia o item para `pendente_lancamento` antes do gate (`service.py:874-878`). |
| DRY_RUN agendado é totalmente sem escrita | **NÃO CONFORME** | Por desenho atual, o scheduler chama `reservar_execucao_agendada(..., dry_run=True)` e depois finaliza `oc73_execucoes`. Não há POST nem tentativa de CTRC, mas há INSERT/UPDATE de auditoria no banco. O modo manual DRY_RUN sem reserva não exige banco (`service.py:585-592`). |
| Falha CWB não impede BIG | **COMPROVADO** | Jobs separados; falha é capturada por invocação. Teste offline dedicado em `test_oc73_scheduler.py`. |

## 4. Diagnóstico somente leitura

O utilitário foi endurecido nesta sprint:

- recusa grants fora de `USAGE`, `SELECT` e `SHOW VIEW`, sem ecoar seu conteúdo;
- consulta engine/collation, colunas, índices, constraints, FKs/regras e DDL;
- procura duplicidades de scheduler, CTRC/código e referência;
- procura tentativas órfãs;
- mantém SQL fixo e parametrizado; os únicos identificadores interpolados vêm da
  constante interna `TABELAS_OC73`;
- fecha conexão em `finally`; o teste opcional de named lock continua restrito ao
  laboratório validado.

### Provisionamento pendente para administrador autorizado

Executar administrativamente, substituindo host e segredo sem registrá-los em
shell history ou repositório:

```sql
CREATE USER 'oc73_auditor'@'<host_da_auditoria>' IDENTIFIED BY '<segredo_forte>';
GRANT SELECT ON automationx.oc73_execucoes
  TO 'oc73_auditor'@'<host_da_auditoria>';
GRANT SELECT ON automationx.oc73_tentativas
  TO 'oc73_auditor'@'<host_da_auditoria>';
```

Depois, carregar `OC73_DIAGNOSTICO_DB_HOST`, `..._PORT`, `..._NAME`, `..._USER`
e `..._PASSWORD` por mecanismo secreto e executar, sem a opção de lock:

```text
python scripts/diagnostico_oc73_mysql.py --detalhar-esquema
```

Se o validador recusar os grants, não flexibilizá-lo: provisionar outra conta.
Revogar/remover a conta quando a política operacional exigir.

## 5. Matriz de riscos

| ID | Risco | Severidade | Situação | Mitigação exigida |
|---|---|---:|---|---|
| R1 | UNIQUE/FK/DDL real diferente do esperado | Crítica | Pendente | Diagnóstico com conta dedicada somente leitura; comparar nomes, ordem, collation e FK. |
| R2 | SSW truncar/normalizar a referência em `f6` | Crítica | Pendente | Um único lançamento supervisionado e evidência OP101 antes/depois. |
| R3 | Duplicidade de execução por produtor que ignore `GET_LOCK` | Alta | Aberto | Decidir backstop UNIQUE condicional após medir histórico e testar MySQL 5.7. |
| R4 | Named lock não cobrir múltiplos servidores MySQL | Alta | Aberto | Confirmar topologia/roteamento; usar um único servidor lógico ou constraint no banco. |
| R5 | Credencial de diagnóstico possuir escrita | Alta | Mitigado no código | O script agora recusa grant mutável; ainda falta provisionar a conta real. |
| R6 | Registro `executando` órfão bloquear futuras execuções | Média | Aceito por segurança | Runbook de investigação/reconciliação; nunca apagar/reabrir automaticamente. |
| R7 | DRY_RUN agendado escrever auditoria/reserva | Média | Conhecido | Tratar como escrita autorizada de auditoria; não usar scheduler se for exigido zero escrita. |
| R8 | Falha de `RELEASE_LOCK` ficar sem alerta específico | Média | Aberto | Instrumentar métrica/log sem mudar a decisão fail-closed; `close()` continua obrigatório. |
| R9 | Misfire executar até 60 s atrasado | Baixa | Configurado | Aceitar formalmente a tolerância ou reduzi-la em mudança separada/testada. |

## 6. Checklist Go/No-Go da homologação supervisionada

Qualquer item “não” resulta em **NO-GO**.

- [ ] Autorização operacional explícita, separada e limitada a um POST.
- [ ] Responsável primário e segundo operador identificados.
- [ ] CTRC de teste aprovado, elegível e sem impacto indevido documentado.
- [ ] Scheduler confirmado desligado no ambiente efetivo.
- [ ] Escopo limitado ao CTRC e `MAX_LANCAMENTOS=1` confirmado.
- [ ] DDL/índices/grants reais validados por conta somente leitura.
- [ ] Zero duplicidade de scheduler, CTRC/código e referência no diagnóstico.
- [ ] `uq_oc73_ctrc` e `uq_oc73_referencia` presentes com nomes/ordem esperados.
- [ ] Histórico OP101 anterior capturado e sem OC73 conflitante.
- [ ] Referência persistida em estado `reservado` antes do POST.
- [ ] Observação final possui exatamente um `REF:` e até 70 caracteres.
- [ ] Plano de interrupção e canal de decisão em timeout disponíveis.
- [ ] Nenhum mecanismo de retry automático ativo.
- [ ] Evidências serão armazenadas com acesso restrito e sem credenciais/cookies.

## 7. Roteiro de homologação supervisionada

1. Registrar autorização, janela, operadores e CTRC aprovado.
2. Capturar flags efetivas e comprovar scheduler desligado. Não alterar flags por
   painel/código.
3. Executar o diagnóstico somente leitura e anexar versão, identidade, grants
   saneados, DDL, índices, constraints, duplicidades e órfãos.
4. Consultar OP101 antes; registrar a ausência de OC73 conflitante.
5. Pelo fluxo normal do `PortaoOC73`, persistir execução/tentativa e capturar ID,
   estado `reservado`, referência e observação completa.
6. Fazer pausa de conferência dupla. Se qualquer dado divergir, abortar sem POST.
7. Sob a autorização separada, realizar exatamente um POST.
8. Tratar HTTP somente como transporte; consultar OP101 e comparar a referência
   completa, caractere por caractere, dentro de ocorrência 73.
9. Aprovar somente se houver um POST, referência integral e estado `confirmado`.
10. Em timeout, conexão perdida, resposta ambígua ou consulta indisponível,
    interromper, manter `envio_iniciado`/`incerto`, proibir retry e iniciar apenas
    reconciliação de leitura.

Reprovar se houver truncamento/alteração, confirmação apenas por HTTP, segundo
POST, mudança manual para `reservado` ou necessidade de contornar o gate.

## 8. Rollback e recuperação

Não existe rollback seguro de um POST cujo efeito seja incerto. O procedimento é:

1. desligar/confirmar desligado o scheduler e preservar os estados;
2. não alterar `envio_iniciado`, `incerto` ou `confirmado` para permitir reenvio;
3. consultar OP101 por CTRC e referência exata;
4. se encontrada, reconciliar somente para `confirmado`;
5. se ausente ou indisponível, manter bloqueado e escalar ao operador;
6. preservar logs/respostas saneados e IDs para análise.

Para rollback de aplicação futuro: restaurar a versão anterior do código e a
configuração previamente registrada, validar segredo/sessão antes do restart e
manter scheduler/gate desligados. Não reverter dados OC73 automaticamente.

Uma eventual migration de unicidade deverá possuir backup, teste em laboratório,
janela aprovada e rollback limitado ao índice/coluna introduzido por ela; nunca
deverá apagar duplicidades históricas automaticamente.

## 9. Testes e evidências reproduzidas

| Comando | Resultado atual |
|---|---|
| `python -m pytest tests/test_oc73_observabilidade.py -q` | **39 passed** |
| `python -m pytest -q` | **225 passed, 16 skipped**, 2 avisos de depreciação FastAPI `on_event` |
| `python -m compileall -q modules/ocorrencia_73 operations/op101 scripts/diagnostico_oc73_mysql.py web` | sucesso |
| `git diff --check` | sucesso; apenas avisos informativos de conversão LF/CRLF |

Os 16 testes ignorados são integrações MySQL. Nesta sessão,
`OC73_MYSQL_TEST`, `AUTOMATION_DB_*` e `OC73_DIAGNOSTICO_DB_*` estavam ausentes;
portanto não houve conexão nem escrita em banco. Nenhum resultado foi classificado
como “laboratório”.

## 10. Bloqueios para produção

1. **PENDENTE DE VALIDAÇÃO REAL** — DDL, engine, constraints, FKs, índices,
   duplicidades e órfãos no banco efetivo.
2. **PENDENTE DE VALIDAÇÃO REAL** — conta dedicada somente leitura.
3. **PENDENTE DE VALIDAÇÃO REAL** — teste na versão exata MySQL 5.7.32 ou ambiente
   fiel a ela.
4. **PENDENTE DE VALIDAÇÃO REAL** — preservação integral de `REF:OC73-...` na
   OP101 após um lançamento formalmente autorizado.
5. **PENDENTE DE DECISÃO** — UNIQUE condicional de fluxo/data ou aceitação formal
   da dependência exclusiva em `GET_LOCK`.
6. **NÃO CONFORME para requisito “zero escrita em DRY_RUN agendado”** — a reserva
   e finalização de auditoria são gravadas; isso precisa ser aceito ou redesenhado
   antes de usar o scheduler em DRY_RUN.

