# OC73 — Auditoria adversarial pré-laboratório 3.15.5

Escopo exclusivamente offline; nenhum efeito operacional foi produzido.

## Achados e correções

| Severidade | Achado/causa | Correção | Evidência | Risco residual |
|---|---|---|---|---|
| Alta | Parser rejeitava por substring antes de entender o grant | gramática fechada, tokens completos, conjuntos exatos e duplicidade rejeitada | testes com identificadores `UPDATE/CREATE/DROP/FILE`, comentários, sufixos e tokens repetidos | sintaxe real de `SHOW GRANTS` ainda não observada |
| Alta | Consumo/auditoria podiam referenciar autorização A e tentativa B | `UNIQUE (id,tentativa_id)` e FKs compostas | inspeção estática do DDL | execução MySQL 5.7 pendente |
| Alta | Autorização podia copiar referência/revisão diferente da tentativa | chave candidata e FK composta `(tentativa_id,referencia,revisao_codigo)` | teste estrutural do DDL | regras temporais/status continuam na aplicação |
| Média | Evidência SQL podia ser confundida com isolamento físico | contrato administrativo independente e documentação explícita | testes de completude/validade | valores reais dependem de administrador autorizado |
| Média | Driver não possuía contrato de versão/origem/integridade | `ContratoDriver` exige inventário, origem, SHA-256 e capabilities exatas | driver falso; incompatibilidade bloqueia construção | versão/hash reais pendentes |
| Média | Falha entre updates só era implícita | injeção `entre_updates` comprova rollback do snapshot fake | teste de snapshot integral | atomicidade InnoDB real pendente |

## Integridade declarativa e transacional

O banco proposto impede: duas autorizações por tentativa, dois consumos por
autorização/tentativa, contexto divergente entre autorização e tentativa e pares
autorização/tentativa divergentes em consumo/auditoria. Unicidade de papel,
conta e pessoa por autorização também é declarativa.

MySQL 5.7 não consegue impor de forma adequada, sem triggers/`CHECK` confiável:
executor diferente dos aprovadores, consumo antes da expiração, bloqueio após
revogação e transições completas de estado. Essas regras permanecem sob
`SELECT ... FOR UPDATE`, updates condicionais e validação da aplicação. Nenhum
trigger foi introduzido.

## Modelo de ameaças

- parser confuso: rejeição por gramática/significado;
- grants extras/duplicados: igualdade exata por escopo;
- servidor mentindo sobre si: identidade SQL nunca substitui evidência externa;
- pacote/driver trocado: versão, origem, hash e capabilities obrigatórios;
- relações cruzadas incoerentes: FKs compostas;
- corrida/queda: autorização única, updates condicionais e nenhum retry/callback
  em commit ambíguo;
- SSW aceitou POST sem resposta: estado incerto, jamais reenvio automático.

## Go/No-Go

O pacote está pronto para **solicitar** autorização específica somente após
preenchimento e revisão do contrato administrativo. Execução, runtime, SSW e
produção permanecem NO-GO. Mocks não comprovam driver, InnoDB ou SSW real.
