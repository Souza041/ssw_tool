[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ProjectRoot,
    [string]$EnvFile = "$env:ProgramData\Nexus\local-agent.env",
    [string]$TaskName = "Nexus Local Agent"
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
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
& $python (Join-Path $root "scripts\nexus_local_agent_diagnose.py") --role executor --env-file $EnvFile
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) { Write-Host "PENDENTE: tarefa automática não registrada."; exit 1 }
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host ("Tarefa: {0}; Estado: {1}; Último resultado: {2}" -f $TaskName, $task.State, $info.LastTaskResult)
Write-Host "Diagnóstico somente leitura concluído; nenhum token foi exibido."
