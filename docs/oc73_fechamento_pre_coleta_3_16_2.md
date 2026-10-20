# OC73 — Fechamento definitivo pré-coleta 3.16.2

## Estado da autorização

**APROVAÇÃO NÃO CONCEDIDA.**

Este pacote foi validado apenas com código, análise estática e dados sintéticos.
Não houve execução do script PowerShell, consulta ao Windows, conexão MySQL,
SQL, acesso ao SSW/IdP, alteração administrativa, commit, push ou deploy.

## Problemas corrigidos

O inventário bruto podia transportar afirmações administrativas e o analisador
podia tratá-las como prova. Os campos de produto administrativo, volume dedicado
e ausência administrativa de replicação foram removidos do produtor e do schema
bruto. Tentativas de reinseri-los são rejeitadas como campos desconhecidos.

A validação de caminhos também foi endurecida nos dois lados do contrato.
Somente caminhos locais absolutos nas unidades `C:` a `Z:` são aceitos. UNC,
device paths, extended-length paths, unidades especiais, drive-relative, `..`,
ADS, caracteres de controle, nomes reservados e componentes malformados são
recusados.

## Observação versus comprovação

`product_metadata`, versão de arquivo, assinatura, hash, listener, PID, volume e
indicadores da linha de comando são observações locais. Elas ajudam a detectar
divergências, mas não comprovam propriedade administrativa, dedicação física ou
ausência de replicação.

Comprovação administrativa deve vir de origem independente e ter procedência
verificada fora do conteúdo declarativo. Sem essa verificação, o resultado é
`INDETERMINADO`, mesmo quando todos os campos parecem coerentes.

## Contrato de evidência externa e vínculo criptográfico

O contrato final `oc73-administrative-evidence-v2` é uma entrada separada do
inventário e exige exatamente:

- identificador da evidência e da instância;
- responsável e origem categórica;
- data de observação e prazo de validade;
- SHA-256 da revisão do procedimento;
- referência documental HTTPS ou URN OC73 verificável;
- SHA-256 dos bytes exatos do inventário bruto;
- PID, marcador da instância, caminho e SHA-256 do executável;
- produto, ID do volume, dedicação e ausência de replicação declarados.

O SHA-256 é calculado sobre os bytes recebidos, sem reserialização. Uma entrada
`str` é definida pelos bytes de sua codificação UTF-8 exata. Se o chamador usar
um objeto Python, precisa fornecer separadamente os bytes originais; eles são
parseados e comparados ao objeto. Sem esses bytes, não há vínculo criptográfico.
JSON semanticamente igual com whitespace, ordem ou codificação diferentes possui
outro digest e não pode reutilizar a evidência.

A versão `v1` continua reconhecida para compatibilidade, mas sempre resulta em
`INDETERMINADO`, pois não contém o digest do inventário.

## Revisão autorizada e verificador de procedência

`procedure_revision_sha256` somente é aceito quando comparado com uma revisão
aprovada fornecida independentemente. A origem prevista é o registro de mudança
ou autorização controlado pelo responsável humano. Nenhuma autoridade, revisão
ou aprovação foi inventada neste hotfix. Sem valor aprovado, o resultado é
`INDETERMINADO`; divergência é `NÃO CONFORME`.

O callback retorna um `ResultadoProcedencia` estruturado, vinculado ao ID da
evidência, hash do inventário e revisão aprovada, além de referência da
autoridade. Retornar apenas `True`, omitir autoridade, divergir dos vínculos ou
lançar exceção mantém `INDETERMINADO`. Essa interface é somente uma fronteira de
integração: um objeto Python não comprova procedência real sem uma autoridade
independente efetivamente integrada e auditada.

## Contrato JSON bruto final

O produtor usa `oc73-inventario-windows-v2`. PIDs de processo/listener são
inteiros positivos; serviço parado usa PID `null`. Processos têm metadados
locais, hash, assinatura, datadir, volume observado, marcador e estabilidade.
Volumes possuem apenas `status` e `id`. Replicação possui apenas `status` e
indicadores locais. Erros pertencem a uma allowlist categórica.

O relatório é limitado a 256 KiB, rejeita chaves duplicadas e campos extras e
mantém limites por coleção. O relatório bruto sozinho nunca chega a
`CANDIDATO` porque não contém prova administrativa independente.

## Garantias testadas offline

- modos inertes separados de `-Collect`;
- autorização e destino validados antes da coleta;
- ausência de SQL, binários MySQL, elevação, descoberta de rede e mutações;
- escopo de processo limitado ao PID que escuta a porta 3308;
- sanitização por categorias e ausência de linha de comando integral;
- `FileMode.CreateNew`, `FileShare.None`, revalidação e fechamento em `finally`;
- contrato de PID, timestamps, enums, endereços, volumes e erros;
- rejeição de autodeclarações administrativas;
- vínculo SHA-256 aos bytes exatos, inclusive diferença entre JSONs semanticamente iguais;
- comparação da revisão declarada com revisão aprovada independente;
- rejeição de callback booleano ou resultado sem vínculos exatos;
- vínculo da evidência externa a instância, processo, executável e volume;
- falha fechada para evidência ausente, expirada, divergente ou não verificada;
- listener externo, coleta parcial e troca de processo não são promovidos.

Mocks e fixtures sintéticas não comprovam o estado do Windows nem do banco real.

## Garantias ainda não demonstradas

- identidade real da máquina e do administrador executor;
- integração com uma autoridade externa real de procedência;
- revisão aprovada obtida de registro independente e aceita pelo responsável;
- existência e estado reais do listener, processo, serviço, volume e shares;
- identidade administrativa MySQL Community 5.7.32;
- dedicação física e administrativa do volume;
- ausência real de replicação;
- resistência completa a TOCTOU promovido por administrador local;
- qualquer identidade, privilégio, schema ou conteúdo MySQL;
- persistência de referência OC73 no histórico real do SSW.

## Riscos residuais

Reparse points são verificados antes e imediatamente antes da criação, e
`FileMode.CreateNew` impede sobrescrita, mas um administrador local ainda pode
alterar o filesystem entre verificações. Falha depois da criação pode deixar um
arquivo parcial fechado, que deve ser descartado como evidência. Metadados e
assinatura de arquivo não substituem verificação administrativa. A referência
textual de autorização não é assinatura criptográfica.

O SHA-256 prova igualdade de conteúdo, não autoria, aprovação ou correção da
coleta. O `ResultadoProcedencia` também não é confiável por existir: a confiança
depende integralmente da integração futura que o produzir e da autoridade
humana que a aprovar.

## Compatibilidade PowerShell/Python 3.16.3

O marcador de instância definitivo é
`PID|yyyy-MM-ddTHH:mm:ss.fffffffZ`. `DateTime`, `DateTimeOffset`, ISO round-trip
e CIM/DMTF estrito são convertidos para UTC com cultura invariável. Não é usada
a data atual como fallback. Conversão impossível produz marcador `null`, erro
`PROCESS_OBSERVATION_INCOMPLETE` e resultado inconclusivo. Divergência de PID,
timestamp ou executável só produz `NÃO CONFORME` quando ambas as observações são
válidas, por meio de `PROCESS_CHANGE_CONFIRMED`. O antigo `PROCESS_CHANGED` é
aceito apenas para compatibilidade e permanece inconclusivo, pois não demonstra
se houve mudança confirmada ou falha de observação.

Segunda consulta com exceção, zero ou múltiplos resultados, timestamp inicial
ou final inválido e executável inicial ou final ausente/inválido são todos
`INDETERMINADO`. Já `stable_observation=false` sem código de incompletude é uma
contradição não explicada e permanece `NÃO CONFORME`. Em nenhum desses casos
uma falha parcial pode resultar em `CANDIDATO`.

Falhas parciais usam `null`, listas vazias ou status `INDETERMINADO` conforme o
campo, sempre acompanhadas do código em `errors`. Falha de consulta do processo
com listener observado não é mais confundida com divergência de PID: torna o
resultado `INDETERMINADO`. Tipos estruturais inválidos, inclusive status em
lista/objeto e booleanos usados como inteiros, resultam uniformemente em
`InventarioInvalido`.

A matriz completa produtor → consumidor está em
`docs/oc73_inventario_windows_3_16.md`. A auditoria estática confirmou UTF-8
estrito sem BOM e ausência de mojibake nos arquivos originais. A corrupção vista
no TXT consolidado ocorreu na leitura/apresentação sem encoding explícito. Uma
exportação deve declarar UTF-8 e nunca substituir os bytes usados no SHA-256 do
inventário original.

## Auditoria estática integral do PowerShell

A leitura integral e o parse AST foram feitos sem executar o arquivo:

- parâmetros e modos estão declarados nas linhas 2–8; exatamente um modo é
  exigido nas linhas 638–641;
- `-Explain` e `-PlanOnly` retornam antes da coleta, linhas 650–659;
- a referência textual é apenas requisito sintático, linhas 661–663, e não é
  descrita como autorização verificada;
- destino e combinações de parâmetros são validados antes da única chamada de
  coleta, linhas 638–676;
- `Test-OutputDestination`, linhas 208–273, rejeita destinos não locais,
  componentes inválidos, arquivos existentes e reparse points;
- `Write-ExclusiveJson`, linhas 275–308, revalida o destino, usa
  `FileMode.CreateNew`/`FileShare.None` nas linhas 292–294 e fecha streams em
  `finally`, linhas 304–306;
- um arquivo parcial após erro permanece fechado e não satisfaz o contrato JSON
  validado; deve ser descartado, nunca aceito como evidência;
- a coleta começa pelo listener 3308 na linha 336 e consulta processo somente
  por `ProcessId` nas linhas 379–380 e 489–490;
- a linha de comando integral não é emitida: a função das linhas 75–111 produz
  apenas flags categóricas e a saída usa somente esse resumo nas linhas 548–551;
- hash, assinatura, volume e shares são consultas locais nas linhas 422, 427,
  460 e 592; não há descoberta remota;
- a normalização temporal está nas linhas 124–190; comparação das duas
  observações e falha fechada estão nas linhas 480–533;
- não há SQL, execução de `mysql.exe`/`mysqld.exe`, elevação, alteração de
  serviço, registro, firewall, ACL ou instalação;
- a escrita só ocorre com `-WriteJson`, linhas 671–681;
- o produtor declara `oc73-inventario-windows-v2` na linha 621 e não produz
  campos administrativos de produto, dedicação ou ausência de replicação.

O inventário de comandos obtido da AST contém apenas funções locais e os
cmdlets consultivos documentados. Não foram encontrados operadores de chamada
dinâmica, `Invoke-Expression`, processos filhos, rede ativa ou mutações.

## Critérios para solicitar autorização

O pacote pode ser submetido à revisão final somente se o hash/revisão exatos do
script forem fixados, máquina, usuário, janela e diretório forem preenchidos,
um responsável aceitar os riscos e a autorização disser separadamente se a
gravação JSON é permitida. A evidência externa e seu mecanismo real de
verificação de procedência também devem ser definidos e revisados.

## Comandos previstos para uma futura execução

Estes comandos são documentação, não autorização, e não foram executados:

```powershell
powershell.exe -NoProfile -File .\scripts\oc73_inventario_windows_readonly.ps1 -Explain
powershell.exe -NoProfile -File .\scripts\oc73_inventario_windows_readonly.ps1 -PlanOnly
powershell.exe -NoProfile -File .\scripts\oc73_inventario_windows_readonly.ps1 -Collect -AuthorizationReference '<REFERENCIA_APROVADA>'
powershell.exe -NoProfile -File .\scripts\oc73_inventario_windows_readonly.ps1 -Collect -AuthorizationReference '<REFERENCIA_APROVADA>' -WriteJson -OutputPath 'D:\oc73-evidencias\inventario.json'
```

Somente o modo e o comando explicitamente aprovados poderão ser usados. Uma
aprovação de coleta não autoriza automaticamente gravação.

## Critérios de interrupção

Interromper sem tentar contornar se houver divergência de máquina, usuário,
janela, revisão, diretório ou autorização; solicitação de elevação; destino
existente/reparse/mudado; erro parcial; listener externo; múltiplos PIDs;
processo trocado; suspeita de segredo; ou qualquer necessidade de SQL, rede,
serviço, firewall, registro ou permissão administrativa.

## Parecer

O código está **PRONTO PARA SOLICITAR AUTORIZAÇÃO DE COLETA**, condicionado à
revisão do diff exato. Inventário real, MySQL, DDL/DML, homologação SSW e produção
OC73 permanecem **NO-GO** até autorizações separadas e evidências reais.
