# OC73 — Hardening adversarial pré-coleta Windows — Sprint 3.16.1

Escopo exclusivamente offline. Nenhum modo do script PowerShell foi executado;
nenhuma API administrativa, porta, serviço, processo, volume ou banco foi consultado.

## Achados, causa e correção

| Severidade | Achado/causa | Correção offline | Limitação residual |
|---|---|---|---|
| Alta | serviço parado emitia PID `0`, rejeitado pelo analisador | serviço parado/indisponível usa `null`; processo/listener exigem inteiro positivo não booleano | estado real não observado |
| Alta | destino era validado após iniciar coleta | modo, autorização, saída e destino agora precedem `Invoke-ReadonlyCollection` | referência continua documental |
| Alta | teste de existência seguido de `WriteAllText` permitia colisão | revalidação e `FileMode.CreateNew`/`FileShare.None`, fechamento em `finally` | administrador local hostil ainda pode criar corridas fora do modelo |
| Alta | todos os processos MySQL/MariaDB podiam receber inspeção aprofundada | listener 3308 primeiro; somente seus PIDs recebem hash/assinatura/linha sanitizada | serviços registrados ainda são enumerados minimamente para conflitos |
| Alta | campos livres e regex eram defesa principal | schema fechado, categorias, tipos, limites, caminhos normalizados e erros codificados | futuro schema exige revisão coordenada |
| Média | volumes duplicados sempre pareciam contraditórios | deduplicação por ID normalizado; conflito real bloqueia; dedicação ausente é indeterminada | dedicação requer evidência externa |
| Alta | substring `mysql` podia sugerir Community | categorias conservadoras e prova administrativa separada do FileVersionInfo | metadado do arquivo não autentica origem |
| Alta | `NÃO OBSERVADO` podia parecer ausência de replicação | campo administrativo independente obrigatório para candidatura | ausência real permanece pendente |
| Alta | PID podia ser reutilizado entre consultas | marcador da instância e segunda observação de PID/executável/criação | não elimina mudança após a coleta |

## Contrato de PID

- processo/listener `OBSERVADO`: PID de tipo inteiro exato e maior que zero;
- `bool`, zero, negativo ou ausente são inválidos nesses objetos;
- serviço parado ou sem PID: `pid=null`;
- serviço `RUNNING` sem PID é aceito como evidência incompleta e não correlaciona;
- serviço `STOPPED` com PID positivo é contraditório;
- PID nulo nunca pode satisfazer a correlação do listener.

## Política de sanitização

O JSON aceita apenas campos conhecidos. Limites: relatório textual até 256 KiB,
serviços 64, processos 8, listeners 16, volumes 16, shares 64, erros 32 e flags 16.
Erros usam códigos fixos; flags usam categorias. Caminhos locais têm limite,
normalização lexical e rejeição de UNC/device path/controle/`..`. Linhas integrais,
ambiente, DSN, token, senha, `.env`, stack trace e exceção bruta não são emitidos.

Chaves duplicadas são detectadas durante o parse JSON. A regex de segredo é
defesa adicional; a barreira primária é a ausência de campos de texto livre.

## Política de destino e gravação

Antes da coleta: validar modo, combinações de parâmetros, referência documental,
política de saída e destino. Para gravação, exigir caminho absoluto local `.json`,
diretório FileSystem existente, ancestrais sem reparse point e arquivo ausente.
Revalidar o mesmo pai imediatamente antes de abrir. Usar criação exclusiva, sem
compartilhamento, e `finally`. Colisão, troca ou erro resultam em falha conservadora.

Não se afirma eliminação absoluta de TOCTOU contra administrador local capaz de
trocar mounts/objetos do kernel. Esse risco exige diretório administrativo dedicado,
ACL previamente aprovada e revisão operacional futura — sem alterá-las no script.
Falha depois de `CreateNew` pode deixar arquivo parcial fechado; ele não deve ser
interpretado como relatório nem removido/reutilizado automaticamente.

## Política de produto

FileVersionInfo gera uma categoria de metadado: Community reconhecido por nome
exato allowlisted, edição MySQL desconhecida, MariaDB, outro ou desconhecido. A
candidatura exige também `administrative_product=MYSQL_COMMUNITY_5_7_32`, obtido
fora desse metadado. Versão ausente, edição desconhecida ou prova administrativa
ausente mantêm `INDETERMINADO`; MariaDB/produto divergente é `NÃO CONFORME`.

## Política de endereços e replicação

Endereços são parseados e canonizados. O contrato aceita somente o listener
literal IPv4 `127.0.0.1:3308`. `::1`, wildcards, externos ou múltiplos PIDs não
são promovidos a equivalentes. Duplicatas idênticas são deduplicadas.

Indicadores locais de replicação bloqueiam. Ausência de indicadores e status
`NÃO OBSERVADO` não comprovam ausência: candidatura exige confirmação
administrativa independente, temporalmente válida.

## Testes e evidência

Foram adicionados testes sintéticos/estáticos para PIDs, ordem pré-coleta,
CreateNew, reparse/UNC/relativo, colisão/troca, escopo por listener, tipos/limites,
JSON duplicado, strings maliciosas, volumes, produto, IPv4/IPv6, replicação,
falhas categóricas, processo trocado, hash e assinatura. O parser AST do
PowerShell é usado sem executar o script.

Mocks, AST e inspeção estática não comprovam comportamento real do Windows,
filesystem, CIM, cmdlets, assinatura, volume ou produto.

## GO/NO-GO

GO apenas para revisão do pacote e futura solicitação de autorização específica.
Inventário real, gravação, conexão MySQL, DDL/DML, SSW e produção permanecem
NO-GO. A autorização de inventário continua **APROVAÇÃO NÃO CONCEDIDA**.
