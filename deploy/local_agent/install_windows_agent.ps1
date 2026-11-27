[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [Parameter(Mandatory=$true)][string]$ProjectRoot,
    [string]$EnvFile = "$env:ProgramData\Nexus\local-agent.env",
    [string]$TaskName = "Nexus Local Agent",
    [switch]$Apply,
    [switch]$RegisterTask
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$agentScript = Join-Path $root "scripts\nexus_local_agent_windows.py"
$template = Join-Path $root "deploy\local_agent\windows-agent.env.example"
foreach ($required in @($pythonw, $agentScript, $template)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Arquivo obrigatório ausente: $required" }
}
$envDir = Split-Path -Parent $EnvFile
$administrators = (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')).Translate([Security.Principal.NTAccount]).Value
$systemAccount = (New-Object Security.Principal.SecurityIdentifier('S-1-5-18')).Translate([Security.Principal.NTAccount]).Value
$localService = (New-Object Security.Principal.SecurityIdentifier('S-1-5-19')).Translate([Security.Principal.NTAccount]).Value
if ($Apply) {
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "A aplicação idempotente exige PowerShell elevado."
    }
    if (-not (Test-Path -LiteralPath $envDir)) { New-Item -ItemType Directory -Path $envDir | Out-Null }
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        Copy-Item -LiteralPath $template -Destination $EnvFile
        Write-Host "Arquivo privado criado sem token: $EnvFile"
    }
    & icacls.exe $envDir /inheritance:r /grant:r "${administrators}:(OI)(CI)F" "${systemAccount}:(OI)(CI)F" "${localService}:(OI)(CI)RX" | Out-Null
    & icacls.exe $EnvFile /inheritance:r /grant:r "${administrators}:F" "${systemAccount}:F" "${localService}:R" | Out-Null
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) { throw "Arquivo privado ausente: $EnvFile" }
$values = @{}
Get-Content -LiteralPath $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') { $values[$matches[1].Trim()] = $matches[2] }
}
if ($values['NEXUS_LOCAL_AGENT_SERVER_URL'] -notmatch '^https://') { throw "A URL do executor deve usar HTTPS." }
if ([string]::IsNullOrWhiteSpace($values['NEXUS_LOCAL_AGENT_ID'])) { throw "Identificador do agente ausente." }
if (($values['NEXUS_LOCAL_AGENT_TOKEN']).Length -lt 32) { throw "Token do agente ausente ou curto." }
if ($values['NEXUS_LOCAL_AGENT_ALLOW_INSECURE_HTTP'] -ne 'false') { throw "HTTP inseguro deve permanecer desativado." }
$remoteAuthValue = $values['NEXUS_SSW_REMOTE_AUTH_ENABLED']
if (-not [string]::IsNullOrWhiteSpace($remoteAuthValue) -and $remoteAuthValue -notin @('true','false')) {
    throw "NEXUS_SSW_REMOTE_AUTH_ENABLED deve ser true ou false."
}
$sswHandlers = $values['NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED'] -eq 'true'
if ($sswHandlers) {
    $sswTasks = $values['NEXUS_LOCAL_AGENT_SSW_TASKS']
    if ([string]::IsNullOrWhiteSpace($sswTasks)) {
        throw "Handlers SSW exigem allowlist NEXUS_LOCAL_AGENT_SSW_TASKS explícita."
    }
    $knownTasks = @(
        'ssw.documentos_gce',
        'ssw.documentos_gce.collect_ssw',
        'ssw.metricas_op455',
        'ssw.op001.nfd',
        'ssw.op001.transporte',
        'ssw.session.close'
    )
    foreach ($task in $sswTasks.Split(',')) {
        if ($knownTasks -notcontains $task.Trim()) { throw "Tarefa desconhecida na allowlist Windows." }
    }
    $runtimeEnvFile = $values['NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE']
    if ([string]::IsNullOrWhiteSpace($runtimeEnvFile) -or -not (Test-Path -LiteralPath $runtimeEnvFile -PathType Leaf)) {
        throw "Handlers SSW exigem arquivo privado de runtime existente."
    }
    if ($Apply) {
        & icacls.exe $runtimeEnvFile /inheritance:r /grant:r "${administrators}:F" "${systemAccount}:F" "${localService}:R" | Out-Null
    }
}

if ($RegisterTask -and -not $Apply) { throw "-RegisterTask exige -Apply." }
if ($Apply -and $RegisterTask -and $PSCmdlet.ShouldProcess($TaskName, "Registrar tarefa automática")) {
    $arguments = ('"{0}" --env-file "{1}"' -f $agentScript, $EnvFile)
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $localService -LogonType ServiceAccount -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Tarefa registrada, mas não iniciada automaticamente nesta execução."
}
if (-not $RegisterTask) { Write-Host "Tarefa não registrada; use -Apply -RegisterTask somente após homologar --once." }
Write-Host "OK: configuração validada sem exibir credenciais."
