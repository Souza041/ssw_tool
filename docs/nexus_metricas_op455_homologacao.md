# Homologação isolada — `ssw.metricas_op455`

## Fluxo rastreado

1. A VPS mantém `NEXUS_SSW_DELEGATION_ENABLED=false`.
2. O CLI administrativo local grava diretamente no SQLite da fila, usando o arquivo
   privado `/etc/nexus/local-agent.env` e o usuário Unix autorizado.
3. O job possui `mode=validate_only`, `origin=homologation`, `correlation_id`, uma
   tentativa, idempotência persistente e concorrência `ssw.metricas_op455`.
4. O agente HTTPS autentica, faz claim somente se sua allowlist contiver exatamente
   `ssw.metricas_op455` e renova o lease por heartbeat.
5. `ssw_tasks._metrics` chama `MetricasService.homologar_op455`.
6. O serviço reutiliza login, menu, `OP455Report.gerar_e_baixar` e
   `processar_op455_snapshot`. O arquivo fica em diretório temporário e é apagado ao
   final.
7. Resultado, progresso e logs sanitizados retornam ao SQLite da VPS. Nenhuma tabela
   de Métricas ou Health Center é escrita nesse modo.

## Leitura e escrita

| Operação | Natureza |
|---|---|
| Login e abertura do menu SSW | autenticação/leitura de sessão |
| Solicitação e download OP455 | relatório somente leitura do negócio; o SSW pode criar job temporário interno |
| Parser `processar_op455_snapshot` | memória/CPU local |
| Arquivo `.sswweb` | temporário no Windows, removido ao concluir |
| SQLite Local Agent | escreve job, lease, heartbeat, logs, resultado e auditoria técnica |
| `dashboard_runs`, `dashboard_snapshots`, Health Center | nenhuma escrita em `validate_only` |
| Lançamentos OP101/OC73 | inexistentes neste handler |

O modo produtivo do mesmo handler continua chamando `atualizar_op455`, que cria
`dashboard_runs`, grava snapshot central, finaliza sucesso/erro e registra Health
Center quando habilitado. Ele não é usado pelo procedimento abaixo.

## Variáveis Windows

`C:\ProgramData\Nexus\local-agent.env`:

```text
NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=true
NEXUS_LOCAL_AGENT_SSW_TASKS=ssw.metricas_op455
NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE=C:\ProgramData\Nexus\ssw-automation.env
```

`C:\ProgramData\Nexus\ssw-automation.env` precisa conter:

```text
METRICAS_SSW_DOMINIO=
METRICAS_SSW_CPF=
METRICAS_SSW_USUARIO=
METRICAS_SSW_SENHA=
METRICAS_SSW_UNIDADE=MTZ
METRICAS_DB_HOST=
METRICAS_DB_PORT=3306
METRICAS_DB_USER=
METRICAS_DB_PASSWORD=
METRICAS_DB_NAME=
METRICAS_SNAPSHOT_STORAGE=database
HEALTH_CENTER_ENABLED=false
```

Não copie variáveis OC73/Documentos para habilitá-las. O preflight verifica Python
64 bits, dependências e configuração sem imprimir valores.

## Preparação Windows sem SSW

```powershell
Set-Location C:\Nexus\ssw_tool_novo
Stop-ScheduledTask -TaskName "Nexus Local Agent" -ErrorAction SilentlyContinue

.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_metrics_preflight.py `
  --agent-env C:\ProgramData\Nexus\local-agent.env
```

Consulta MySQL opcional e somente leitura, em janela aprovada:

```powershell
.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_metrics_preflight.py `
  --agent-env C:\ProgramData\Nexus\local-agent.env `
  --check-mysql
```

Para confirmar a capability sem consumir job, mantenha a fila vazia e execute uma
vez:

```powershell
.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_windows.py `
  --env-file C:\ProgramData\Nexus\local-agent.env `
  --once
```

No painel administrativo da VPS, as capabilities do agente devem mostrar
`system.ping` e `ssw.metricas_op455`, nunca OC73 ou Documentos.

## Enfileiramento controlado na VPS

Editar `/etc/nexus/local-agent.env` e habilitar somente o gate temporário:

```text
NEXUS_SSW_DELEGATION_ENABLED=false
NEXUS_LOCAL_AGENT_METRICS_HOMOLOGATION_ENABLED=true
```

Reiniciar `ssw_web` não é necessário para o CLI, pois ele lê o arquivo diretamente.
Use uma correlação nova por tentativa:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao /home/automacao/venv/bin/python \
  scripts/nexus_local_agent_metrics_homologation.py \
  --env-file /etc/nexus/local-agent.env \
  enqueue --days 1 --correlation-id HML-OP455-AAAA-MM-DD-01
```

No Windows, consumir exatamente um job:

```powershell
Set-Location C:\Nexus\ssw_tool_novo
.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_windows.py `
  --env-file C:\ProgramData\Nexus\local-agent.env `
  --once
```

Consultar na VPS:

```bash
sudo -u automacao /home/automacao/venv/bin/python \
  /home/automacao/bots/ssw_tool/scripts/nexus_local_agent_metrics_homologation.py \
  --env-file /etc/nexus/local-agent.env \
  status JOB_ID
```

Resultado esperado: `SUCCEEDED`, progresso `3/3`, `mode=validate_only`, total de
registros, tamanho e SHA-256; `database_writes=0`. Repetir o mesmo correlation ID
retorna o mesmo job e não cria nova solicitação.

## Encerramento/rollback

Imediatamente após consultar o resultado:

```bash
sudoedit /etc/nexus/local-agent.env
# NEXUS_LOCAL_AGENT_METRICS_HOMOLOGATION_ENABLED=false
# NEXUS_SSW_DELEGATION_ENABLED=false
```

No Windows, para voltar ao perfil ping-only:

```text
NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=false
NEXUS_LOCAL_AGENT_SSW_TASKS=
```

Depois de salvar o arquivo, valide novamente e retome apenas o polling padrão:

```powershell
.\deploy\local_agent\install_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo `
  -Apply
Start-ScheduledTask -TaskName "Nexus Local Agent"
```

Não habilitar scheduler de Métricas, não alterar OC73 e não usar o endpoint manual
produtivo `/api/metricas/refresh` durante esta homologação.

