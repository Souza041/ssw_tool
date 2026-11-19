# Autenticação SSW remota pelo Nexus Local Agent

## Arquitetura e limites

Quando `NEXUS_SSW_REMOTE_AUTH_ENABLED=true`, o POST de login atual não abre conexão
SSW na VPS. A solicitação fica exclusivamente na memória do processo FastAPI por no
máximo 30 segundos. O Windows inicia o polling HTTPS e somente o agente indicado em
`NEXUS_SSW_REMOTE_AUTH_AGENT_ID` pode reivindicá-la.

As credenciais não usam a fila de jobs e nunca são gravadas em SQLite, MySQL,
histórico, Health Center ou logs. Após o claim, a VPS descarta sua referência à senha;
o executor limpa o payload e fecha a sessão SSW ao concluir. A VPS recebe apenas a
decisão e uma prova HMAC vinculada ao identificador imprevisível, desafio, fingerprint
da identidade, resultado e agente autorizado. A prova e o lease são de consumo único.

O canal pressupõe o HTTPS público já existente, com certificado válido. Não habilite
esta função por HTTP, não registre corpos de requisição/resposta no Nginx e não coloque
tokens em parâmetros de linha de comando. A VPS continua criando a sessão web e
aplicando `NEXUS_ADMIN_USERS` e as permissões de observabilidade existentes.

O broker em memória exige exatamente um worker FastAPI. O bootstrap do servidor recusa
a ativação com `--workers` diferente de `1`; preserve o processo `ssw_web` atual.

Uma sessão autenticada remotamente guarda somente domínio, CPF, usuário e unidade em
memória, sem senha ou cookie SSW. Operações SSW diretas nessa sessão falham de forma
segura; GCE e OP455 continuam usando seus fluxos próprios de delegação. OC73 permanece
fora das allowlists e desativada.

## Configuração VPS

Em `/etc/nexus/local-agent.env`, inicialmente com a função desligada:

```text
NEXUS_LOCAL_AGENT_ENABLED=true
NEXUS_SSW_REMOTE_AUTH_ENABLED=false
NEXUS_SSW_REMOTE_AUTH_AGENT_ID=windows-corporativo-01
NEXUS_SSW_REMOTE_AUTH_TIMEOUT_SECONDS=20
NEXUS_SSW_REMOTE_AUTH_REQUEST_TTL_SECONDS=30
NEXUS_SSW_REMOTE_AUTH_PER_MINUTE=5
NEXUS_SSW_REMOTE_AUTH_MAX_PENDING=50
NEXUS_SSW_DELEGATION_ENABLED=false
```

O agente informado deve existir em `NEXUS_LOCAL_AGENT_TOKENS_JSON`. Não altere o token
durante a ativação. Mantenha o arquivo como `automacao:automacao` e modo `0600`.

## Configuração Windows

Em `C:\ProgramData\Nexus\local-agent.env`:

```text
NEXUS_LOCAL_AGENT_SERVER_URL=https://ENDERECO_PUBLICO_DO_NEXUS
NEXUS_LOCAL_AGENT_ID=windows-corporativo-01
NEXUS_LOCAL_AGENT_TOKEN=VALOR_PRIVADO_JA_PROVISIONADO
NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP=false
NEXUS_SSW_REMOTE_AUTH_ENABLED=true
NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=false
NEXUS_LOCAL_AGENT_SSW_TASKS=
```

Para a recuperação inicial do login, os handlers de GCE/OP455 podem permanecer
desligados. O instalador restringe a ACL e a tarefa agendada sempre inicia o executor
com `--env-file C:\ProgramData\Nexus\local-agent.env`.

## Validação antes da ativação

VPS:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao env \
  NEXUS_PYTHON_BIN=/home/automacao/venv/bin/python \
  NEXUS_PROJECT_ROOT=/home/automacao/bots/ssw_tool \
  bash deploy/local_agent/diagnose_ubuntu.sh /etc/nexus/local-agent.env
sudo supervisorctl status ssw_web
sudo nginx -t
```

Windows, em PowerShell elevado:

```powershell
Set-Location C:\Nexus\ssw_tool_novo
.\deploy\local_agent\install_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo `
  -EnvFile C:\ProgramData\Nexus\local-agent.env `
  -Apply
.\deploy\local_agent\diagnose_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo `
  -EnvFile C:\ProgramData\Nexus\local-agent.env
```

## Homologação real controlada

1. Confirme que `NEXUS_SSW_DELEGATION_ENABLED=false` e que OC73 continua suspensa.
2. Altere somente `NEXUS_SSW_REMOTE_AUTH_ENABLED=true` na VPS e reinicie o programa
   existente:

   ```bash
   sudoedit /etc/nexus/local-agent.env
   sudo supervisorctl restart ssw_web
   sudo supervisorctl status ssw_web
   ```

3. Abra a página normal `/login` via HTTPS, preencha uma conta de homologação
   autorizada e envie o formulário. Enquanto o navegador aguarda, execute no Windows:

   ```powershell
   Set-Location C:\Nexus\ssw_tool_novo
   .\.venv\Scripts\python.exe .\scripts\nexus_local_agent_windows.py `
     --env-file C:\ProgramData\Nexus\local-agent.env `
     --once
   ```

4. O polling deve ocorrer dentro da janela de 20 segundos. Se houver timeout, recarregue
   o formulário e repita todo o login; nunca copie a senha para o terminal.
5. Confirme abertura do dashboard, permissões administrativas esperadas e ausência de
   tentativa a `ssw0422` nos logs de saída da VPS. Os logs não devem conter CPF, senha,
   cookie ou corpo HTTP.
6. Depois do aceite, registre a persistência sem alterar os handlers SSW:

   ```powershell
   .\deploy\local_agent\install_windows_agent.ps1 `
     -ProjectRoot C:\Nexus\ssw_tool_novo `
     -EnvFile C:\ProgramData\Nexus\local-agent.env `
     -Apply `
     -RegisterTask
   Start-ScheduledTask -TaskName "Nexus Local Agent"
   ```

Falha, timeout, agente ausente, prova inválida ou replay sempre mantêm o usuário fora
da sessão. Não existe fallback para login SSW direto quando a flag remota está ativa.

## Rollback

Primeiro restaure o comportamento do servidor:

```bash
sudoedit /etc/nexus/local-agent.env
# NEXUS_SSW_REMOTE_AUTH_ENABLED=false
sudo supervisorctl restart ssw_web
sudo supervisorctl status ssw_web
```

Depois, se o agente não for necessário para outras funções:

```powershell
Stop-ScheduledTask -TaskName "Nexus Local Agent" -ErrorAction SilentlyContinue
# Em C:\ProgramData\Nexus\local-agent.env:
# NEXUS_SSW_REMOTE_AUTH_ENABLED=false
```

O rollback não remove SQLite, histórico, tokens, sessões existentes ou arquivos de
ambiente. Como a VPS continua bloqueada pelo SSW, o login local restaurado somente
voltará a funcionar quando o acesso de rede direto também estiver disponível.

