# OC73 — relatório de prontidão para homologação supervisionada 3.15

Data da revisão: 16/10/2026

Escopo: análise estática, testes offline e preparação documental. Nenhuma
conexão ao MySQL operacional ou ao SSW foi realizada nesta sprint.

> Adendo 3.15.1: o protótipo offline em
> `prototypes/oc73_homologacao_supervisionada.py` demonstra autorização assinada,
> consumo único em filesystem local e contagem no transporte falso. Ele não está
> integrado ao runtime e não altera os pareceres de homologação real ou produção.

## 1. Resumo executivo

**Parecer:** **GO para simulação offline; GO para solicitar autorização de
homologação supervisionada; NO-GO para executar o teste real sem nova autorização;
NO-GO para produção automática.**

- **COMPROVADO:** os caminhos operacionais ativos encontrados chamam o POST de
  lançamento da OP101 somente por meio de `PortaoOC73`; reserva, referência e
  transição para `envio_iniciado` são persistidas antes do callback de envio.
- **COMPROVADO:** timeout, falha de conexão, resposta HTTP ambígua, histórico
  indisponível e queda abrupta não autorizam segundo POST automático.
- **COMPROVADO:** confirmação exige ocorrência de código 73 e referência exata no
  complemento do histórico; resposta HTTP positiva não é confirmação.
- **COMPROVADO:** o scheduler usa trava nomeada independente por fluxo/data na
  mesma conexão que verifica e grava a reserva, faz commit antes de liberar a
  trava e mantém a execução persistida após reinício.
- **COMPROVADO:** a coleta operacional pontual informada pela Sprint 3.14.3
  encontrou zero duplicidades e ausência de UNIQUE adicional por fluxo/data.
  Isso não garante o estado futuro.
- **PENDENTE:** o SSW real ainda não comprovou que preserva integralmente
  `REF:OC73-[0-9A-F]{20}` no campo `f6` exibido no histórico OP101.
- **INFERIDO:** `GET_LOCK` protege processos cooperantes ligados ao mesmo servidor
  e que usam exatamente o mesmo nome de trava. Sem UNIQUE, escrita externa ou
  caminho futuro que ignore o protocolo pode criar duplicidade fluxo/data.
- **RECOMENDAÇÃO:** manter `GET_LOCK` e adicionar, em mudança futura separada, um
  backstop UNIQUE seletivo por coluna gerada anulável para execuções agendadas.
  A migration deve ser validada no laboratório MySQL 5.7.32 e em janela própria;
  não integra esta sprint.

## 2. Arquitetura observada

### 2.1 Diagrama textual do fluxo

```text
startup FastAPI
  └─ se OCORRENCIA_73_ENABLED=true: APScheduler local
       ├─ job CWB 23:30 America/Sao_Paulo
       └─ job BIG 23:30 America/Sao_Paulo
            │
            ▼
calendário por fluxo/data
  └─ reservar_execucao_agendada
       ├─ abre conexão MySQL dedicada
       ├─ GET_LOCK("ssw_tool:oc73:<FLUXO>:<DATA>", 0)
       ├─ SELECT de reserva existente
       ├─ INSERT oc73_execucoes(status=executando)
       ├─ COMMIT
       └─ RELEASE_LOCK na mesma conexão; close em finally
            │
            ▼
Ocorrencia73Service.executar(execucao_id_reservada)
  ├─ login SSW e abertura de menu
  ├─ OP455: solicitar/baixar relatório CWB ou BIG
  ├─ gravar relatório e arquivos locais de auditoria
  ├─ parser + filtros existentes + deduplicação série/número
  └─ para cada CTRC
       ├─ OP101: localizar CTRC e consultar histórico
       ├─ se já existe qualquer OC73: não lançar
       ├─ se DRY_RUN: apenas registrar pendência simulada
       ├─ aplicar CTRC_TESTE, MAX_LANCAMENTOS e OC73_ENVIO_HABILITADO
       └─ PortaoOC73
            ├─ INSERT oc73_tentativas(status=reservado, referência)
            ├─ COMMIT da referência
            ├─ UPDATE condicional reservado → envio_iniciado; COMMIT
            ├─ um POST /bin/ssw0122 com f6="BOT OC73 | REF:<referência>"
            ├─ consultar novamente o histórico OP101
            ├─ código 73 + referência exata → confirmado
            └─ qualquer incerteza → incerto; nunca reenvio automático

reconciliação conservadora, quando invocada explicitamente
  └─ somente consulta histórico de envio_iniciado/incerto
       ├─ referência exata encontrada → confirmado
       └─ ausente/erro → estado preservado; permite_reenvio=false

painel de observabilidade
  └─ autenticação + allowlist → SELECTs limitados; nenhum job ou reconciliação
```

### 2.2 Componentes e evidências

| Componente | Evidência | Classificação |
|---|---|---|
| Calendário CWB/BIG | `calendario.py:10-35,54-108`; testes em `test_oc73_calendario.py` | **COMPROVADO** |
| Scheduler desabilitado por padrão | `scheduler.py:133-139`; `env_bool(..., False)` | **COMPROVADO** |
| Timezone e cron | `scheduler.py:22-25,148-166` | **COMPROVADO** |
| Reserva fluxo/data | `repository.py:99-220` | **COMPROVADO** |
| Reserva de CTRC e referência | `repository.py:271-384` | **COMPROVADO** |
| Máquina de estados | `repository.py:387-486`; `gate.py:43-117` | **COMPROVADO** |
| Lançamento e confirmação OP101 | `ocorrencias.py:726-913` | **COMPROVADO por código e mocks** |
| Persistência real da referência no SSW | não há captura real anonimizada; fixture declarada sintética | **PENDENTE** |
| Painel somente leitura | `routes.py:40-162`; `observabilidade.py:106-370` | **COMPROVADO por código e testes** |

## 3. Pontos de escrita e efeitos externos

### 3.1 MySQL

- `oc73_execucoes`: INSERT de início/reserva e UPDATE de fechamento em
  `repository.py:71-97,169-203,222-258`.
- `oc73_tentativas`: INSERT da reserva, UPDATE para `envio_iniciado`,
  `confirmado` ou `incerto` em `repository.py:271-486`.
- `GET_LOCK`/`RELEASE_LOCK`: efeitos de sessão, não DML, em
  `repository.py:128-218`.
- **COMPROVADO:** o painel executa somente SELECT e fecha a conexão.
- **COMPROVADO:** DRY_RUN manual sem reserva não escreve no banco
  (`service.py:585-592`).
- **COMPROVADO:** DRY_RUN agendado ainda grava a reserva/auditoria em
  `oc73_execucoes`, pois o scheduler reserva antes de chamar o serviço
  (`scheduler.py:86-111`). DRY_RUN impede o POST de ocorrência, mas não significa
  ausência total de escrita de auditoria.

### 3.2 SSW e rede

- login e menu do SSW: `service.py:93-153`;
- OP455 gera relatório por POST e baixa por GET/OP156:
  `operations/op455/report.py:338-469`;
- OP101 usa POSTs para navegação/consulta do CTRC e histórico; são requisições
  HTTP com semântica consultiva no fluxo, mas ainda efeitos externos;
- o único POST de lançamento de ocorrência é `/bin/ssw0122`,
  `operations/op101/ocorrencias.py:798-839`.

### 3.3 Sistema de arquivos

- criação de `downloads/ocorrencia_73/<data>/original|auditoria`;
- gravação do relatório OP455, CSVs de auditoria e JSON de resultado
  (`service.py:155-188,369-469,715-775`).

## 4. Gate, caminhos alternativos e fail-closed

### 4.1 Caminhos encontrados

- **COMPROVADO:** a busca por chamadas no código ativo encontrou uma chamada de
  `OP101Ocorrencias.lancar_ocorrencia_73`, em `service.py:909`, dentro do callback
  entregue ao `PortaoOC73` criado em `service.py:897`.
- **COMPROVADO:** as rotas OC73 expõem apenas observabilidade; não executam o
  serviço, scheduler, reconciliação ou lançamento.
- **COMPROVADO:** `service.py.bak` não é importado e não é caminho operacional.
- **INFERIDO/RISCO MÉDIO:** `lancar_ocorrencia_73` é método público e não conhece
  o gate ou `OC73_ENVIO_HABILITADO`. Código novo, console administrativo ou
  script futuro poderia chamá-lo diretamente. Hoje não há bypass ativo no
  repositório; a garantia depende de revisão de chamadas e disciplina de
  aplicação. Recomenda-se, em sprint futura, encapsular o cliente de escrita ou
  exigir uma capacidade interna emitida pelo gate, com testes de arquitetura.

### 4.2 Invariantes de estado

| Invariante | Evidência | Resultado |
|---|---|---|
| Referência persiste antes do envio | INSERT e commit de `reservar_ctrc` antecedem `iniciar_envio` e callback | **COMPROVADO** |
| Apenas reserva nova pode enviar | `gate.py:50-60` | **COMPROVADO** |
| Transição é condicional | UPDATE exige `reservado`, referência e timestamp vazio | **COMPROVADO** |
| HTTP positivo não confirma | gate ignora `success` para confirmação e exige histórico exato | **COMPROVADO** |
| Timeout não vira ausência | exceção leva a `incerto`; falha de persistência interrompe | **COMPROVADO** |
| `incerto`/`envio_iniciado`/`confirmado` não reenviam | reserva existente bloqueia gate | **COMPROVADO** |
| Reconciliação não reenvia | `permite_reenvio=False`; nenhuma função de POST | **COMPROVADO** |
| Falha do banco não libera envio | erro de reserva/transição ocorre antes do callback | **COMPROVADO** |
| Observabilidade não altera estado | rotas GET + repository somente SELECT | **COMPROVADO** |

## 5. Proteção por fluxo/data e concorrência

### 5.1 Protocolo atual

- O nome da trava contém fluxo e data; CWB e BIG não se bloqueiam entre si.
- `GET_LOCK`, SELECT, INSERT, commit e `RELEASE_LOCK` usam a mesma conexão.
- O commit acontece antes da liberação da trava.
- A verificação e a inserção não possuem janela concorrente entre processos que
  respeitam o mesmo protocolo.
- Se a conexão cai antes do commit, a transação é revertida e o lock de sessão é
  liberado; nova execução pode reservar.
- Se o commit ocorreu mas o cliente não recebeu confirmação, a linha persistida
  bloqueia a tentativa posterior.
- Se `RELEASE_LOCK` falha, `close()` em `finally` encerra a sessão e libera o lock.
- Execução com status `executando` permanece registrada e bloqueia reinício.
- Manuais usam identificadores distintos (`manual`, `cron`, `teste_periodo` etc.);
  o scheduler usa exclusivamente `scheduler:CWB|BIG`.

O manual MySQL 5.7 confirma que locks nomeados são exclusivos no servidor,
pertencem à sessão, não são liberados por commit/rollback e são liberados ao
encerrar a sessão: [Locking Functions](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/locking-functions.html).

### 5.2 Limitação central

**INFERIDO — risco alto antes de produção automática:** `GET_LOCK` é advisory e
cooperativo. A ausência do UNIQUE significa que um produtor que use outro nome,
ou insira diretamente em `oc73_execucoes`, pode criar duas reservas para o mesmo
fluxo/data. O método genérico `iniciar_execucao` aceita `triggered_by` arbitrário,
embora nenhum caminho ativo o use com identificador `scheduler:*`.

### 5.3 Comparação das alternativas

| Alternativa | Garantia | Compatibilidade/impacto histórico | Concorrência | Migração e rollback | Homologação |
|---|---|---|---|---|---|
| A. Apenas `GET_LOCK` | Forte apenas entre clientes cooperantes no mesmo servidor | Nenhuma alteração; preserva todos os históricos | Serializa nomes iguais; falha fechada atual | Sem migration; rollback inexistente | Testes multiprocesso, perda de conexão e restart já existem; revisar todos os produtores continuamente |
| B. UNIQUE direto `(data_referencia, triggered_by)` | Banco rejeita duplicidade para qualquer origem | Pode bloquear múltiplas execuções manuais com mesmo identificador/data; exige auditoria de todo histórico | Atômica no banco | ALTER/índice pode bloquear ou reconstruir; remoção do índice é simples, mas registros rejeitados não são recuperados | Laboratório, inventário de `triggered_by`, duplicidades históricas, carga e tempo de DDL |
| C. Tabela auxiliar de idempotência | Forte se a reserva auxiliar e a execução forem gravadas atomicamente e todos os produtores migrarem | Não altera linhas históricas; adiciona entidade e FK/código | Boa, mas introduz transação e recuperação entre duas tabelas | CREATE TABLE, código e rollback mais complexos; risco de reservas órfãs | Testes de crash em cada etapa, backfill/adoção, concorrência e recuperação |
| D. Coluna gerada anulável + UNIQUE | Banco protege apenas `scheduler:CWB|BIG`; manuais viram NULL e podem repetir | Mantém semântica manual e deriva dados existentes; falha se houver duplicidade scheduler | Backstop atômico, complementar ao `GET_LOCK` | ALTER adiciona coluna/índice; requer medir lock/rebuild e rollback do índice/coluna | Laboratório MySQL 5.7.32, dados reais saneados, plano de janela e rollback |

O MySQL 5.7/InnoDB suporta índices secundários UNIQUE em colunas geradas virtuais,
e UNIQUE permite múltiplos valores NULL: [Generated-column indexes](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/create-table-secondary-indexes.html) e
[CREATE INDEX](https://docs.oracle.com/cd/E17952_01/mysql-5.7-en/create-index.html).

### 5.4 Alternativa recomendada

**D, coluna gerada anulável + UNIQUE, mantendo também `GET_LOCK`.** Ela cria
defesa em profundidade sem impedir repetições manuais. O SQL abaixo é apenas
exemplo de desenho, **não executável e sujeito a revisão de DBA e aprovação
separada**:

```sql
-- EXEMPLO NÃO EXECUTÁVEL. NÃO APLICAR SEM MIGRATION REVISADA.
-- ALTER TABLE oc73_execucoes
--   ADD COLUMN scheduler_fluxo VARCHAR(3)
--     GENERATED ALWAYS AS (
--       CASE
--         WHEN triggered_by = 'scheduler:CWB' THEN 'CWB'
--         WHEN triggered_by = 'scheduler:BIG' THEN 'BIG'
--         ELSE NULL
--       END
--     ) VIRTUAL,
--   ADD UNIQUE KEY uq_oc73_scheduler_data_fluxo
--     (data_referencia, scheduler_fluxo);
```

Pré-requisitos: confirmar DDL/collation/tipos, repetir diagnóstico de duplicidade,
validar o plano no laboratório, medir locks, impedir jobs durante a migration,
ter backup e rollback aprovado. A coleta pontual com zero duplicidades reduz, mas
não elimina, o risco de criação do índice.

## 6. Referência OP101

- Formato: `OC73-` + 20 hexadecimais maiúsculos (`referencia.py:5-12`).
- Geração: `secrets.token_hex(10).upper()` durante o INSERT da tentativa
  (`repository.py:291-334`).
- Transporte: campo `f6` como `BOT OC73 | REF:<referência>`
  (`service.py:909-915`, `ocorrencias.py:798-839`).
- Limite imposto pelo código: 70 caracteres; exatamente um marcador `REF:` e
  referência válida (`ocorrencias.py:747-774`). A observação atual possui 40
  caracteres.
- Confirmação: código 73 e token exato no complemento `f6`, com limites de token
  (`referencia.py:15-40`). Referência truncada, minúscula, parcial ou diferente
  não confirma.
- **PENDENTE:** comprimento e transformação reais do `f6` pelo SSW não foram
  comprovados. A fixture `historico_oc73_sintetico.xml` é explicitamente sintética.

### Matriz de testes OP101

| Cenário | Resultado seguro esperado | Evidência offline | Validação real |
|---|---|---|---|
| Referência integral | confirmar somente após histórico 73 exato | fixture sintética + testes de referência/gate | **PENDENTE** |
| Referência truncada | `incerto`, sem segundo POST | teste de referência truncada | **PENDENTE** |
| Referência ausente | `incerto`, sem confirmação | testes de referência e reconciliação | **PENDENTE** |
| OC73 antiga sem referência | pré-checagem impede novo envio; reconciliação não associa à tentativa | testes OP101/reconciliação | **PENDENTE** |
| Timeout após envio | `incerto`; segundo processamento bloqueado | cliente HTTP falso | **PENDENTE** |
| Histórico indisponível | `incerto`; sem confirmação/reenvio | cliente HTTP falso + reconciliador | **PENDENTE** |
| Reserva duplicada | gate bloqueia antes do POST | mocks e testes de repository | não requer POST real |
| Queda após `envio_iniciado` | estado permanece bloqueado; só reconciliação consultiva | `SystemExit` simulado | **PENDENTE** |

## 7. Riscos

| Severidade | Risco | Mitigação/decisão |
|---|---|---|
| Crítica | Referência pode ser truncada ou transformada pelo SSW real | Um único teste supervisionado; confirmar histórico antes de qualquer produção |
| Alta | Ausência de UNIQUE fluxo/data | Manter `GET_LOCK`; preparar migration separada com coluna gerada UNIQUE |
| Alta | Método público de lançamento pode ser chamado por código futuro sem gate | Revisão de chamadas e futura encapsulação/capability interna |
| Alta | Credencial MySQL operacional pode escrever além das tabelas OC73 | Conta dedicada de menor privilégio para diagnóstico; grants mínimos para runtime |
| Alta | Falha de persistência depois do POST impede certeza imediata | Interromper, preservar bloqueio e reconciliar manualmente; nunca reenviar |
| Média | DRY_RUN agendado grava reserva/auditoria | Tratar DRY_RUN como “sem POST”, não como “sem escrita”; documentar janela |
| Média | OP455/OP101 consultivos usam HTTP POST e geram arquivos/fila | Homologar em fase offline com fakes; no real, registrar efeitos esperados |
| Média | Dados sensíveis em CSV/JSON/downloads | ACL, retenção curta, saneamento e canal restrito |

## 8. Problemas bloqueantes e recomendações

### Para solicitar homologação supervisionada

Não há bloqueio técnico offline. É necessário anexar este relatório, o plano da
Sprint 3.15 e os resultados da suíte ao pedido de autorização.

### Para executar homologação supervisionada

Bloqueado até existir autorização específica, CTRC aprovado, janela, operador
SSW, revisão das flags efetivas e plano de interrupção. A ausência do UNIQUE
fluxo/data deve ser aceita formalmente para esse teste único, mantendo scheduler
desligado.

### Para produção automática

Bloqueado por:

1. ausência de prova real da preservação integral da referência;
2. ausência de UNIQUE por fluxo/data ou decisão formal de aceitar a dependência
   exclusiva do lock cooperativo;
3. falta de evidência de homologação real do fluxo completo e de recuperação;
4. necessidade de revisar privilégios da credencial e retenção das evidências;
5. scheduler e gate ainda devem permanecer desabilitados.

## 9. Checklist resumido

- [x] Fluxo real reconstruído por código.
- [x] Pontos de escrita e efeitos externos inventariados.
- [x] Gate, estados, idempotência e reconciliação auditados.
- [x] Concorrência fluxo/data analisada.
- [x] Alternativas A–D comparadas.
- [x] Matriz OP101 preparada e exercitada offline.
- [ ] Autorização específica de homologação real.
- [ ] CTRC formalmente aprovado.
- [ ] Evidência anterior e posterior do OP101 definida e coletada.
- [ ] Persistência integral da referência comprovada no SSW real.
- [ ] Decisão sobre backstop UNIQUE aprovada e, se escolhida, homologada.

## 10. Go/No-Go

| Marco | Parecer | Justificativa |
|---|---|---|
| Simulação offline | **GO** | Controles e cenários críticos possuem testes locais; nenhuma rede real é necessária |
| Solicitar homologação supervisionada | **GO** | Riscos e procedimento estão documentados |
| Executar homologação supervisionada | **NO-GO nesta sprint** | Falta autorização posterior, CTRC, janela e revisão das flags efetivas |
| Produção automática | **NO-GO** | Referência real não comprovada e unicidade fluxo/data sem backstop de banco |
