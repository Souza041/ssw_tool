# Recuperação produtiva SSW pelo Nexus Local Agent

## Arquitetura resultante

A interface e a fila permanecem na VPS. O único agente autorizado abre um Chromium
visível no servidor Windows interno; o funcionário informa credenciais e resolve o
CAPTCHA diretamente no site oficial do SSW. A VPS envia ao agente somente domínio,
usuário, unidade esperada e um vínculo aleatório de sessão.

Senhas, CPF, cookies, tokens do SSW e perfil Chromium não atravessam o canal e não são
gravados em SQLite, MySQL ou logs. O transporte Chromium homologado mantém o contexto
na memória do agente e detecta retorno ao login, CAPTCHA, fechamento do navegador e
saída da origem oficial.

Cada sessão Nexus possui vínculo próprio. A fila direciona GCE e OP001 ao mesmo agente
que autenticou e usa a mesma chave de concorrência da sessão. Os jobs operacionais têm
uma única tentativa: lease expirado ou transporte incerto encerra a operação sem
repetição automática.

## Ativação da VPS

Preserve os tokens e segredos existentes. Em `/etc/nexus/local-agent.env`:

```dotenv
NEXUS_LOCAL_AGENT_ENABLED=true
NEXUS_SSW_REMOTE_AUTH_ENABLED=true
NEXUS_SSW_REMOTE_AUTH_AGENT_ID=windows-corporativo-01
NEXUS_SSW_REMOTE_AUTH_TIMEOUT_SECONDS=960
NEXUS_SSW_REMOTE_AUTH_REQUEST_TTL_SECONDS=960
NEXUS_SSW_DELEGATION_ENABLED=true
NEXUS_SSW_DELEGATED_TASKS=ssw.documentos_gce,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_LOCAL_AGENT_ALLOWED_TASKS=system.ping,ssw.documentos_gce,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON={"windows-corporativo-01":["system.ping","ssw.documentos_gce","ssw.op001.nfd","ssw.op001.transporte","ssw.session.close"]}
NEXUS_LOCAL_AGENT_MAX_PAYLOAD_BYTES=12582912
NEXUS_LOCAL_AGENT_MAX_RESULT_BYTES=12582912
```

Execute diagnóstico antes de reiniciar:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao env NEXUS_PYTHON_BIN=/home/automacao/venv/bin/python NEXUS_PROJECT_ROOT=/home/automacao/bots/ssw_tool bash deploy/local_agent/diagnose_ubuntu.sh /etc/nexus/local-agent.env
sudo nginx -t
sudo supervisorctl status ssw_web
```

## Ativação do Windows

Em `C:\ProgramData\Nexus\local-agent.env`, preserve URL HTTPS, agente e token
provisionados e habilite:

```dotenv
NEXUS_SSW_REMOTE_AUTH_ENABLED=true
NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=true
NEXUS_LOCAL_AGENT_SSW_TASKS=ssw.documentos_gce,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE=C:\ProgramData\Nexus\ssw-automation.env
NEXUS_OP001_MAX_UPLOAD_BYTES=8388608
```

Em `C:\ProgramData\Nexus\ssw-automation.env`, configure apenas proxy, GCE e banco.
Não inclua credenciais SSW:

```dotenv
SSW_BASE_URL=https://tms.ssw.inf.br
SSW_AUTH_MODE=browser
SSW_HUMAN_LOGIN_TIMEOUT=900
PROXY_SERVER=
PROXY_USER=
PROXY_PASS=
GCE_LOGIN=
GCE_PASSWORD=
DOCUMENTOS_DB_HOST=
DOCUMENTOS_DB_PORT=3306
DOCUMENTOS_DB_USER=
DOCUMENTOS_DB_PASSWORD=
DOCUMENTOS_DB_NAME=
```

O agente que abre Chromium deve ser executado na sessão Windows interativa do
funcionário. Não registre um novo agendamento para esta recuperação:

```powershell
Set-Location C:\Nexus\ssw_tool_novo
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\deploy\local_agent\install_windows_agent.ps1 -ProjectRoot C:\Nexus\ssw_tool_novo -EnvFile C:\ProgramData\Nexus\local-agent.env -Apply
.\deploy\local_agent\diagnose_windows_agent.ps1 -ProjectRoot C:\Nexus\ssw_tool_novo -EnvFile C:\ProgramData\Nexus\local-agent.env
.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_windows.py --env-file C:\ProgramData\Nexus\local-agent.env
```

## Rollback operacional

Primeiro desative somente as flags na VPS e reinicie o processo existente:

```bash
sudo sed -i 's/^NEXUS_SSW_DELEGATION_ENABLED=.*/NEXUS_SSW_DELEGATION_ENABLED=false/; s/^NEXUS_SSW_REMOTE_AUTH_ENABLED=.*/NEXUS_SSW_REMOTE_AUTH_ENABLED=false/' /etc/nexus/local-agent.env
sudo supervisorctl restart ssw_web
sudo supervisorctl status ssw_web
```

Depois interrompa o agente foreground no Windows com `Ctrl+C` e deixe
`NEXUS_SSW_REMOTE_AUTH_ENABLED=false` e
`NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=false` no arquivo privado.

Esse rollback não remove certificados, tokens, SQLite, bancos, ACLs ou arquivos de
ambiente. Para rollback de código, restaure o checkout anterior já identificado pela
equipe na VPS e no Windows, sem apagar os arquivos privados.
