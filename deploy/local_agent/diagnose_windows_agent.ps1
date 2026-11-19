[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ProjectRoot,
    [string]$EnvFile = "$env:ProgramData\Nexus\local-agent.env",
    [string]$TaskName = "Nexus Local Agent"
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$agentScript = Join-Path $root "scripts\nexus_local_agent_windows.py"
$envDir = Split-Path -Parent $EnvFile
$logFile = Join-Path $envDir "logs\local-agent.log"
$lockFile = Join-Path $envDir "runtime\local-agent.lock"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python do venv não encontrado." }
$acl = Get-Acl -LiteralPath $EnvFile
if (-not $acl.AreAccessRulesProtected) { throw "ACL do arquivo privado ainda herda permissões." }
$unsafeSids = @('S-1-1-0','S-1-5-11','S-1-5-32-545')
foreach ($entry in $acl.Access) {
    $sid = $entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($entry.AccessControlType -eq 'Allow' -and $unsafeSids -contains $sid) {
        throw "ACL insegura detectada no arquivo privado."
    }
}
foreach ($writableDir in @((Split-Path -Parent $logFile), (Split-Path -Parent $lockFile))) {
    if (-not (Test-Path -LiteralPath $writableDir -PathType Container)) {
        throw "Diretório operacional ausente: $writableDir"
    }
    $writableAcl = Get-Acl -LiteralPath $writableDir
    if (-not $writableAcl.AreAccessRulesProtected) { throw "ACL do diretório operacional ainda herda permissões." }
    $localServiceCanWrite = $false
    foreach ($entry in $writableAcl.Access) {
        $sid = $entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if (
            $sid -eq 'S-1-5-19' -and
            $entry.AccessControlType -eq 'Allow' -and
            [string]$entry.FileSystemRights -match 'Modify|FullControl|Write'
        ) {
            $localServiceCanWrite = $true
        }
    }
    if (-not $localServiceCanWrite) { throw "LocalService não possui escrita no diretório operacional." }
}
$values = @{}
Get-Content -LiteralPath $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') { $values[$matches[1].Trim()] = $matches[2] }
}
if ($values['NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED'] -eq 'true') {
    $runtimeEnv = $values['NEXUS_LOCAL_AGENT_RUNTIME_ENV_FILE']
    if ([string]::IsNullOrWhiteSpace($runtimeEnv) -or -not (Test-Path -LiteralPath $runtimeEnv -PathType Leaf)) {
        throw "Arquivo privado de runtime SSW ausente."
    }
    $runtimeAcl = Get-Acl -LiteralPath $runtimeEnv
    if (-not $runtimeAcl.AreAccessRulesProtected) { throw "ACL do runtime SSW ainda herda permissões." }
    foreach ($entry in $runtimeAcl.Access) {
        $sid = $entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($entry.AccessControlType -eq 'Allow' -and $unsafeSids -contains $sid) {
            throw "ACL insegura detectada no runtime SSW."
        }
    }
}
& $python (Join-Path $root "scripts\nexus_local_agent_diagnose.py") --role executor --env-file $EnvFile
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "AVISO: tarefa automática ainda não registrada; estado esperado antes da homologação --once."
    Write-Host "Diagnóstico somente leitura concluído; nenhum token foi exibido."
    exit 0
}
$info = Get-ScheduledTaskInfo -TaskName $TaskName
$problems = [System.Collections.Generic.List[string]]::new()
$action = @($task.Actions)[0]
if ($action.Execute -ne $python) { $problems.Add("Executável divergente; esperado python.exe do venv.") }
if ($action.WorkingDirectory -ne $root) { $problems.Add("Diretório de trabalho divergente.") }
foreach ($expectedArgument in @($agentScript, $EnvFile, $logFile, $lockFile)) {
    if ($action.Arguments -notlike "*$expectedArgument*") { $problems.Add("Argumento obrigatório ausente na ação agendada.") }
}
try {
    $principalSid = (New-Object Security.Principal.NTAccount($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
} catch {
    $principalSid = $task.Principal.UserId
}
if ($principalSid -ne 'S-1-5-19') { $problems.Add("Principal divergente; esperado LocalService.") }
if ([string]$task.Settings.MultipleInstances -ne 'IgnoreNew') { $problems.Add("Política de múltiplas instâncias não é IgnoreNew.") }
if ($task.Settings.RestartCount -lt 1) { $problems.Add("Reinício automático não configurado.") }
$executionLimit = [string]$task.Settings.ExecutionTimeLimit
if ($executionLimit -notin @('PT0S','00:00:00')) { $problems.Add("ExecutionTimeLimit deve ser ilimitado (PT0S).") }
if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Host "ERRO: $_" }
    throw "Definição da tarefa agendada incompatível; reaplique o instalador."
}
$resultDescription = if ($info.LastTaskResult -eq 267009) {
    "0x41301 (processo ainda em execução; validar atividade pelo log)"
} elseif ($info.LastTaskResult -eq 0) {
    "0 (última execução concluída sem erro)"
} else {
    ("{0} (0x{1:X})" -f $info.LastTaskResult, $info.LastTaskResult)
}
Write-Host ("Tarefa: {0}; Estado: {1}; Último resultado: {2}" -f $TaskName, $task.State, $resultDescription)
if (Test-Path -LiteralPath $logFile -PathType Leaf) {
    $log = Get-Item -LiteralPath $logFile
    Write-Host ("Log operacional: {0}; bytes={1}; atualizado={2:O}" -f $log.FullName, $log.Length, $log.LastWriteTime)
    if ($task.State -eq 'Running' -and $log.LastWriteTime -lt (Get-Date).AddMinutes(-10)) {
        Write-Host "AVISO: tarefa Running sem atualização recente no log; valide conectividade e processo."
    }
} else {
    Write-Host "AVISO: log operacional ainda não existe; a tarefa pode não ter iniciado o launcher atualizado."
}
Write-Host "Diagnóstico somente leitura concluído; nenhum token foi exibido."
