# OC73 — plano de homologação supervisionada 3.15

Este documento prepara uma atividade futura. **Não autoriza lançamento, conexão
operacional, alteração de flags, migration ou deploy nesta sprint.** A execução
real exige autorização posterior, específica e registrada.

> Atualização 3.15.1: existe um protótipo offline de autorização assinada e
> contador de transporte falso, documentado em
> `docs/oc73_blindagem_homologacao_3_15_1.md`. Ele não está integrado ao runtime,
> não constitui a pausa operacional exigida abaixo e não torna a Fase B apta.

## 1. Objetivo e limites

Validar, com um único CTRC previamente aprovado e no máximo uma tentativa, se o
SSW persiste integralmente no histórico OP101 a referência
`REF:OC73-[0-9A-F]{20}` enviada no campo `f6`, e se os estados MySQL permanecem
coerentes diante do resultado observado.

O teste não autoriza scheduler, lote, reenvio, alteração de elegibilidade,
migration ou uso de outro CTRC. Resposta HTTP positiva é apenas evidência de
transporte e nunca confirma a ocorrência.

## 2. Papéis e autorizações

Registrar antes da janela:

| Papel | Responsabilidade | Identificação/aceite |
|---|---|---|
| Responsável técnico | autorizar exatamente uma tentativa | PENDENTE |
| Responsável operacional | aprovar CTRC e impacto da ocorrência 73 | PENDENTE |
| Operador SSW | acompanhar histórico antes/depois | PENDENTE |
| DBA/infra | aprovar janela e monitorar MySQL | PENDENTE |
| Executor | seguir checklist e interromper sem improviso | PENDENTE |
| Segundo observador | conferir referência, flags e evidências | PENDENTE |

A exceção de identidade em
`docs/oc73_excecao_identidade_mysql_2026-10-16.md` não substitui nenhuma dessas
autorizações.

## 3. Pré-condições obrigatórias

### Autorizações e escopo

- [ ] autorização escrita específica para um único lançamento;
- [ ] data, início e fim da janela aprovados;
- [ ] CTRC de teste formalmente escolhido, com série, número, unidade e motivo;
- [ ] confirmação operacional de que o CTRC pode receber ocorrência 73 sem
      impacto indevido;
- [ ] usuário SSW autorizado e acompanhado;
- [ ] proibição explícita de segundo POST, inclusive após timeout;
- [ ] responsáveis e canal de incidente definidos.

### Código e ambiente

- [ ] revisão do commit/hash exato a ser executado;
- [ ] working tree de homologação sem alterações não aprovadas;
- [ ] dependências e Python equivalentes aos testados;
- [ ] scheduler comprovadamente desabilitado;
- [ ] apenas a execução manual supervisionada poderá ser considerada;
- [ ] `OCORRENCIA_73_CTRC_TESTE` restrito ao CTRC autorizado;
- [ ] `OCORRENCIA_73_MAX_LANCAMENTOS=1` validado no processo efetivo;
- [ ] gate de envio revisado por dois operadores, sem registrar segredo;
- [ ] nenhuma rota web ou scheduler usado como atalho para o teste;
- [ ] credenciais carregadas pelo mecanismo operacional aprovado;
- [ ] nenhum segredo presente em terminal compartilhado, log ou evidência.

### Banco e identidade

- [ ] endpoint, porta, schema, versão e UUID esperados revisados;
- [ ] exceção de identidade formalmente aceita para a janela;
- [ ] coleta consultiva recente confirma contrato mínimo e ausência de
      duplicidades; se a coleta for antiga, exigir nova autorização para repeti-la;
- [ ] ausência de UNIQUE fluxo/data reconhecida e aceita para este teste único;
- [ ] nenhuma execução scheduler CWB/BIG em andamento;
- [ ] não existe tentativa anterior para o CTRC selecionado;
- [ ] estratégia de recuperação definida: não apagar nem retroceder estados;
- [ ] backup aplicável e política de retenção de evidências confirmados.

### Evidências planejadas

- [ ] modelo de registro com timestamps em America/Sao_Paulo;
- [ ] captura saneada do histórico OP101 antes do teste;
- [ ] IDs da execução e tentativa;
- [ ] referência persistida e contagem de caracteres da observação;
- [ ] contador independente de POSTs;
- [ ] resposta de transporte saneada;
- [ ] histórico OP101 posterior;
- [ ] estado final no banco e conclusão assinada.

## 4. Fase A — simulação offline

Esta fase não usa MySQL operacional nem SSW real.

1. Criar ambiente local limpo a partir da revisão aprovada.
2. Confirmar que scheduler e gate permanecem desabilitados no processo de teste.
3. Executar testes direcionados:

   ```text
   python -m pytest -q \
     tests/test_oc73_gate.py \
     tests/test_oc73_falhas_http.py \
     tests/test_oc73_reconciliacao.py \
     tests/test_oc73_referencia.py \
     tests/test_oc73_op101_validacao.py \
     tests/test_oc73_repository.py \
     tests/test_oc73_scheduler.py \
     tests/test_oc73_observabilidade.py
   ```

4. Executar `python -m pytest -q` e registrar aprovados, ignorados e warnings.
5. Executar `git diff --check`.
6. Com fakes/mocks, demonstrar os oito cenários da matriz:
   referência integral, truncada, ausente, OC73 antiga sem referência, timeout
   após envio, histórico indisponível, reserva duplicada e interrupção após
   `envio_iniciado`.
7. Confirmar por inspeção que nenhum cliente falso permite rede real.
8. Anexar resultados ao pedido de autorização da Fase B.

### Aprovação da Fase A

Exigir todos os testes aprovados, integrações reais ignoradas, nenhum efeito
externo e nenhum desvio de segurança. Qualquer falha é NO-GO.

## 5. Fase B — teste real supervisionado futuro

**Não executar nesta sprint.** O roteiro abaixo só entra em vigor depois de nova
autorização específica.

### 5.1 Preparar o ambiente

1. Congelar a revisão aprovada e registrar seu hash.
2. Confirmar processo, usuário do sistema, venv e fonte efetiva das variáveis.
3. Confirmar scheduler desligado no processo e no painel.
4. Confirmar que nenhuma segunda instância executará OC73.
5. Verificar flags por leitura saneada, sem imprimir credenciais.
6. Configurar limitação ao CTRC aprovado e a uma única tentativa.
7. Não iniciar ainda qualquer fluxo capaz de chamar `/bin/ssw0122`.

### 5.2 Registrar estado inicial

1. Conferir série/número, `seq_ctrc`, família e local do CTRC aprovado.
2. Consultar o histórico OP101 e registrar captura saneada.
3. Se já existir qualquer ocorrência 73, interromper e reprovar o candidato.
4. Consultar as tabelas OC73 pelo procedimento autorizado e confirmar ausência de
   tentativa anterior para o CTRC.
5. Confirmar que não há execução scheduler concorrente.

### 5.3 Preparar uma única tentativa

1. Criar a execução manual pelo fluxo normal do serviço, nunca por INSERT manual.
2. Entrar exclusivamente pelo `PortaoOC73`.
3. Confirmar a tentativa em `reservado` e a referência já persistida.
4. Validar a referência contra `OC73-[0-9A-F]{20}`.
5. Construir `BOT OC73 | REF:<referência>` e registrar:
   - exatamente um `REF:`;
   - referência idêntica à persistida;
   - comprimento total menor ou igual a 70.
6. Segundo observador deve conferir CTRC, referência e contador máximo de um POST.

O protótipo local da Sprint 3.15.1 não deve ser usado como substituto desse
controle. Uma autorização operacional futura precisará de consumo transacional
compartilhado e autenticação real dos dois responsáveis.

### 5.4 Autorizar o ponto irreversível

Realizar pausa formal imediatamente antes da transição para `envio_iniciado`.
Responsável técnico e operacional devem confirmar no registro:

- CTRC correto;
- histórico inicial correto;
- referência correta;
- scheduler desligado;
- uma única tentativa;
- plano de interrupção compreendido.

Sem dois aceites, não prosseguir.

### 5.5 Envio e verificação

1. Permitir a transição condicional para `envio_iniciado`.
2. Executar no máximo um POST `/bin/ssw0122` pelo callback do gate.
3. Registrar horário e resultado de transporte saneado.
4. Não concluir sucesso pelo HTTP.
5. Consultar o histórico OP101 uma vez pelo fluxo normal.
6. Comparar código 73 e referência completa, byte a byte no domínio ASCII
   esperado.
7. Persistir `confirmado` apenas se a correspondência for exata.
8. Em ausência, truncamento, transformação, timeout ou erro, manter `incerto` ou
   `envio_iniciado` conforme o ponto da falha e interromper.

### 5.6 Conferência final

Comparar em conjunto:

- histórico OP101 anterior e posterior;
- referência gerada, enviada e retornada;
- IDs e timestamps MySQL;
- estado final da tentativa;
- contador de POSTs;
- logs saneados e arquivos de auditoria.

Não “corrigir” manualmente a tentativa, não voltar a `reservado` e não repetir o
envio. Reconciliação posterior, se autorizada, será somente consultiva.

## 6. Critérios imediatos de interrupção

Interromper sem nova tentativa diante de:

- UUID, porta, versão ou schema divergente;
- revisão de código/venv diferente da aprovada;
- scheduler ativo ou segunda instância não controlada;
- CTRC, `seq_ctrc`, família ou local inesperado;
- qualquer OC73 anterior no CTRC;
- tentativa/reserva anterior encontrada;
- referência inválida, ausente, duplicada, minúscula ou truncada;
- observação acima de 70 caracteres ou com mais de um `REF:`;
- falha de aquisição/manutenção da trava quando aplicável;
- erro de persistência antes ou depois do envio;
- timeout, perda de conexão ou resposta ambígua;
- histórico indisponível ou divergente;
- tentativa de segundo POST;
- divergência entre MySQL, SSW e evidências;
- comportamento não previsto neste roteiro.

## 7. Tratamento de falhas e recuperação

| Falha | Ação permitida | Ação proibida |
|---|---|---|
| Antes de `envio_iniciado` | interromper e preservar logs | contornar reserva/gate |
| Após `envio_iniciado`, antes de resposta | considerar efeito desconhecido | repetir POST |
| HTTP positivo sem histórico | manter `incerto` | marcar confirmado pelo HTTP |
| Histórico indisponível | interromper; solicitar reconciliação consultiva futura | assumir ausência |
| Referência truncada/diferente | reprovar homologação e preservar tentativa bloqueada | editar referência ou reenviar |
| Falha ao persistir estado | escalar incidente, preservar banco/SSW | DML manual improvisado |
| Processo encerrado | não reiniciar automaticamente a tentativa | limpar reserva para reenvio |

Rollback não significa apagar a ocorrência real nem retroceder estados. Consiste
em manter scheduler/gate desabilitados, cessar novas tentativas, preservar
evidências e restaurar apenas configuração/processo conforme plano aprovado. Uma
ocorrência já aceita pelo SSW exige tratamento operacional próprio.

## 8. Evidências e privacidade

Guardar em diretório restrito:

- autorização, responsáveis, janela e hash do código;
- CTRC autorizado e justificativa;
- histórico antes/depois, com dados não necessários mascarados;
- referência completa, pois é o objeto do teste;
- IDs de execução/tentativa e estados;
- um registro de POST, sem cookie, senha ou token;
- logs saneados;
- decisão final e incidentes.

Não registrar credenciais, cookies, payload completo com dados pessoais ou dumps
de tabelas. Definir ACL, retenção e descarte antes da janela.

## 9. Go/No-Go por marco

### Pronto para simulação offline

**GO** quando a revisão estiver disponível localmente. Não requer autorização de
efeito externo.

### Pronto para solicitar homologação supervisionada

**GO** após Fase A aprovada e anexação deste plano e do relatório técnico.

### Pronto para executar homologação supervisionada

**NO-GO até** preencher todas as pré-condições, obter autorização específica,
aprovar CTRC/janela e realizar a pausa de dois operadores.

### Pronto para produção automática

**NO-GO.** Exige ao menos homologação real aprovada, prova da preservação integral
da referência, decisão sobre unicidade fluxo/data, revisão de privilégios e nova
autorização de ativação.

## 10. Registro de conclusão futura

Ao encerrar a Fase B, classificar:

- **APROVADA:** exatamente um POST, referência integral e idêntica no histórico,
  estado `confirmado` coerente e nenhuma divergência;
- **REPROVADA:** truncamento/transformação, segundo POST, confirmação sem histórico,
  estado incoerente ou necessidade de contornar proteção;
- **INCONCLUSIVA:** timeout, histórico indisponível, perda de conexão ou falha de
  persistência. Resultado inconclusivo nunca autoriza repetição automática.
