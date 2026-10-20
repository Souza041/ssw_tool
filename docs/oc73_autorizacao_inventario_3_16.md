# OC73 — Autorização para inventário local — Sprint 3.16

> **APROVAÇÃO NÃO CONCEDIDA.**

Este modelo não executa coleta nem contém valores fictícios de aprovação.

## Identificação obrigatória

| Campo | Valor |
|---|---|
| Responsável autorizador | A preencher |
| Máquina autorizada | A preencher |
| Usuário executor | A preencher |
| Responsável executor | A preencher |
| Janela de execução com timezone | A preencher |
| Prazo de validade | A preencher |
| Hash/revisão do script | A preencher |
| Revisão do analisador e schema JSON | A preencher |
| Referência externa da autorização | A preencher |

A referência é vínculo textual de auditoria e não constitui assinatura ou prova
criptográfica de autorização.

## Escopo e comandos permitidos

Enumerar individualmente os cmdlets/APIs autorizados, dentre:

- `Get-CimInstance` para `Win32_OperatingSystem`, `Win32_Service` e `Win32_Process`;
- `Get-NetTCPConnection -State Listen`;
- `Get-AuthenticodeSignature` e `Get-FileHash -Algorithm SHA256` sobre executáveis identificados;
- `System.Diagnostics.FileVersionInfo`;
- `Get-Volume` e `Get-SmbShare`.

Comandos permitidos efetivos: A preencher. Qualquer fonte não enumerada é proibida.

## Gravação do JSON

- Permissão ou proibição de gravar JSON: A preencher;
- Diretório de saída, se autorizado: A preencher;
- Política de retenção e acesso: A preencher;
- Confirmação de que o diretório já existe: A preencher.
- Confirmação de caminho local absoluto, sem UNC/reparse point: A preencher.
- Confirmação de que criação exclusiva de arquivo novo é permitida: A preencher.

Sem permissão explícita, usar saída efêmera e não gravar arquivo. A autorização de
coleta não implica autorização de gravação.

## Operações proibidas

SQL, TCP/socket ativo, MySQL/MariaDB executável, iniciar/parar serviço, alteração de
registro/firewall/ACL/configuração, instalação, elevação, rede, `.env`, ambiente
completo, arquivos de dados, conteúdo de banco, SSW, DDL, DML e limpeza.

## Condições de interrupção

Interromper se máquina/usuário/revisão/janela divergirem, a fonte exigir elevação,
o script pedir comando não autorizado, surgir dado sensível, houver erro não
sanitizado ou o destino de gravação não corresponder literalmente ao aprovado.

## Aceite

- decisão: A preencher — APROVAR ou REJEITAR;
- escopo autorizado: A preencher;
- gravação autorizada: A preencher;
- condições adicionais: A preencher;
- assinatura/referência verificável e expiração: A preencher.

Até o preenchimento e aceite explícito, `-Collect` e gravação permanecem NO-GO.
Esta autorização nunca concede conexão MySQL, DDL, DML, SSW ou produção.

## Condição adicional da Sprint 3.16.2

**APROVAÇÃO NÃO CONCEDIDA.**

O inventário bruto `oc73-inventario-windows-v2` não produz prova
administrativa. Qualquer futura revisão deverá receber separadamente uma
evidência `oc73-administrative-evidence-v2`, verificar sua procedência fora do
JSON e conferir o vínculo exato com o SHA-256 dos bytes originais, instância,
PID, marcador, executável, hash e volume observados. Evidências `v1` permanecem
inconclusivas. O preenchimento do contrato não substitui a autorização
expressa de coleta e não concede escrita, SQL, homologação SSW ou produção.

A revisão aprovada do procedimento deve ser obtida independentemente no registro
de mudança ou autorização assinado pelo responsável. Ela não pode ser copiada
do inventário ou aceita por coincidir com o valor autodeclarado na evidência.
Este documento não registra atualmente autoridade integrada nem revisão
aprovada: ambas continuam pendentes de decisão humana.
