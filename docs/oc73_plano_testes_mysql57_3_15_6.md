# OC73 — Plano de testes transacionais MySQL 5.7 — Sprint 3.15.6

Plano exclusivamente **offline**: não executa SQL e não autoriza testes. A futura
execução usará somente dados sintéticos no laboratório aprovado, nunca CTRC
operacional, SSW real, IdP real ou callback de rede. Cada barreira deve ser
coordenada pelo harness revisado; sleeps não constituem sincronização confiável.

## Regras comuns

- pré-condição global: inventário, DDL e autorização de Testes DML aprovados;
- conexões independentes, `autocommit=False`, isolamento registrado e timeouts;
- ordem normal de locks: tentativa, autorização e registros dependentes;
- consultas de evidência fixas e saneadas, sem dados pessoais ou segredos;
- erro inesperado, identidade divergente ou commit ambíguo interrompe a janela;
- recuperação nunca reenvia callback nem transforma estado incerto em reenviável;
- limpeza requer autorização separada.

## T01 — Consumo único

- **Pré-condições:** autorização pendente, válida e com duas aprovações sintéticas.
- **Dados sintéticos:** uma tentativa, uma autorização e três identidades fictícias.
- **Conexões:** duas conexões transacionais independentes.
- **Ordem/locks e barreiras:** ambas iniciam; C1 bloqueia autorização antes de C2.
- **Transações:** C1 consome e confirma; C2 reavalia a condição após o desbloqueio.
- **Resultado esperado:** exatamente um consumo; C2 não altera estado.
- **Evidência SQL:** rowcounts e estados agregados da autorização/tentativa/consumo.
- **Aprovação:** um commit vencedor, um resultado conservador e zero callback real.
- **Interrupção:** dois consumos, espera sem limite ou transição parcial.
- **Recuperação e limpeza:** preservar evidência; não repetir; Limpeza separada.

## T02 — Rollback por falha de auditoria

- **Pré-condições:** contexto válido e mecanismo aprovado de falha antes do commit.
- **Dados sintéticos:** autorização ainda não consumida e evento de auditoria inválido controlado.
- **Conexões:** uma conexão transacional.
- **Ordem/locks e barreiras:** lock da tentativa e autorização antes das mudanças.
- **Transações:** updates condicionais seguidos de falha de auditoria e rollback.
- **Resultado esperado:** nenhuma transição parcial ou consumo persistido.
- **Evidência SQL:** snapshot agregado antes/depois e ausência do evento/consumo.
- **Aprovação:** snapshots equivalentes e conexão encerrada.
- **Interrupção:** qualquer coluna alterada após rollback.
- **Recuperação e limpeza:** manter bloqueado para análise; Limpeza separada.

## T03 — Concorrência por tentativa

- **Pré-condições:** tentativa reservada e duas operações concorrentes aprovadas.
- **Dados sintéticos:** uma tentativa/referência e duas autorizações candidatas.
- **Conexões:** duas conexões com transações explícitas.
- **Ordem/locks e barreiras:** C1 obtém lock da tentativa; C2 aguarda na mesma linha.
- **Transações:** ambas tentam criar/alterar o mesmo contexto.
- **Resultado esperado:** invariantes e UNIQUE preservados; somente uma vencedora.
- **Evidência SQL:** contagens por tentativa/referência e códigos de erro saneados.
- **Aprovação:** uma autorização persistida e nenhum contexto órfão.
- **Interrupção:** duplicidade, deadlock inesperado ou lock fora da tentativa.
- **Recuperação e limpeza:** rollback do perdedor; sem retry automático.

## T04 — Referência divergente

- **Pré-condições:** tentativa persistida com referência sintética conhecida.
- **Dados sintéticos:** autorização candidata com outra referência válida em formato.
- **Conexões:** uma conexão transacional.
- **Ordem/locks e barreiras:** tentativa bloqueada antes da inserção candidata.
- **Transações:** tentativa de inserir autorização com contexto divergente.
- **Resultado esperado:** FK composta rejeita a operação.
- **Evidência SQL:** erro de integridade saneado e contagem inalterada.
- **Aprovação:** zero autorização divergente e rollback completo.
- **Interrupção:** inserção aceita ou erro expondo dados/credenciais.
- **Recuperação e limpeza:** rollback e preservação do diagnóstico.

## T05 — Revisão divergente

- **Pré-condições:** tentativa com revisão sintética fixada.
- **Dados sintéticos:** autorização candidata com revisão distinta.
- **Conexões:** uma conexão transacional.
- **Ordem/locks e barreiras:** lock da tentativa antes da validação/inserção.
- **Transações:** inserção do contexto com revisão diferente.
- **Resultado esperado:** FK composta rejeita a autorização.
- **Evidência SQL:** erro saneado, rowcount zero e contexto original intacto.
- **Aprovação:** nenhuma autorização divergente persistida.
- **Interrupção:** aceitação, atualização da tentativa ou fallback de revisão.
- **Recuperação e limpeza:** rollback; não corrigir valores automaticamente.

## T06 — Autorização expirada

- **Pré-condições:** relógio UTC registrado e autorização já expirada.
- **Dados sintéticos:** timestamps inequivocamente anteriores ao instante do teste.
- **Conexões:** uma conexão transacional.
- **Ordem/locks e barreiras:** lock e releitura da autorização no consumo.
- **Transações:** update condicional exige status pendente e `expira_em` futuro.
- **Resultado esperado:** nenhum consumo e nenhuma transição de tentativa.
- **Evidência SQL:** rowcount zero e estados agregados inalterados.
- **Aprovação:** falha fechada sem callback.
- **Interrupção:** tolerância implícita, consumo ou ajuste automático de relógio.
- **Recuperação e limpeza:** registrar e encerrar; nova autorização exigiria novo fluxo.

## T07 — Autorização revogada

- **Pré-condições:** revogação persistida antes do início do teste.
- **Dados sintéticos:** autorização com status `revogada` e timestamp coerente.
- **Conexões:** uma conexão transacional.
- **Ordem/locks e barreiras:** lock e releitura do status atual.
- **Transações:** tentativa de consumo por update condicional.
- **Resultado esperado:** rowcount zero; nenhum consumo.
- **Evidência SQL:** status/timestamps e contagem de consumos.
- **Aprovação:** estado revogado preservado e zero callback.
- **Interrupção:** reabertura, consumo ou remoção da revogação.
- **Recuperação e limpeza:** nenhuma correção automática; preservar evidência.

## T08 — Executor inválido

- **Pré-condições:** duas aprovações válidas e identidades com namespaces completos.
- **Dados sintéticos:** executor igual ao aprovador técnico ou operacional, em subcasos.
- **Conexões:** uma conexão por subcaso.
- **Ordem/locks e barreiras:** lock da autorização; aprovações relidas na transação.
- **Transações:** validação server-side precede consumo e auditoria.
- **Resultado esperado:** ambos os subcasos são rejeitados.
- **Evidência SQL:** zero consumo/transição; identidades exibidas apenas por hashes.
- **Aprovação:** executor-aprovador nunca persiste como consumidor.
- **Interrupção:** comparação por username/email ou consumo parcial.
- **Recuperação e limpeza:** rollback e análise da identidade; sem substituição automática.

## T09 — Auditoria inconsistente

- **Pré-condições:** autorização A pertence à tentativa A; tentativa B existe.
- **Dados sintéticos:** evento tenta combinar autorização A e tentativa B.
- **Conexões:** uma conexão transacional.
- **Ordem/locks e barreiras:** locks dos contextos em ordem crescente de tentativa.
- **Transações:** inserção direta controlada da associação divergente.
- **Resultado esperado:** FK composta impede a auditoria.
- **Evidência SQL:** erro de FK saneado e ausência do evento.
- **Aprovação:** nenhuma linha cruzada e transação revertida.
- **Interrupção:** evento aceito ou lock em ordem não planejada.
- **Recuperação e limpeza:** rollback; não desabilitar FK.

## T10 — Consumo inconsistente

- **Pré-condições:** autorização A pertence à tentativa A; tentativa B existe.
- **Dados sintéticos:** consumo combina autorização A e tentativa B.
- **Conexões:** uma conexão transacional.
- **Ordem/locks e barreiras:** locks em ordem crescente antes da inserção.
- **Transações:** tentativa controlada de inserir o par divergente.
- **Resultado esperado:** FK composta impede o consumo.
- **Evidência SQL:** erro de FK saneado e contagem de consumos inalterada.
- **Aprovação:** nenhuma linha cruzada ou transição parcial.
- **Interrupção:** consumo aceito ou constraints desabilitadas.
- **Recuperação e limpeza:** rollback; jamais reparar a associação manualmente.

## T11 — Deadlock controlado

- **Pré-condições:** duas tentativas sintéticas e timeout/deadlock observável.
- **Dados sintéticos:** C1 e C2 operam os mesmos pares em ordem deliberadamente inversa.
- **Conexões:** duas conexões transacionais independentes.
- **Ordem/locks e barreiras:** barreiras confirmam o primeiro lock antes do segundo.
- **Transações:** servidor escolhe uma vítima; harness não executa callback.
- **Resultado esperado:** perdedor faz rollback; vencedor só confirma se inequivocamente conhecido.
- **Evidência SQL:** código de deadlock saneado, estados e rowcounts finais.
- **Aprovação:** invariantes preservados e nenhum retry/callback automático.
- **Interrupção:** loop de retry, estado parcial ou bloqueio não limitado.
- **Recuperação e limpeza:** encerrar ambas; revisão humana antes de qualquer repetição.

## T12 — Commit ambíguo

- **Pré-condições:** falha controlada entre envio do commit e confirmação ao cliente.
- **Dados sintéticos:** autorização consumível sem integração externa.
- **Conexões:** uma conexão de teste e outra exclusivamente consultiva para evidência posterior.
- **Ordem/locks e barreiras:** falha injetada somente após a decisão de commit.
- **Transações:** cliente perde confirmação e classifica o resultado indeterminado.
- **Resultado esperado:** nenhum retry, callback ou reabertura automática.
- **Evidência SQL:** estado observado posteriormente, sem usá-lo para reenviar.
- **Aprovação:** indeterminação preservada e intervenção humana exigida.
- **Interrupção:** repetição automática ou conversão para sucesso/falha por suposição.
- **Recuperação e limpeza:** registrar; não alterar estado para permitir novo consumo.

## T13 — Crash após commit

- **Pré-condições:** ponto controlado depois do commit e antes do callback falso.
- **Dados sintéticos:** autorização válida e callback local contador, sem rede.
- **Conexões:** uma conexão transacional; nova conexão apenas após reinício simulado.
- **Ordem/locks e barreiras:** persistência confirmada antes da interrupção do processo.
- **Transações:** commit ocorre; processo termina antes de liberar callback.
- **Resultado esperado:** reinício observa consumo e não executa segundo envio.
- **Evidência SQL:** autorização/consumo/tentativa e contador local de callback zero.
- **Aprovação:** nenhuma reexecução automática após reinício.
- **Interrupção:** callback duplicado ou autorização reaberta.
- **Recuperação e limpeza:** reconciliação apenas consultiva e decisão humana.

## T14 — Referência duplicada

- **Pré-condições:** primeira tentativa com referência sintética já confirmada no banco.
- **Dados sintéticos:** segunda tentativa/CTRC sintético tenta reutilizar a referência.
- **Conexões:** uma ou duas conexões conforme subcaso sequencial/concorrente.
- **Ordem/locks e barreiras:** concorrente usa barreira antes das inserções.
- **Transações:** ambas tentam persistir a mesma referência binária.
- **Resultado esperado:** UNIQUE permite no máximo uma linha.
- **Evidência SQL:** agrupamento por referência e erro de duplicidade saneado.
- **Aprovação:** contagem igual a um e nenhum fallback para nova referência implícita.
- **Interrupção:** duas linhas, comparação não binária ou retry automático.
- **Recuperação e limpeza:** rollback do perdedor; preservar a referência vencedora.

## Saída esperada da futura execução

Relatório por cenário com revisão, autorização, timestamps, conexões, barreiras,
resultado completo/limitado/inconclusivo, SQL fixo utilizado, rowcounts, erros
saneados e decisão GO/NO-GO. Falha de consulta torna o resultado inconclusivo;
ausência de evidência nunca é interpretada como ausência de violação.
