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

O instalador não contém tokens, senhas, certificados privados nem credenciais de proxy.
`requests` usa o proxy configurado para o usuário Windows e o Chromium usa `PROXY_*`
quando definidos no ambiente privado opcional. Proxy com autenticação integrada pode ser
configurado pelo Windows; credenciais compartilhadas não devem ser colocadas no setup.

## 1. Gerar e assinar o instalador

Em uma máquina Windows de build com Python 3.11+ e Inno Setup 6:

```powershell
Set-Location C:\fontes\ssw_tool_multiuser
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_agent.ps1 -Version 0.1.0
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

Os comandos abaixo preservam `/etc/nexus`, `/var/lib/nexus`, HTTPS/Nginx e o supervisor
existentes. Execute somente após a equipe registrar o commit homologado desta branch:

```bash
cd /home/automacao/bots/ssw_tool
git fetch origin feat/nexus-agent-multiuser
git switch feat/nexus-agent-multiuser
git pull --ff-only origin feat/nexus-agent-multiuser
/home/automacao/venv/bin/python -m pip install -r requirements.txt
sudo NEXUS_PROJECT_ROOT=/home/automacao/bots/ssw_tool NEXUS_PYTHON_BIN=/home/automacao/venv/bin/python bash deploy/local_agent/install_ubuntu.sh --apply
sudoedit /etc/nexus/local-agent.env
```

Mantenha os tokens e o agente central existentes e configure:

```dotenv
NEXUS_LOCAL_AGENT_ENABLED=true
NEXUS_LOCAL_AGENT_OFFLINE_SECONDS=30
NEXUS_LOCAL_AGENT_PAIRING_ENABLED=true
NEXUS_LOCAL_AGENT_PAIRING_TTL_SECONDS=600
NEXUS_LOCAL_AGENT_PAIRED_TASKS=system.ping,ssw.documentos_gce,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_SSW_REMOTE_AUTH_ENABLED=true
NEXUS_SSW_REMOTE_AUTH_AGENT_ID=windows-corporativo-01
NEXUS_SSW_REMOTE_AUTH_BINDINGS_JSON={"rod/usuario.admin":"windows-corporativo-01"}
NEXUS_SSW_DELEGATION_ENABLED=true
NEXUS_SSW_DELEGATED_TASKS=ssw.documentos_gce,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_LOCAL_AGENT_ALLOWED_TASKS=system.ping,ssw.documentos_gce,ssw.op001.nfd,ssw.op001.transporte,ssw.session.close
NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON={"windows-corporativo-01":["system.ping","ssw.documentos_gce","ssw.op001.nfd","ssw.op001.transporte","ssw.session.close"]}
NEXUS_LOCAL_AGENT_MAX_PAYLOAD_BYTES=12582912
NEXUS_LOCAL_AGENT_MAX_RESULT_BYTES=12582912
```

Valide e reinicie apenas o processo existente:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao env NEXUS_PYTHON_BIN=/home/automacao/venv/bin/python NEXUS_PROJECT_ROOT=/home/automacao/bots/ssw_tool bash deploy/local_agent/diagnose_ubuntu.sh /etc/nexus/local-agent.env
sudo nginx -t
sudo supervisorctl restart ssw_web
sudo supervisorctl status ssw_web
```

## 3. Autorizar e instalar cada computador

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

Para Documentos GCE, a infraestrutura homologada ainda exige as configurações privadas
do Portal GCE e do banco. Elas não podem ser incluídas no instalador. Se já autorizadas
para o colaborador, grave-as por canal administrativo em
`%LOCALAPPDATA%\Nexus\Agent\runtime.env`, com as mesmas chaves do
`windows-ssw-runtime.env.example`. OP001 e autenticação humana não exigem esse arquivo.

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

Rollback funcional imediato, preservando dados para auditoria:

```bash
sudo cp -a /etc/nexus/local-agent.env /etc/nexus/local-agent.env.multiuser.rollback
sudoedit /etc/nexus/local-agent.env
# alterar somente:
# NEXUS_LOCAL_AGENT_PAIRING_ENABLED=false
# NEXUS_SSW_DELEGATION_ENABLED=false
# manter NEXUS_SSW_REMOTE_AUTH_AGENT_ID=windows-corporativo-01 para o fluxo administrativo
sudo supervisorctl restart ssw_web
sudo supervisorctl status ssw_web
```

Rollback de código para o homologado anterior:

```bash
cd /home/automacao/bots/ssw_tool
git switch --detach 72c1ffc
sudo supervisorctl restart ssw_web
sudo supervisorctl status ssw_web
```

Nos computadores, revogue os agentes e desinstale pelo Windows. Não apague
`/var/lib/nexus/local-agent.sqlite3`: ele contém fila e auditoria, não cookies ou senhas.

## 6. Homologação sem escrita SSW

Use duas identidades e dois computadores/agentes. Confirme: códigos diferentes e de uso
único; login de cada usuário abre Chromium apenas no seu computador; tentativa de token
ou conclusão cruzada retorna rejeição; fechamento do Chromium aparece como
`reauthentication_required`; parada do agente aparece como `offline`; GCE/OP001 criam
jobs com o `target_agent_id` correto; duas tarefas da mesma sessão serializam; sessões
distintas podem progredir em agentes distintos; logout agenda apenas `ssw.session.close`;
revogação rejeita o token. Na homologação de OP001, apenas abra/consulte e não confirme
ações que gravem no SSW.
