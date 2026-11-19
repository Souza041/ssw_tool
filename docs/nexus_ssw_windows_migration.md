# Migração das automações SSW — VPS para Windows

## Arquitetura segura

- A VPS continua sendo a autoridade de agenda, fila, lease, logs e auditoria.
- O Windows apenas faz `claim` HTTPS autenticado e executa handlers compilados.
- `NEXUS_SSW_DELEGATION_ENABLED=false` e
  `NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=false` são os padrões.
- Jobs operacionais usam uma tentativa. Uma perda de lease termina em `FAILED` e
  nunca dispara novamente a mesma operação automaticamente.
- Chaves de idempotência impedem dois disparos do mesmo horário. Chaves de
  concorrência impedem leases simultâneos sem descartar CWB/BIG enfileirados.
- O agente não possui handler de shell nem aceita nome de tarefa vindo do payload.

## Inventário

| Fluxo | Entrada/horário na VPS | Parâmetros | SSW e efeitos | Migração |
|---|---|---|---|---|
| OC73 CWB | APScheduler, dom–sex, 23:55, `America/Sao_Paulo` | fluxo e data de referência; calendário calcula janela | OP455, OP101 e, sob os gates existentes, ocorrência 73; MySQL e Health Center | `ssw.ocorrencia_73`, scheduler permanece na VPS |
| OC73 BIG | APScheduler, seg–qui, 23:55 | fluxo e data de referência | mesmos gates; regras BIG preservadas | mesmo handler, concorrência comum com CWB |
| Rechecagem BIG | APScheduler a cada minuto quando o gate preexistente está ativo | minuto agendado | retoma pendências persistidas; pode consultar/lançar somente sob gates OC73 | `ssw.ocorrencia_73.recheck_big` |
| Documentos GCE | `scripts/processar_documentos_diario.py`, agenda externa não declarada no repositório; também POST `/documentos/executar` | referência, período, modos de notificação | Portal GCE, OP455/OP930, importação, matching, alertas, métricas e notificações | `ssw.documentos_gce`; histórico e Health Center continuam no MySQL central |
| Métricas OP455 | POST `/api/metricas/refresh`; scheduler horário existe no código, mas seu startup está comentado | 1–365 dias; origem manual/scheduler | OP455, `dashboard_runs` e snapshot | `ssw.metricas_op455`; snapshot remoto obrigatoriamente no banco |
| Documentos LG | POST `/documentos-lg/executar` e CLI incremental; sem agenda instalada pelo repositório | períodos, unidade, timeout, planilha | OP435/455/930, arquivos para download e eventualmente planilha | execução direta na VPS é bloqueada quando a delegação está ativa; usar a FastAPI Windows. Não serializar sessão nem artefatos pela fila |
| Operações interativas | POSTs OP455, OP150, OP103, OP488, OP535, débitos/OP475, OP001, OP101 e OP930 | formulários e sessão SSW do usuário | relatórios, downloads e possíveis lançamentos explícitos | não são schedulers; ficam disponíveis apenas na FastAPI Windows. A VPS bloqueia esses POSTs com o gate ativo |
| LG POD 01h | systemd `01:00`, D-1, SC/PR/SP | referência e perfis | portal LG, não SSW | fora do escopo; timer preservado |
| Whirlpool | manual/interface | planilhas e portal corporativo | portal Whirlpool, não SSW | fora do escopo |
| Monitor de dependências | worker opt-in | URL/HEAD/TCP | não autentica nem executa no SSW | permanece na VPS |

Scripts de laboratório, pré-voo, importação histórica e debug não são agendas de
produção e não foram transformados em handlers. Continuam explicitamente manuais.

## Contratos dos handlers

Os payloads rejeitam campos extras. Datas são ISO e limitadas a 2020–2099; OC73
aceita apenas `CWB`/`BIG`; métricas aceitam no máximo 365 dias; Documentos GCE não
aceita caminho de saída remoto e mantém notificações mutuamente exclusivas. Os
timeouts HTTP/relatório continuam sendo os dos serviços existentes. Heartbeats
renovam o lease e transportam cancelamento e progresso.

Os serviços OC73 e Documentos GCE já usam `run_monitored`. Métricas ganhou catálogo
próprio no Health Center. O Windows precisa alcançar o mesmo MySQL da VPS. Para
Métricas, `METRICAS_SNAPSHOT_STORAGE=database` é obrigatório; o handler recusa a
execução antes do login SSW se essa garantia não estiver presente.

## Variáveis

VPS, em `/etc/nexus/local-agent.env`:

```text
NEXUS_LOCAL_AGENT_ENABLED=true
NEXUS_SSW_DELEGATION_ENABLED=false
NEXUS_LOCAL_AGENT_ALLOWED_TASKS=system.ping,ssw.documentos_gce,ssw.metricas_op455,ssw.ocorrencia_73,ssw.ocorrencia_73.recheck_big
NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON={"windows-corporativo-01":["system.ping","ssw.documentos_gce","ssw.metricas_op455","ssw.ocorrencia_73","ssw.ocorrencia_73.recheck_big"]}
```

Windows, em `C:\ProgramData\Nexus\local-agent.env`:

```text
NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=false
NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE=C:\ProgramData\Nexus\ssw-automation.env
```

O segundo arquivo privado contém somente os prefixos permitidos: `SSW_`,
`DOCUMENTOS_`, `GCE_`, `AUTOMATION_DB_`, `HEALTH_`, `METRICAS_`,
`OCORRENCIA_73_`, `OC73_` e `SMTP_`. Copie os valores aprovados; não mova nem
rotacione credenciais como parte desta implantação. Preserve os gates OC73 atuais.

## Ativação controlada

1. Implantar o código com os dois gates novos ainda `false`.
2. Aplicar a migração SQLite automática iniciando apenas a aplicação VPS. Ela só
   adiciona metadados de idempotência/concorrência e preserva jobs existentes.
3. No Windows 64 bits, instalar os requisitos completos do projeto e preparar os
   dois arquivos privados com ACL para Administradores, SYSTEM e LOCAL SERVICE.
4. Manter os handlers SSW falsos e homologar `system.ping --once`.
5. Incluir as quatro tarefas nas allowlists da VPS e do agente, sem alterar ainda
   `NEXUS_SSW_DELEGATION_ENABLED`.
6. No Windows, definir `NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=true`, manter todos
   os gates produtivos preexistentes como estavam e executar diagnóstico/`--once`
   com a fila vazia. Confirmar as capabilities no `/admin/local-agent`.
7. Registrar/iniciar a tarefa persistente Windows somente após a validação anterior.
8. Em janela única, definir `NEXUS_SSW_DELEGATION_ENABLED=true` na VPS e reiniciar
   somente `ssw_web`. Não criar scheduler no Windows.
9. Confirmar que o próximo disparo cria exatamente um job, é leased pelo agente
   autorizado e aparece no histórico do módulo e no Health Center.

Comandos de preparação e ativação estão também em `docs/nexus_local_agent.md`.
Nenhum script desta entrega reinicia serviços ou muda flags automaticamente.

### Comandos de referência

VPS, ainda com a delegação falsa:

```bash
cd /home/automacao/bots/ssw_tool
sudoedit /etc/nexus/local-agent.env
# Acrescente as quatro tarefas às duas allowlists; mantenha:
# NEXUS_SSW_DELEGATION_ENABLED=false

sudo -u automacao env NEXUS_PYTHON_BIN=/home/automacao/venv/bin/python \
  NEXUS_PROJECT_ROOT=/home/automacao/bots/ssw_tool \
  bash deploy/local_agent/diagnose_ubuntu.sh /etc/nexus/local-agent.env
```

Windows, PowerShell elevado, sem iniciar tarefa persistente:

```powershell
Set-Location C:\Nexus\ssw_tool
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt

.\deploy\local_agent\install_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool `
  -Apply

.\deploy\local_agent\diagnose_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool

.\.venv\Scripts\python.exe .\scripts\nexus_local_agent_windows.py `
  --env-file C:\ProgramData\Nexus\local-agent.env `
  --once
```

Depois de validar capabilities e fila vazia, registrar o worker, ainda sem criar
nenhum scheduler de bot no Windows:

```powershell
.\deploy\local_agent\install_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool `
  -Apply `
  -RegisterTask
Start-ScheduledTask -TaskName "Nexus Local Agent"
```

Na janela de virada da VPS:

```bash
sudoedit /etc/nexus/local-agent.env
# NEXUS_SSW_DELEGATION_ENABLED=true
sudo supervisorctl restart ssw_web
```

O agendamento externo de Documentos deve continuar chamando o mesmo script e usuário.
Ele carrega `/etc/nexus/local-agent.env` e, com o gate ativo, apenas enfileira:

```bash
cd /home/automacao/bots/ssw_tool
sudo -u automacao /home/automacao/venv/bin/python \
  scripts/processar_documentos_diario.py
```

## Rollback

1. Definir `NEXUS_SSW_DELEGATION_ENABLED=false` na VPS e reiniciar `ssw_web`.
2. Parar/desabilitar a tarefa `Nexus Local Agent` no Windows.
3. Definir `NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED=false` no arquivo Windows.
4. Cancelar somente jobs ainda `QUEUED`; jobs `LEASED` devem ser conferidos no
   histórico operacional antes de qualquer repetição.
5. Preservar SQLite, MySQL, logs e artefatos para auditoria. Não reativar uma agenda
   equivalente no Windows.

O rollback restaura os caminhos locais antigos, mas eles só devem ser reativados se
o acesso da VPS ao SSW tiver sido restabelecido. Caso contrário, mantenha os
schedulers produtivos desligados para evitar falhas ou lançamentos incertos.

Comandos:

```bash
sudoedit /etc/nexus/local-agent.env
# NEXUS_SSW_DELEGATION_ENABLED=false
sudo supervisorctl restart ssw_web
```

```powershell
Stop-ScheduledTask -TaskName "Nexus Local Agent" -ErrorAction SilentlyContinue
Disable-ScheduledTask -TaskName "Nexus Local Agent"
```

## Riscos e decisões ainda externas ao código

- A agenda externa de Documentos GCE não está versionada; horário, usuário e
  comando precisam ser inventariados na VPS antes da ativação.
- A autenticação web da VPS ainda depende da política existente de sessão SSW. Esta
  entrega não cria login alternativo.
- Downloads/planilhas das rotas interativas não possuem armazenamento compartilhado
  aprovado. Por isso essas rotas devem ser usadas na FastAPI Windows, sem transportar
  cookies, credenciais ou arquivos pela fila.
- A conectividade Windows → MySQL central e a capacidade das colunas JSON precisam
  ser validadas sem dados produtivos antes do primeiro job real.
