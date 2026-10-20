# OC73 — Inventário administrativo Windows somente leitura — Sprint 3.16

## Objetivo e escopo

Preparar a futura verificação administrativa de uma possível instância MySQL
Community 5.7.32 em `127.0.0.1:3308`, sem SQL, socket, execução de binários MySQL
ou descoberta de rede. Esta sprint é offline: o script não foi executado.

Os valores de host, porta, schema e usuário são contrato planejado, não evidência
de existência ou isolamento.

## Modos do script

- sem modo: falha fechado e não coleta;
- `-Explain`: lista somente as fontes planejadas;
- `-PlanOnly`: produz plano estruturado com `collects_data=false`;
- `-Collect`: futura coleta; exige referência de autorização externa;
- `-WriteJson -OutputPath <literal>`: gravação separada, somente com `-Collect` e
  autorização que permita escrita. O diretório deve existir, ser local e não
  conter reparse point; não é criado.

`-Collect` permanece proibido nesta sprint. A referência não é token e não prova
aprovação: o operador deve conferir o documento autorizado fora do script.

## Fontes de evidência e comandos consultivos planejados

| Fonte/API nativa | Cmdlet/API planejado | Dados mínimos |
|---|---|---|
| Windows | `Get-CimInstance Win32_OperatingSystem` | caption e versão |
| Serviços | `Get-CimInstance Win32_Service` | nome, estado, PID e executável extraído |
| Processos | `Get-CimInstance Win32_Process` | PID, nome, executável e resumo sanitizado |
| Listeners | `Get-NetTCPConnection -State Listen` | endereço local, porta e PID |
| Binário | `Get-AuthenticodeSignature` | estado da assinatura |
| Binário | `Get-FileHash -Algorithm SHA256` | hash do executável identificado |
| Versão | `System.Diagnostics.FileVersionInfo` | produto e versão do arquivo |
| Volume | `Get-Volume` | identificador do volume do datadir |
| Compartilhamento | `Get-SmbShare` | caminhos compartilhados locais |

Não são planejados `mysql.exe`, `mysqld.exe`, SQL, TCP ativo, registro, firewall,
ACL, instalação de módulo, elevação, exportação do ambiente ou leitura de arquivos
de dados. O script não lê conteúdo de arquivos de configuração; apenas reconhece
flags administrativas permitidas na linha de comando.

O listener da porta 3308 é consultado antes dos processos. Apenas o PID associado
a esse listener recebe inspeção aprofundada, hash, assinatura, versão, datadir e
dupla observação de identidade. Outros serviços MySQL/MariaDB são representados
somente por nome, estado, PID opcional e executável extraído. Não se enumeram ou
hasheiam executáveis de processos irrelevantes.

## Correlação

O relatório associa serviço → PID → processo → executável → listener 3308 →
datadir declarado → volume. Divergências permanecem explícitas. Porta 3308 por si
só não torna uma instância isolada. Caminho XAMPP, volume compartilhado, MariaDB,
processo desconhecido ou indício de replicação bloqueiam a candidatura.

Ausência de flag de replicação permanece `INDETERMINADO`. `NÃO OBSERVADO` descreve
somente o alcance da fonte local e nunca comprova ausência. `CANDIDATO` exige
confirmação administrativa independente no JSON revisado.

## Contrato JSON endurecido

- processo e listener observados exigem PID inteiro positivo; booleanos são
  rejeitados;
- serviço parado ou sem PID usa `pid: null`; `0` nunca vira identidade válida;
- serviço `STOPPED` com PID positivo é contraditório;
- listas têm limites fechados e strings possuem tamanho máximo;
- caminhos são locais, absolutos, sem UNC, device path, controle ou `..`, e são
  normalizados lexicalmente sem consultar o filesystem;
- `errors` contém apenas códigos categóricos allowlisted, nunca texto de exceção;
- flags observadas são categorias (`port:3308`, `server-id:PRESENT` etc.);
- chaves JSON duplicadas, campos desconhecidos e relatório acima de 256 KiB são
  rejeitados;
- volumes idênticos são deduplicados; conflitos são `NÃO CONFORME`; dedicação
  administrativa existe somente na evidência externa verificada;
- identidade do produto separa metadado local do executável da comprovação
  administrativa externa. Substring `mysql` não prova edição Community;
- `stable_observation` e `instance_marker` detectam troca/reuso do PID entre as
  duas observações;
- o marcador definitivo é `PID|yyyy-MM-ddTHH:mm:ss.fffffffZ`, sempre em UTC e
  cultura invariável. Entradas `.NET DateTime`, `DateTimeOffset`, ISO round-trip
  e CIM/DMTF estrito são normalizadas para esse formato. Não há fallback para a
  hora atual; conversão impossível gera `PROCESS_OBSERVATION_INCOMPLETE`,
  marcador `null` e resultado inconclusivo;
- `PROCESS_CHANGE_CONFIRMED` é reservado à divergência efetivamente comparada
  entre duas observações completas. O código legado `PROCESS_CHANGED` não
  distingue troca de falha de coleta e, por compatibilidade, é inconclusivo.

## Política de sanitização e dados sensíveis

Não registrar senha, token, connection string, DSN, `.env`, variáveis completas,
linha de comando integral, conteúdo de configuração, dados de clientes, bancos ou
dados SSW. A linha de comando é tratada como secreta: somente flags allowlisted
são extraídas; caminhos de arquivos de defaults são substituídos por marcador.

Erros são mensagens fixas sem exceção bruta. Falha de acesso produz evidência
`INDETERMINADO`, nunca bypass. A segurança não depende somente de regex: schema,
tipos, categorias, limites e allowlists rejeitam conteúdo livre desnecessário.

## Estados de evidência

- `OBSERVADO`: a fonte consultada retornou aquele valor;
- `NÃO OBSERVADO`: busca aplicável concluída sem o indício;
- `INDETERMINADO`: acesso, correlação ou alcance insuficiente;
- `NÃO APLICÁVEL`: fonte não pertinente ao objeto.

O analisador classifica o conjunto como `CANDIDATO`, `NÃO CONFORME` ou
`INDETERMINADO`. `CANDIDATO` significa coerência mínima e sempre mantém
`isolamento_comprovado=false`; não é autorização nem prova de isolamento.

## Limitações

- metadados do executável podem estar ausentes ou não provar a origem instalada;
- assinatura válida não prova que o serviço usa configuração isolada;
- processo, datadir ou volume podem mudar após a coleta;
- `Get-SmbShare` não prova ausência de outros compartilhamentos/mounts;
- flags não provam ausência de replicação;
- evidência local não comprova grants, UUID, schema ou conta MySQL;
- coleta é temporal e expira; revisão posterior não retroage a validade.
- a checagem de reparse point e a revalidação reduzem redirecionamentos, mas não
  eliminam toda corrida de filesystem contra um administrador local hostil;
- `FileMode.CreateNew` impede sobrescrita/colisão do arquivo, não autentica o
  operador nem transforma a referência textual em autorização criptográfica.

## Política de destino

Todos os parâmetros e o destino são validados antes da entrada na coleta. O
destino deve ser absoluto, local, `.json`, com diretório pai preexistente no
provider FileSystem e sem reparse point em seus ancestrais. O arquivo não pode
existir. Imediatamente antes da gravação o destino é revalidado e comparado; a
abertura usa `FileMode.CreateNew`, `FileShare.None` e fechamento em `finally`.
Colisão ou mudança cancela a gravação, sem sobrescrever ou criar diretórios.
Se uma falha ocorrer depois da criação exclusiva, um arquivo parcial pode
permanecer fechado no destino. Ele não é evidência válida, não é sobrescrito e
sua remoção exige decisão separada; o erro permanece conservador.

## Critérios de interrupção e revisão humana

Interromper por autorização ausente/expirada, hash do script divergente, usuário ou
máquina diferente, necessidade de elevação, fonte não prevista, segredo aparente,
erro inesperado, listener externo, múltiplos PIDs, XAMPP, MariaDB, volume
compartilhado, replicação ou tentativa de acessar conteúdo sensível.

Revisão humana deve conferir autorização, hash, timestamps, erros, correlações e
todos os itens indeterminados. Não corrigir relatório manualmente; nova coleta
exige nova decisão dentro da janela.

## Evidências necessárias antes de futura autorização SQL

- relatório JSON sanitizado e válido, se sua gravação tiver sido autorizada;
- contrato administrativo assinado e temporalmente válido;
- processo/porta/executável/datadir/volume correlacionados;
- origem e SHA-256 do binário e do script;
- ausência comprovada administrativamente de vínculo operacional;
- revisão dos indícios de XAMPP, compartilhamento e replicação;
- autorização consultiva SQL separada. O inventário local não concede SQL.

## Fechamento do contrato na Sprint 3.16.2

O relatório bruto passou para `oc73-inventario-windows-v2`. Ele contém somente
observações locais e não possui os campos `administrative_product`, `dedicated`
ou `administrative_absence_confirmed`. A mera inclusão desses campos torna o
relatório inválido. Assim, o produtor não pode transformar sua própria coleta em
prova administrativa independente.

Evidência administrativa é uma entrada separada. O contrato final
`oc73-administrative-evidence-v2` identifica instância, responsável,
origem, PID, marcador da instância, caminho e hash do executável, data de
observação, validade, hash da revisão do procedimento e referência documental.
Também contém o SHA-256 dos bytes exatos do inventário bruto e vincula
explicitamente as afirmações sobre produto, volume e replicação. A versão `v1`
continua reconhecida somente para compatibilidade e sempre é inconclusiva.

Mesmo estruturalmente válida, essa evidência permanece inconclusiva se o
chamador não fornecer um verificador externo de procedência. Um booleano ou uma
string incluídos no JSON não são prova. O resultado `CANDIDATO` não significa
`isolamento_comprovado=true` e continua sujeito a decisão humana independente.

Para entrada `bytes`, o digest usa exatamente os bytes recebidos. Para `str`,
usa exatamente sua codificação UTF-8, sem reformatar o JSON. Um objeto Python
somente pode ter vínculo criptográfico se vier acompanhado dos bytes originais;
esses bytes são parseados e precisam representar exatamente o mesmo objeto.

A revisão autorizada do procedimento é uma entrada independente. Ela deve vir
do registro de mudança/autorização mantido pelo responsável humano, nunca do
inventário nem da própria evidência. Ausência mantém `INDETERMINADO`; divergência
produz `NÃO CONFORME` antes de qualquer avaliação de procedência.

Os caminhos aceitos pelo analisador usam somente unidades locais de `C:` a
`Z:`, formato absoluto e componentes normais. São recusados UNC, device paths,
extended-length paths, caminhos drive-relative, unidades `A:`/`B:`, `..`, ADS,
caracteres de controle, nomes reservados e componentes terminados em ponto ou
espaço.

## Matriz de compatibilidade produtor → consumidor

| Fonte PowerShell | Campo emitido | Falha/ausência | Tratamento Python |
|---|---|---|---|
| `Win32_OperatingSystem` | `os.caption`, `os.version` | `null`, status `INDETERMINADO`, `OS_QUERY_FAILED` | `INDETERMINADO` |
| `Win32_Service` | nome, estado, PID, executável opcional | lista vazia ou executável `null`, `SERVICE_QUERY_FAILED` | `INDETERMINADO` sem correlação |
| `Get-NetTCPConnection` | endereço, porta inteira, PID positivo | lista vazia, `LISTENER_QUERY_FAILED` | `INDETERMINADO` |
| `Win32_Process` | PID, nome, caminhos opcionais, marcador UTC | processo omitido/segunda consulta incompleta, `PROCESS_QUERY_FAILED` e/ou `PROCESS_OBSERVATION_INCOMPLETE` | falha: `INDETERMINADO`; `PROCESS_CHANGE_CONFIRMED`: `NÃO CONFORME` |
| `Get-FileHash` | SHA-256 minúsculo ou `null` | `HASH_FAILED` | `INDETERMINADO` |
| `Get-AuthenticodeSignature` | enum fechado | `UNKNOWN`, `SIGNATURE_FAILED` | `INDETERMINADO` |
| `Get-Volume` | ID normalizado ou nenhuma associação | `VOLUME_QUERY_FAILED` | `INDETERMINADO` |
| `Get-SmbShare` | caminho local ou `null` | status `INDETERMINADO`, `SHARE_QUERY_FAILED` | `INDETERMINADO` |

Limites de listas e textos são aplicados no produtor antes da serialização e
novamente no consumidor. Ausências não são substituídas por identidade, caminho
ou timestamp inventados.

### Matriz conservadora das duas observações do processo

| Cenário | Código categórico | Classificação |
|---|---|---|
| PID, timestamp UTC e executável válidos e idênticos | nenhum código de mudança/incompletude; `stable_observation=true` | elegível aos demais critérios; não basta sozinho para `CANDIDATO` |
| PID válido alterado | `PROCESS_CHANGE_CONFIRMED` | `NÃO CONFORME` |
| Timestamp válido alterado | `PROCESS_CHANGE_CONFIRMED` | `NÃO CONFORME` |
| Executável local válido alterado | `PROCESS_CHANGE_CONFIRMED` | `NÃO CONFORME` |
| Segunda consulta falha, retorna zero ou múltiplos processos | `PROCESS_QUERY_FAILED` e `PROCESS_OBSERVATION_INCOMPLETE` | `INDETERMINADO` |
| Timestamp inicial ou final ausente/inválido | `PROCESS_OBSERVATION_INCOMPLETE` | `INDETERMINADO` |
| Executável inicial ou final ausente/inválido | `PROCESS_OBSERVATION_INCOMPLETE` | `INDETERMINADO` |
| `stable_observation=false` sem código que explique a inconsistência | evidência contraditória | `NÃO CONFORME` |
| Relatório legado com `PROCESS_CHANGED` | sem prova de que houve comparação completa | `INDETERMINADO` |

Uma falha parcial nunca é promovida a `CANDIDATO`. A distinção é deliberadamente
conservadora: somente valores válidos dos dois lados permitem afirmar mudança;
ausência, erro ou impossibilidade de normalização significam evidência
insuficiente, não igualdade nem divergência comprovada.

## Codificação

Os arquivos auditados são UTF-8 estrito sem BOM; não foram encontrados bytes
inválidos, caractere de substituição ou sequências de mojibake. Os textos
corrompidos observados no consolidado decorreram da leitura/apresentação com
encoding incorreto de UTF-8 sem BOM, não dos fontes originais.

Uma exportação futura deve declarar o encoding explicitamente. No PowerShell 7,
usar `Get-Content -Raw -Encoding utf8` e `Set-Content -Encoding utf8NoBOM`. No
Windows PowerShell 5.1, ler com `Get-Content -Raw -Encoding UTF8` e gravar com
`[IO.File]::WriteAllText(..., [Text.UTF8Encoding]::new($false))`. A exportação
não deve ser reutilizada para calcular o SHA-256 do inventário original.
