# Nexus Local Agent — implantação e homologação

## Escopo aprovado

O Local Agent possui dois processos independentes:

- **VPS Ubuntu:** FastAPI sob Supervisor, fila e auditoria em SQLite.
- **Windows corporativo:** executor automático sob Agendador de Tarefas.

O único handler compilado e autorizado nesta fase é `system.ping`. Não existe execução de shell, login alternativo, acesso SSW pelo agente ou handler para GCE, OC73, Health Center, e-mail ou banco operacional.

As duas chaves de ativação permanecem desligadas nos exemplos:

```text
NEXUS_LOCAL_AGENT_ENABLED=false
NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED=false
```

## Bootstrap operacional sem sessão SSW

O bootstrap expõe somente:

- `POST /api/local-agent/v1/bootstrap/system-ping` — enfileira `system.ping`.
- `POST /api/local-agent/v1/bootstrap/system-ping/{id}/status` — consulta apenas pings criados pelo bootstrap.

Cada chamada exige identificador, timestamp Unix, nonce aleatório e assinatura HMAC-SHA256. A assinatura cobre método, caminho, timestamp, nonce e SHA-256 do corpo canônico. Nonces são persistidos como hash e possuem unicidade no SQLite, impedindo repetição após reinício ou entre workers.

Os limites por minuto e hora consideram chave e endereço remoto. Sucessos, falhas de assinatura, timestamps inválidos, repetições e bloqueios são auditados sem guardar segredos ou o nonce original.

O segredo do bootstrap deve ser diferente do token do executor. Ele não cria cookie, sessão ou identidade reutilizável e não aceita campo `task`.

## Arquivos privados

O arquivo `/etc/nexus/local-agent.env` fica fora do checkout, pertence a `automacao:automacao` e usa modo `0600`. O loader recusa arquivos dentro do repositório, permissões Unix abertas, proprietário diferente e variáveis fora do prefixo `NEXUS_LOCAL_AGENT_`.

O executor usa `C:\ProgramData\Nexus\local-agent.env`. O instalador remove herança da ACL e concede acesso somente a Administradores, SYSTEM e LOCAL SERVICE. Nenhum comando de diagnóstico imprime tokens.

## Preparação da VPS — sem ativar

No checkout revisado da VPS:

```bash
cd /home/automacao/bots/ssw_tool
sudo bash deploy/local_agent/install_ubuntu.sh --apply
```

O comando cria ou valida, sem sobrescrever conteúdo existente:

- `/etc/nexus/local-agent.env` — `automacao:automacao`, `0600`;
- `/var/lib/nexus` — `automacao:automacao`, `0750`;
- `/var/lib/nexus/local-agent.sqlite3` — `automacao:automacao`, `0600`;
- `/var/log/nexus` — `automacao:automacao`, `0750`.

Ele não executa `supervisorctl`, não reinicia o Nexus e mantém as flags falsas.

Provisione o HMAC e o token individual sem colocá-los no histórico ou na saída. O
arquivo de transferência deve ficar fora do checkout e ser enviado ao Windows por
canal seguro:

```bash
sudo /home/automacao/venv/bin/python \
  /home/automacao/bots/ssw_tool/scripts/nexus_local_agent_provision.py \
  --server-url https://HOST_NEXUS \
  --windows-env-output /root/nexus-local-agent-windows.env
```

O comando é idempotente, preserva credenciais válidas, mantém as duas flags falsas,
grava atomicamente com modo `0600` e nunca imprime os valores. Rotação exige a opção
explícita `--rotate` e somente é aceita com ambas as flags desligadas. A configuração
resultante segue este contrato (os valores secretos não são literais):

```text
NEXUS_LOCAL_AGENT_ENABLED=false
NEXUS_LOCAL_AGENT_DB=/var/lib/nexus/local-agent.sqlite3
NEXUS_LOCAL_AGENT_TOKENS_JSON={"windows-corporativo-01":"TOKEN_EXECUTOR"}
NEXUS_LOCAL_AGENT_ALLOWED_TASKS=system.ping
NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON={"windows-corporativo-01":["system.ping"]}
NEXUS_LOCAL_AGENT_LEASE_SECONDS=120

NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED=false
NEXUS_LOCAL_AGENT_BOOTSTRAP_KEY_ID=homologacao-admin
NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET=SEGREDO_BOOTSTRAP_DIFERENTE
NEXUS_LOCAL_AGENT_BOOTSTRAP_MAX_SKEW_SECONDS=60
NEXUS_LOCAL_AGENT_BOOTSTRAP_PER_MINUTE=5
NEXUS_LOCAL_AGENT_BOOTSTRAP_PER_HOUR=20
NEXUS_LOCAL_AGENT_BOOTSTRAP_URL=https://HOST_NEXUS
```

O arquivo [supervisor-nexus.conf.example](../deploy/local_agent/supervisor-nexus.conf.example) mostra como carregar explicitamente o ambiente privado. Mescle manualmente o `command=` no programa Nexus existente; não crie um segundo servidor na mesma porta.

Validação somente leitura:

```bash
cd /home/automacao/bots/ssw_tool
sudo bash deploy/local_agent/diagnose_ubuntu.sh /etc/nexus/local-agent.env
```

Para um Python ou checkout não padrão, o diagnóstico aceita explicitamente
`NEXUS_PYTHON_BIN` e `NEXUS_PROJECT_ROOT`. Falta de permissão para consultar o
Supervisor é apenas aviso sanitizado; validações locais continuam sendo executadas.

Somente após revisão e janela aprovada:

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl restart ssw_web
```

Esses três comandos não são executados pelos scripts do repositório.

## Preparação do Windows — sem iniciar

Crie o venv no checkout corporativo e instale os requisitos conforme o procedimento padrão. Em PowerShell elevado:

```powershell
Set-Location C:\Nexus\ssw_tool
.\deploy\local_agent\install_windows_agent.ps1 -ProjectRoot C:\Nexus\ssw_tool -Apply
```

Na primeira execução o script cria o arquivo sem token, restringe a ACL e interrompe
antes de registrar uma configuração utilizável. Transfira o pacote gerado na VPS por
canal corporativo seguro para `C:\ProgramData\Nexus\local-agent.env`, preservando a
ACL, ou preencha-o por editor seguro:

```text
NEXUS_LOCAL_AGENT_SERVER_URL=https://HOST_NEXUS
NEXUS_LOCAL_AGENT_ID=windows-corporativo-01
NEXUS_LOCAL_AGENT_TOKEN=TOKEN_EXECUTOR
NEXUS_LOCAL_AGENT_POLL_SECONDS=5
NEXUS_LOCAL_AGENT_REQUEST_TIMEOUT_SECONDS=15
NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP=false
```

Execute novamente o instalador apenas para validar ambiente e ACL. Nesta etapa ele
**não registra** tarefa automática:

```powershell
.\deploy\local_agent\install_windows_agent.ps1 -ProjectRoot C:\Nexus\ssw_tool -Apply
```

Diagnóstico offline:

```powershell
.\deploy\local_agent\diagnose_windows_agent.ps1 -ProjectRoot C:\Nexus\ssw_tool
```

Homologação foreground de um único polling, sem tarefa agendada:

```powershell
.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_windows.py --env-file C:\ProgramData\Nexus\local-agent.env --once
```

## Homologação ponta a ponta

1. Na VPS, mantenha ambas as flags falsas, instale arquivos, revise Supervisor e faça os diagnósticos.
2. Mescle somente o carregamento explícito do ambiente privado no programa Supervisor
   existente `ssw_web`, preservando integralmente suas demais opções e variáveis.
3. Em janela aprovada, use `sudoedit /etc/nexus/local-agent.env`, altere somente
   `NEXUS_LOCAL_AGENT_ENABLED=true`, execute `sudo supervisorctl restart ssw_web` e
   valide que nenhum job foi criado.
4. Prepare o Windows e execute `--once`; a fila ainda estará vazia.
5. Use novamente `sudoedit /etc/nexus/local-agent.env`, altere somente
   `NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED=true` e execute
   `sudo supervisorctl restart ssw_web` na janela aprovada.
6. Enfileire o ping sem colocar segredo no comando:

```bash
sudo -u automacao /home/automacao/venv/bin/python \
  /home/automacao/bots/ssw_tool/scripts/nexus_local_agent_bootstrap.py \
  --env-file /etc/nexus/local-agent.env enqueue --message homologacao
```

7. Execute um polling único no Windows:

```powershell
.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_windows.py --env-file C:\ProgramData\Nexus\local-agent.env --once
```

8. Consulte o resultado usando um nonce novo gerado pelo cliente:

```bash
sudo -u automacao /home/automacao/venv/bin/python \
  /home/automacao/bots/ssw_tool/scripts/nexus_local_agent_bootstrap.py \
  --env-file /etc/nexus/local-agent.env status ID_RETORNADO
```

Resultado esperado: `SUCCEEDED`, progresso `1/1`, `ok=true`, logs do handler e agente Windows visível. O E2E automatizado cobre enfileiramento assinado, claim, execução, heartbeat, logs, conclusão e consulta assinada.

Somente depois desse resultado, registre a inicialização automática. O instalador
configura LOCAL SERVICE, boot, privilégio limitado e recuperação, mas não inicia a
tarefa nesta execução:

```powershell
.\deploy\local_agent\install_windows_agent.ps1 -ProjectRoot C:\Nexus\ssw_tool -Apply -RegisterTask
```

## Operação e auditoria

Administradores SSW existentes continuam podendo usar `/admin/local-agent`; nenhuma regra desse acesso foi relaxada. A resposta inclui auditoria do bootstrap sem assinatura, segredo, token ou lease token.

O SQLite usa `BEGIN IMMEDIATE` para claim, WAL, token de lease e recuperação de expiração. Arquivo e sidecars recebem modo `0600`; o diretório limita travessia a `automacao` e seu grupo.

## Rollback

Em ordem:

```bash
sudoedit /etc/nexus/local-agent.env
# Defina, nesta ordem:
# NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED=false
# NEXUS_LOCAL_AGENT_ENABLED=false
sudo supervisorctl restart ssw_web
```

No Windows:

```powershell
Stop-ScheduledTask -TaskName "Nexus Local Agent"
Disable-ScheduledTask -TaskName "Nexus Local Agent"
```

Preserve `/var/lib/nexus/local-agent.sqlite3` e os sidecars para auditoria. Não remova automaticamente a fila, não reutilize os segredos e não altere OC73, GCE, Health Center ou agendamentos existentes como parte do rollback.

## Riscos remanescentes

- Reverse proxy deve preservar HTTPS e sincronização NTP deve manter o desvio dentro da janela configurada.
- O SQLite requer backup consistente e monitoramento de espaço em disco antes de uso prolongado.
- A conta LOCAL SERVICE precisa de saída HTTPS até a VPS e leitura do checkout/arquivo privado.
- Rotação de tokens exige coordenação entre VPS e Windows; não há rotação automática nesta fase.
- Os instaladores preparam arquivos e tarefa, mas mudanças de Supervisor, início da tarefa e ativação das flags continuam manuais.
