# Nexus Local Agent multiusuário — homologação e operação

## Resultado e limites de segurança

O backend escolhe o agente pelo vínculo persistente `domínio/usuário -> agent_id` criado
no pareamento. O navegador nunca é escolhido pelo frontend. Cada código de pareamento
possui 96 bits aleatórios, expira, só pode ser usado uma vez e gera no servidor um
`agent_id` e token exclusivos. A VPS guarda somente SHA-256 do token; o Windows guarda
o token cifrado com DPAPI no escopo do usuário.

Cookies, senha SSW, CAPTCHA e contexto Playwright permanecem no computador do usuário.
Documentos GCE e OP001 interativos recebem `target_agent_id` e a mesma chave de
concorrência da sessão autenticada. O agente central `windows-corporativo-01` continua
válido e só recebe autenticações das identidades explicitamente mantidas no mapa legado.

No GCE manual, a VPS baixa o Portal GCE e executa importação, matching, alertas,
métricas e auditoria. A tarefa de coleta consulta OP455 e OP930 na sessão SSW já
aberta no agente do colaborador. Sessão expirada interrompe a coleta e pede nova
entrada no Nexus. O agendamento continua no agente central com o modo SSW e as
opções de notificação já configurados.

Cada relatório viaja por HTTPS com autenticação do agente e lease da tarefa.
A VPS confere SHA-256 na entrada e antes de importar. O limite é 16 MiB por
relatório. A fila elimina as cópias transitórias após consumo, falha ou
cancelamento. Sobras de uma interrupção abrupta expiram em até duas horas e
são removidas na próxima atividade da fila. A tarefa conserva apenas tamanho e
hash dos arquivos na auditoria. Os temporários no disco são removidos ao fim da
execução. Se houver desligamento forçado, inclua os diretórios temporários do
usuário de execução na limpeza operacional da máquina.

O instalador não contém tokens, senhas, certificados privados nem credenciais de proxy.
`requests` usa o proxy configurado para o usuário Windows e o Chromium usa `PROXY_*`
quando definidos no ambiente privado opcional. Proxy com autenticação integrada pode ser
configurado pelo Windows; credenciais compartilhadas não devem ser colocadas no setup.

## 1. Gerar e assinar o instalador

Em uma máquina Windows de build com Python 3.11+ e Inno Setup 6:

```powershell
Set-Location C:\fontes\ssw_tool_multiuser
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_agent.ps1 -Version 0.2.0
Get-Content .\dist\installer\SHA256SUMS.txt
```

O script cria um ambiente de build isolado, instala dependências, baixa o Chromium da
versão Playwright fixada em `requirements.txt`, empacota `NexusAgent.exe` sem console,
gera `NexusAgentSetup.exe` e grava SHA-256 dos dois artefatos. O download ocorre apenas
na máquina de build; o computador do colaborador não precisa de Python ou Playwright.

Antes da distribuição, assine em uma estação que possua o certificado de code signing
no repositório do usuário (a chave privada nunca entra no repositório):

```powershell
signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /a .\dist\NexusAgent.exe
signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /a .\dist\installer\NexusAgentSetup.exe
signtool verify /pa /all .\dist\installer\NexusAgentSetup.exe
Get-FileHash -Algorithm SHA256 .\dist\installer\NexusAgentSetup.exe
```

Recalcule e publique o SHA-256 depois da assinatura. A reprodução binária exata depende
das mesmas versões de Python, PyInstaller, Inno Setup e timestamp; a reprodução funcional
é determinada pelos arquivos versionados e dependências fixadas.

## 2. Atualizar a VPS

Gere primeiro o instalador individual a partir do commit aprovado. Aguarde no
Agent Manager a conclusão dos jobs GCE pendentes e faça a troca fora da janela
do agendamento. Execute após a equipe registrar o commit homologado desta branch.

```bash
cd /home/automacao/bots/ssw_tool
git fetch origin feat/nexus-agent-multiuser
git switch feat/nexus-agent-multiuser
git pull --ff-only origin feat/nexus-agent-multiuser
/home/automacao/venv/bin/python -m pip install -r requirements.txt
sudo cp -a /etc/nexus/local-agent.env /etc/nexus/local-agent.env.pre-gce-split
sudoedit /etc/nexus/local-agent.env
```

Mantenha os tokens e o agente central existentes e configure:

```dotenv
NEXUS_LOCAL_AGENT_ENABLED=true
NEXUS_LOCAL_AGENT_OFFLINE_SECONDS=30
NEXUS_LOCAL_AGENT_PAIRING_ENABLED=true
NEXUS_LOCAL_AGENT_PAIRING_TTL_SECONDS=600
NEXUS_LOCAL_AGENT_PAIRED_TASKS=system.ping,ssw.documentos_gce.collect_ssw,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_SSW_REMOTE_AUTH_ENABLED=true
NEXUS_SSW_REMOTE_AUTH_AGENT_ID=windows-corporativo-01
NEXUS_SSW_REMOTE_AUTH_BINDINGS_JSON={"rod/usuario.admin":"windows-corporativo-01"}
NEXUS_SSW_DELEGATION_ENABLED=true
NEXUS_SSW_DELEGATED_TASKS=ssw.documentos_gce,ssw.documentos_gce.collect_ssw,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_LOCAL_AGENT_ALLOWED_TASKS=system.ping,ssw.documentos_gce,ssw.documentos_gce.collect_ssw,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON={"windows-corporativo-01":["system.ping","ssw.documentos_gce","ssw.op001.nfd","ssw.op001.transporte","ssw.session.close"]}
NEXUS_LOCAL_AGENT_MAX_PAYLOAD_BYTES=12582912
NEXUS_LOCAL_AGENT_MAX_RESULT_BYTES=12582912
```

Mescle as listas com as permissões existentes. Administradores também precisam
de sessão em agente individual para iniciar GCE manual. O grant GCE legado de um
agente pareado autoriza somente a coleta, se ela estiver na allowlist de pareados
da VPS. Pareamentos sem grant GCE podem receber autorização via novo pareamento
aprovado no Agent Manager.

Configure GCE_LOGIN, GCE_PASSWORD e DOCUMENTOS_DB_HOST, DOCUMENTOS_DB_PORT,
DOCUMENTOS_DB_USER, DOCUMENTOS_DB_PASSWORD e DOCUMENTOS_DB_NAME no ambiente
privado já usado por ssw_web ou no .env da aplicação protegido com modo 0600.
Preserve os parâmetros homologados do Portal e do banco, inclusive CA e proxy
quando aplicáveis. Preserve também DOCUMENTOS_OP930_CNPJ, DOCUMENTOS_OP930_GROUP,
DOCUMENTOS_DAILY_OVERLAP_DAYS e DOCUMENTOS_SSW_REPORT_TIMEOUT. A VPS precisa
alcançar o Portal e o banco. Mantenha o ssw_web com um worker. O agendador
e o backend devem usar o mesmo SQLite local da fila.

Se usar o .env da aplicação:

```bash
sudoedit /home/automacao/bots/ssw_tool/.env
sudo chown automacao:automacao /home/automacao/bots/ssw_tool/.env
sudo chmod 0600 /home/automacao/bots/ssw_tool/.env
```

Confira que o bloco HTTPS existente aceita PUT em /api/local-agent/v1/gce-reports/
e tem client_max_body_size de pelo menos 16m nesse prefixo. Recarregue o Nginx
se sua configuração tiver mudado.

```nginx
location /api/local-agent/v1/gce-reports/ {
    client_max_body_size 16m;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Aplique o limite ao bloco existente se ele já atender esse prefixo. Preserve as
diretivas de autenticação, cabeçalhos e restrições de acesso do proxy atual.

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao env NEXUS_PYTHON_BIN=/home/automacao/venv/bin/python NEXUS_PROJECT_ROOT=/home/automacao/bots/ssw_tool bash deploy/local_agent/diagnose_ubuntu.sh /etc/nexus/local-agent.env
sudo nginx -t
sudo systemctl reload nginx
sudo supervisorctl restart ssw_web
sudo supervisorctl status ssw_web
```

## 3. Coordenar agente central e computadores individuais

O agente central que só processa o agendamento continua compatível com o contrato
de fila. Preserve seu ambiente SSW privado. Para atualizar o checkout central,
encerre o agente pelo mecanismo operacional existente e reinicie na mesma sessão
autorizada. Para a instância que usa o script de foreground no checkout:

```powershell
Set-Location C:\Nexus\ssw_tool_novo
git fetch origin feat/nexus-agent-multiuser
git switch feat/nexus-agent-multiuser
git pull --ff-only origin feat/nexus-agent-multiuser
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Start-Process -FilePath (Resolve-Path .\.venv\Scripts\pythonw.exe) -ArgumentList '.\scripts\nexus_local_agent_windows.py --env-file C:\ProgramData\Nexus\local-agent.env' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden
```

Nos computadores individuais já pareados, encerre o agente pela bandeja e
instale a nova versão na sessão do colaborador. A instalação conserva o
pareamento, o arquivo DPAPI e o proxy PAC. Após a troca da VPS, entre uma vez
no Nexus para abrir a sessão SSW que será usada na coleta.

```powershell
Start-Process -FilePath .\NexusAgentSetup.exe -ArgumentList '/VERYSILENT','/NORESTART' -Wait
Start-Process -FilePath "$env:LOCALAPPDATA\Programs\Nexus Local Agent\NexusAgent.exe" -WindowStyle Hidden
```

### Primeira instalação

Na VPS, um administrador emite um código para a identidade SSW exata:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao /home/automacao/venv/bin/python scripts/nexus_local_agent_admin.py --env-file /etc/nexus/local-agent.env --pair ROD/usuario.colaborador
```

Envie apenas o instalador, a URL HTTPS pública do Nexus e o código temporário por canal
corporativo. O colaborador dá duplo clique em `NexusAgentSetup.exe`, aceita a instalação
por usuário e informa URL/código na primeira abertura. Não é necessário administrador,
Python, terminal ou instalação separada do Chromium. O ícone verde na bandeja permanece
na sessão interativa; fechar o ícone interrompe o agente até o próximo logon.

Instalação silenciosa, quando feita pelo próprio usuário:

```powershell
.\NexusAgentSetup.exe /VERYSILENT /NORESTART
& "$env:LOCALAPPDATA\Programs\Nexus Local Agent\NexusAgent.exe"
```

O agente inicia por um atalho em `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`.
O mutex por sessão impede duas instâncias. A configuração fica em
`%LOCALAPPDATA%\Nexus\Agent\config.json`; o token dentro dela é DPAPI e não funciona para
outro usuário Windows ou em outro computador.

Agentes individuais usam a sessão SSW e as configurações locais de proxy.
Se a implantação anterior distribuiu credenciais de banco ou Portal, retire
essas chaves dos arquivos individuais, preserve as configurações de proxy e
rotacione as credenciais anteriormente distribuídas.

## 4. Revogação, desinstalação e atualização

Revogue primeiro na VPS; a próxima chamada do agente recebe `401` e jobs ainda na fila
para ele são cancelados:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao /home/automacao/venv/bin/python scripts/nexus_local_agent_admin.py --env-file /etc/nexus/local-agent.env --revoke note-usuario-1234abcd
```

Depois use **Configurações > Aplicativos > Nexus Local Agent > Desinstalar**. O uninstall
encerra o processo e remove binário, autostart, configuração DPAPI e runtime privado do
usuário. Para atualizar, gere uma versão maior e execute o novo `NexusAgentSetup.exe`;
o `AppId` estável atualiza os binários e preserva o pareamento. Revogue e repare somente
quando trocar o usuário/computador ou suspeitar de comprometimento.

## 5. Rollback

Para suspender somente GCE manual, retire ssw.documentos_gce.collect_ssw de
NEXUS_SSW_DELEGATED_TASKS e reinicie ssw_web. O agendamento e OP001 continuam
com suas respectivas tarefas.

```bash
sudoedit /etc/nexus/local-agent.env
sudo supervisorctl restart ssw_web
sudo supervisorctl status ssw_web
```

Para reverter o código, drene as execuções antes de restaurar a versão anterior.
O commit baae25a restaura o fluxo GCE manual centralizado.

```bash
cd /home/automacao/bots/ssw_tool
sudo supervisorctl stop ssw_web
sudo cp -a /etc/nexus/local-agent.env.pre-gce-split /etc/nexus/local-agent.env
git switch --detach baae25a
sudo supervisorctl start ssw_web
sudo supervisorctl status ssw_web
```

O rollback do GCE preserva os pareamentos e o SQLite da fila. Mantenha
`/var/lib/nexus/local-agent.sqlite3` para auditoria.

## 6. Homologação sem escrita SSW

Use duas identidades e dois computadores/agentes. Confirme: códigos diferentes e de uso
único; login de cada usuário abre Chromium apenas no seu computador; tentativa de token
ou conclusão cruzada retorna rejeição; fechamento do Chromium aparece como
`reauthentication_required`; parada do agente aparece como `offline`; GCE/OP001 criam
jobs com o `target_agent_id` correto; duas tarefas da mesma sessão serializam; sessões
distintas podem progredir em agentes distintos; logout agenda apenas `ssw.session.close`;
revogação rejeita o token. Confirme que o GCE manual consulta OP455/OP930 sem abrir
outro Chromium, mantém a sessão do colaborador aberta e importa somente após
receber os dois relatórios. Teste OP001 NFD e Transporte com handlers simulados.
