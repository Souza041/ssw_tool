# OC73 — Inventário planejado do laboratório 3.15.6

Documento exclusivamente **offline**. Não executa descoberta, conexão, SQL,
provisionamento ou validação real. Campos não observados permanecem como
`A preencher`; o documento não presume que a instância exista ou esteja isolada.

## Escopo literal planejado

| Campo | Valor planejado | Estado |
|---|---|---|
| Host TCP | `127.0.0.1` | fixado pela allowlist do protótipo |
| Porta | `3308` | fixada pela allowlist do protótipo |
| Schema | `oc73_autorizacao_transacional_test` | fixado pela allowlist |
| Usuário | `oc73_tester` | fixado pela allowlist |
| Produto | MySQL Community 5.7.32 | requisito; ainda não observado |
| Conta efetiva `user@host` | A preencher | Pendente |
| Identificador da instância | A preencher | Pendente |
| Responsável técnico | A preencher | Pendente |
| Revisão/hash do pacote | A preencher | Pendente |
| Início e expiração da evidência | A preencher | Pendente |

Nenhum valor planejado é evidência de que o processo, binário, volume ou servidor
real corresponda ao contrato.

## A. Evidência Administrativa

Deve ser produzida por administrador autorizado, fora da conta transacional:

| Evidência | Campo a registrar | Coleta futura com menor privilégio |
|---|---|---|
| Processo | PID, usuário do SO e serviço | consulta local somente leitura ao gerenciador de serviços/processos |
| Executável | caminho absoluto, proprietário e permissões | inspeção de metadados do arquivo; sem executar o binário |
| Origem e integridade | fornecedor, artefato, SHA-256 e cadeia de custódia | inventário/manifesto aprovado e hash somente leitura |
| Porta | PID que escuta exclusivamente em `127.0.0.1:3308` | inventário local de sockets, sem abrir conexão |
| Datadir | caminho físico canônico | configuração do serviço e metadados do filesystem |
| Volume dedicado | identificador, mount e dispositivo | inventário somente leitura de volumes/mounts |
| Arquivos compartilhados | declaração e evidência de ausência | comparação administrativa de mounts, links e permissões |
| Replicação | ausência de upstream/downstream operacional | configuração aprovada e inventário administrativo |
| Isolamento | serviço, credenciais e dados exclusivamente experimentais | atestado assinado pelo responsável da instância |
| Temporalidade | emissão, expiração e responsável | registro assinado associado à janela solicitada |

Não basta o servidor declarar seu próprio hostname, UUID ou datadir. Evidência
administrativa deve vincular processo, porta, executável, volume e instância.

## B. Evidência Declarada pelo servidor

Somente depois de autorização específica de inventário consultivo, coletar com
conta sem DDL/DML: versão/distribuição, `server_uuid`, hostname, datadir, schema
efetivo, `CURRENT_USER()`, `USER()`, inventário das cinco tabelas e `SHOW GRANTS`.
Esses dados confirmam a declaração SQL do servidor, não isolamento físico.

Valores ainda pendentes:

- UUID: A preencher;
- hostname: A preencher;
- datadir declarado: A preencher;
- versão completa e comentário de distribuição: A preencher;
- conta efetiva incluindo `@host`: A preencher;
- grants literais e saneados: A preencher;
- inventário de tabelas: A preencher.

## C. Evidência Obtida pelo driver

Depois de inventário e autorização separados, registrar: pacote e versão PyMySQL,
origem, SHA-256, caminho carregado, `DictCursor`, timeouts, `autocommit=False`,
flags solicitados e efetivamente observáveis, ausência de socket e ausência de
`CLIENT.MULTI_STATEMENTS`. Não imprimir DSN, senha ou objetos com credenciais.

## D. Evidência Pendente

Todo campo marcado `A preencher` permanece **Pendente**. Também permanecem
pendentes: compatibilidade real do DDL no MySQL 5.7.32, semântica de locks,
rollback/commit, comportamento do driver, grants efetivos e isolamento real.
Mocks e análise textual não promovem esses itens a comprovados.

## Critérios de aceitação do inventário

GO apenas quando as três fontes forem consistentes, temporalmente válidas e
vinculadas à mesma revisão. Divergência, ausência, evidência expirada, privilégio
excessivo, compartilhamento ou replicação resultam em NO-GO. A coleta de
inventário não autoriza provisionamento, DML ou limpeza.
