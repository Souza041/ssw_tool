# Ativação segura da delegação SSW — GCE e OP455

## Escopo autorizado

Esta fase permite exclusivamente:

```text
ssw.documentos_gce
ssw.metricas_op455
```

`ssw.ocorrencia_73` e `ssw.ocorrencia_73.recheck_big` são recusadas pela
allowlist VPS, pela API interna de enqueue e pela allowlist Windows. Se a flag global
for ligada, o scheduler e a rechecagem OC73 retornam sem executar localmente.

## Configuração VPS

Em `/etc/nexus/local-agent.env`:

```text
NEXUS_LOCAL_AGENT_ENABLED=true
NEXUS_SSW_DELEGATION_ENABLED=false
NEXUS_SSW_DELEGATED_TASKS=ssw.documentos_gce,ssw.metricas_op455
NEXUS_LOCAL_AGENT_ALLOWED_TASKS=system.ping,ssw.documentos_gce,ssw.metricas_op455
NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON={"windows-corporativo-01":["system.ping","ssw.documentos_gce","ssw.metricas_op455"]}
```

Em caso de tarefa desconhecida na allowlist de delegação, o processo falha fechado e
nenhum job é criado.

## Configuração Windows

Em `C:\ProgramData\Nexus\local-agent.env`:

```text
NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=true
NEXUS_LOCAL_AGENT_SSW_TASKS=ssw.documentos_gce,ssw.metricas_op455
NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE=C:\ProgramData\Nexus\ssw-automation.env
```

O instalador recusa OC73 ou qualquer tarefa fora da fase atual.

## Pré-ativação

Na VPS:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao env \
  NEXUS_PYTHON_BIN=/home/automacao/venv/bin/python \
  NEXUS_PROJECT_ROOT=/home/automacao/bots/ssw_tool \
  bash deploy/local_agent/diagnose_ubuntu.sh /etc/nexus/local-agent.env
```

No Windows:

```powershell
Set-Location C:\Nexus\ssw_tool_novo
.\deploy\local_agent\install_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo `
  -Apply
.\deploy\local_agent\diagnose_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo
```

Confirmar no painel administrativo que as capabilities são apenas `system.ping`,
`ssw.documentos_gce` e `ssw.metricas_op455`.

Na VPS, confirmar também que o programa existente carrega o ambiente privado pelo
bootstrap explícito, sem substituir o bloco Supervisor nem as variáveis do Health
Center:

```bash
sudo supervisorctl status ssw_web
ssw_web_pid="$(sudo supervisorctl pid ssw_web)"
ps -o user=,pid=,args= -p "$ssw_web_pid"
# O command do programa deve continuar equivalente a:
# /home/automacao/venv/bin/python /home/automacao/bots/ssw_tool/scripts/run_nexus_server.py \
#   --env-file /etc/nexus/local-agent.env --host 127.0.0.1 --port 8000 --workers 1
```

## Ativação

Em janela aprovada:

```bash
sudoedit /etc/nexus/local-agent.env
# Confirmar:
# NEXUS_SSW_DELEGATED_TASKS=ssw.documentos_gce,ssw.metricas_op455
# Alterar somente:
# NEXUS_SSW_DELEGATION_ENABLED=true
sudo supervisorctl restart ssw_web
```

O scheduler externo de Documentos continua chamando o mesmo comando. Ele passa a
somente enfileirar, mantendo datas, sobreposição e demais parâmetros:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao /home/automacao/venv/bin/python \
  scripts/processar_documentos_diario.py
```

O endpoint manual `/documentos/executar` envia explicitamente:

```json
{"origin":"MANUAL","send_notifications":false,"preview_notifications":false}
```

O Windows reutiliza `execute_daily_documents()`. Pipeline run, importações, matching,
alertas e snapshots continuam no banco central; o resultado resumido retorna à fila.
O wrapper existente mantém a execução no Health Center. Nenhuma regra de negócio foi
duplicada.

Antes de registrar a tarefa persistente, enfileire **uma** execução manual aprovada
pelo painel e faça um único polling no Windows:

```powershell
Set-Location C:\Nexus\ssw_tool_novo
.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_windows.py `
  --env-file C:\ProgramData\Nexus\local-agent.env `
  --once
```

Confira no painel o término, o `pipeline_run_id`, os logs sanitizados e o evento no
Health Center. Somente depois da homologação, registre e inicie a persistência:

```powershell
.\deploy\local_agent\install_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo `
  -Apply `
  -RegisterTask
Start-ScheduledTask -TaskName "Nexus Local Agent"
```

Para OP455, use o refresh já existente no dashboard. Não use o utilitário isolado de
homologação com a delegação global ligada; ele foi projetado para a fase anterior,
com `NEXUS_SSW_DELEGATION_ENABLED=false`.

## Rollback

Interromper novos enqueues primeiro:

```bash
sudoedit /etc/nexus/local-agent.env
# NEXUS_SSW_DELEGATION_ENABLED=false
sudo supervisorctl restart ssw_web
```

No Windows:

```powershell
Stop-ScheduledTask -TaskName "Nexus Local Agent" -ErrorAction SilentlyContinue
```

Conferir jobs `LEASED` antes de repetir qualquer execução. Jobs `QUEUED` podem ser
cancelados pela interface administrativa. Preservar SQLite, logs e históricos.

Com a flag global falsa, os entrypoints mantêm o caminho local anterior. Como a VPS
continua bloqueada pelo SSW, não reative os schedulers produtivos localmente até
restabelecer o acesso.

