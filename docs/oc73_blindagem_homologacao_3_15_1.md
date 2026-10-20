# OC73 — blindagem da homologação supervisionada 3.15.1

Data: 16/10/2026

Escopo: protótipo e testes exclusivamente offline. O mecanismo descrito neste
documento **não está integrado ao runtime operacional e não autoriza lançamento
real**.

## 1. Diagnóstico do ponto irreversível atual

Não existe pausa humana implementada no código operacional. A pausa descrita no
plano da Sprint 3.15 é apenas procedimento documental.

O fluxo real é:

```text
reservar_ctrc
  ├─ gera OC73-<20 hex> com secrets.token_hex(10).upper()
  ├─ INSERT oc73_tentativas(status=reservado, referencia=...)
  └─ COMMIT ao sair da transação
       │
       ▼
PortaoOC73 valida reserva nova e referência
       │
       ▼
iniciar_envio
  ├─ UPDATE condicional reservado → envio_iniciado
  └─ COMMIT ao sair da transação
       │
       ▼
callback enviar(referencia)
       │
       ▼
OP101Ocorrencias.lancar_ocorrencia_73
  ├─ GET consultivo do código 73
  └─ POST /bin/ssw0122, act=II3, f3=73  ← ponto irreversível externo
```

Evidências: `repository.py:271-384,387-417`, `gate.py:43-79`,
`service.py:897-925` e `operations/op101/ocorrencias.py:726-839`.

Falhas relevantes entre os pontos:

- após reserva e antes de `envio_iniciado`: não houve POST; reserva continua
  bloqueando outra tentativa;
- após `envio_iniciado` e antes de entrar no transporte: efeito externo não
  ocorreu, mas o estado permanece conservador e não pode ser reenviado;
- durante o transporte: não é possível distinguir com certeza se o SSW recebeu;
- após transporte aceitar e antes da resposta: timeout deixa efeito desconhecido;
- após resposta e antes do histórico: HTTP não confirma a ocorrência;
- após histórico exato e durante persistência: SSW pode estar confirmado enquanto
  o banco permanece incerto; exige reconciliação consultiva, nunca reenvio.

## 2. Arquitetura do protótipo offline

Implementação: `prototypes/oc73_homologacao_supervisionada.py`.

```text
dois responsáveis fornecem aceites distintos
                 │
                 ▼
ConteudoAutorizacao canônico
  CTRC + seq_ctrc + família + local
  referência persistida
  revisão de código
  emissão + expiração ≤ 15 min
  identidade técnica + operacional
                 │
                 ▼
HMAC-SHA256 com chave fornecida pelo chamador
                 │
                 ▼
token assinado
                 │
                 ▼
validação offline fail-closed
  assinatura + campos exatos + prazo + status=reservado
                 │
                 ▼
marcador local O_CREAT|O_EXCL
  <authorization_id>.usada
                 │
                 ▼
autorização queimada antes de qualquer POST
```

O protótipo não lê `.env`, não gera ou persiste segredo, não abre rede, não acessa
MySQL e não é importado por `service.py`, `gate.py`, scheduler, rotas ou aplicação.

## 3. Fronteiras de confiança

| Fronteira | O que o protótipo demonstra | O que não demonstra |
|---|---|---|
| Emissor do token | alteração posterior invalida HMAC | identidade humana real; quem possui a chave pode emitir ambos os aceites |
| Conteúdo aprovado | vínculo canônico a CTRC, referência e revisão | que os dados vieram efetivamente do MySQL/SSW |
| Prazo | validade positiva, explícita e limitada a 15 minutos | relógio confiável entre hosts |
| Uso único local | `O_EXCL` permite um vencedor entre processos no mesmo filesystem local | consumo único entre hosts, containers ou filesystem de rede |
| Estado | somente `reservado` é aceito; `incerto`, `envio_iniciado` e `confirmado` bloqueiam | atomicidade conjunta com a transição MySQL real |
| Instrumentação | contagem exata no transporte falso | pacote observado na rede ou recebido pelo SSW real |

## 4. Modelo de autorização

Campos assinados:

- ID aleatório de autorização com 32 caracteres hexadecimais;
- série e número do CTRC;
- `seq_ctrc`, família e local;
- referência `OC73-[0-9A-F]{20}`;
- revisão Git aprovada, entre 7 e 64 caracteres hexadecimais;
- instante de emissão e expiração UTC;
- identidades distintas do responsável técnico e operacional.

A serialização usa JSON canônico, chaves ordenadas e HMAC-SHA256. Campos ausentes,
extras, assinatura divergente, validade maior que 15 minutos, referência inválida
ou aceites iguais são recusados.

As normalizações são limitadas e determinísticas: espaços externos são removidos,
identificadores SSW são convertidos para maiúsculas e revisão para minúsculas.
Depois disso, a comparação usa `hmac.compare_digest`.

### Limitação dos aceites

Os aceites do protótipo são identificadores assinados, não autenticação humana.
Uma integração real precisa receber afirmações de identidade de um mecanismo
confiável, registrar horário/ator e impedir que o mesmo principal exerça os dois
papéis. A chave HMAC também precisará de armazenamento e rotação seguros.

## 5. Consumo único, concorrência e revogação

O armazenamento local cria `<id>.usada` com criação exclusiva. Dois processos
testados em paralelo obtêm exatamente um consumo e uma rejeição por reutilização.
O marcador persiste após criar uma nova instância do armazenamento, simulando
reinício do processo.

A autorização é queimada antes do POST. Se ocorrer falha ao escrever, no hook
pós-reivindicação ou depois do consumo e antes do transporte, o marcador não é
removido. Isso sacrifica disponibilidade para preservar fail-closed.

Revogação cria `<id>.revogada`. O consumo verifica revogação antes e depois de
reivindicar. Em corrida, a operação que efetivamente conclui primeiro define o
resultado; se a revogação aparecer antes da verificação final, o consumo falha e
o token permanece queimado.

### Limitação estrutural

O consumo local não pode ser atômico com o UPDATE MySQL
`reservado → envio_iniciado`. Portanto ele não é adequado, sozinho, para runtime
com mais de um host. Para integração futura, o consumo deve usar persistência
transacional compartilhada e UNIQUE, preferencialmente na mesma transação que
autoriza `iniciar_envio`. Essa alteração exige desenho de schema, migration,
laboratório e autorização separados.

## 6. Contador offline de POSTs

O protótipo separa cinco marcos:

1. `callback_solicitado`: o gate invocou o callback;
2. `tentativa_transporte`: o método `post` falso recebeu uma operação classificada
   como lançamento;
3. `observada_transporte_falso`: o transporte falso aceitou a requisição antes de
   responder ou simular timeout;
4. `resposta_recebida`: o callback retornou ao chamador;
5. `confirmada_por_historico`: uma OC73 com referência exata foi localizada.

A classificação de lançamento não depende somente da URL. Exige conjuntamente:

- endpoint `/bin/ssw0122`;
- `act=II3`;
- `f3=73`;
- `seq_ctrc` presente.

Eventos guardam somente o nome da operação e fingerprints SHA-256 truncados do
`seq_ctrc` e da referência. Cookies, tokens, senhas e payload não são registrados.

### Limite da evidência

`observada_transporte_falso=1` significa apenas que a implementação falsa aceitou
a chamada. Não prova socket aberto, pacote enviado, chegada ao balanceador ou
recebimento pelo SSW. Evidência de rede real exigiria instrumentação e autorização
operacionais específicas e não faz parte desta sprint.

## 7. Inventário HTTP OP455/OP101

| Categoria | Operação observada no código | Classificação |
|---|---|---|
| Login | POST `/bin/ssw0422`, `act=L` | autenticação; nunca contador OC73 |
| Menu/navegação | POST `/bin/menu01`; OP101 também usa GET `/bin/ssw0082` | navegação; nunca contador OC73 |
| Abrir opção | POST `/bin/ssw0053`, `sequencia=101/455` | navegação; nunca contador OC73 |
| Solicitar OP455 | POST `/bin/ssw0230`, `act=E1` | relatório; contador consultivo separado |
| Baixar relatório | GET `/bin/ssw0424` ou fluxo OP156 | download; nunca contador OC73 |
| Pesquisar CTRC | GET `/bin/ssw0385` | consulta; nunca contador OC73 |
| Abrir CTRC | POST `/bin/ssw0053`, `act=P1` | consulta; contador consultivo separado |
| Histórico | POST `/bin/ssw0053`, `act=O` | consulta; contador consultivo separado |
| Consultar código 73 | GET `/bin/ssw0385`, `tipo=ocorrencia` | consulta; nunca contador OC73 |
| Lançar OC73 | POST `/bin/ssw0122`, `act=II3`, `f3=73` | único lançamento contabilizado |

## 8. Cenários comprovados offline

| Cenário | Resultado |
|---|---|
| Ausência de autorização | bloqueado |
| Autorização expirada | bloqueado |
| CTRC/seq/família/local diferente | bloqueado |
| Referência diferente | bloqueado |
| Revisão diferente | bloqueado |
| Apenas um aceite | emissão bloqueada |
| Mesma identidade nos dois aceites | emissão bloqueada |
| Conteúdo alterado depois da assinatura | assinatura inválida |
| Reutilização/reinício | bloqueado pelo marcador persistente |
| Dois processos concorrentes | exatamente um consumo |
| Falha durante ou depois do consumo | token permanece queimado |
| Revogação | consumo bloqueado |
| Estado incerto/iniciado/confirmado | consumo bloqueado |
| Timeout após aceite falso | uma requisição observada, nenhuma resposta, sem retry |
| Resposta positiva ambígua | resposta contada, nenhuma confirmação |
| Referência truncada no histórico | nenhuma confirmação |
| Interrupção após `envio_iniciado` | estado bloqueado, nenhum segundo callback |
| POSTs consultivos | não incrementam lançamento |
| Histórico exato | confirmação contada separadamente |

## 9. Tratamento de falhas

- qualquer divergência ocorre antes do marcador e antes de POST;
- depois do consumo, qualquer falha mantém a autorização inutilizável;
- timeout após aceite falso produz `incerto`, não reenvio;
- HTTP ambíguo pode produzir resposta recebida, mas não confirmação;
- interrupção `BaseException` depois de `envio_iniciado` preserva o bloqueio;
- eventos saneados não contêm os identificadores brutos.

## 10. Testes executados

- protótipo direcionado: **31 passed**;
- regressão OC73/OP101: **227 passed, 16 skipped, 47 deselected**;
- suíte offline completa: **274 passed, 16 skipped**;
- compilação do protótipo e dos testes: concluída sem erros;
- `git diff --check`: sem erro de whitespace;
- warnings: duas depreciações FastAPI preexistentes em `web/main.py`.

As integrações ignoradas exigem configuração explícita e não foram executadas.
Nenhum teste acessou MySQL operacional, SSW ou rede externa.

## 11. Critérios para integração operacional futura

Antes de integrar qualquer parte ao runtime:

1. substituir armazenamento local por consumo compartilhado e transacional;
2. acoplar atomicamente consumo e transição para `envio_iniciado`;
3. definir schema/UNIQUE e homologar migration no laboratório MySQL 5.7.32;
4. autenticar os dois responsáveis por mecanismo confiável e papéis distintos;
5. definir geração, custódia, rotação e revogação da chave de assinatura;
6. obter revisão Git de fonte confiável e comparar com o processo efetivo;
7. definir relógio confiável e tolerância de clock;
8. criar trilha append-only saneada;
9. preservar `OC73_ENVIO_HABILITADO`, limite, CTRC de teste e `PortaoOC73`;
10. repetir testes multiprocesso, crash, timeout e rollback em laboratório;
11. revisar ameaça de acesso local ao diretório/DB de autorizações;
12. obter autorização específica para a integração e para o teste real.

## 12. Riscos residuais e parecer

- **Alta:** consumo local não garante uso único entre hosts e não é transacional
  com MySQL.
- **Alta:** aceites são identificadores assinados, não prova autônoma de identidade.
- **Alta:** evidência do transporte falso não é evidência de rede/SSW real.
- **Média:** falha depois do consumo exige nova decisão humana; o protótipo não
  reabre automaticamente a autorização.
- **Média:** segredo HMAC é responsabilidade do chamador e não possui lifecycle
  neste protótipo.

Pareceres separados:

- **A. Protótipo offline: GO.** Demonstra vínculo, expiração, alteração detectável,
  uso único local, fail-closed e contagem estratificada.
- **B. Integração futura ao runtime: GO somente para planejamento.** A solução
  local não deve ser conectada ao caminho operacional.
- **C. Homologação real: NO-GO.** Falta autorização específica e controles
  integrados com persistência compartilhada.
- **D. Produção automática: NO-GO.** Continua dependente de homologação real,
  prova da referência e resolução dos bloqueios operacionais.
