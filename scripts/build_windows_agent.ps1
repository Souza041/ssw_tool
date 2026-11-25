[CmdletBinding()]
param(
    [string]$Version = "0.1.0",
    [string]$Python = "py",
    [string]$Iscc = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildVenv = Join-Path $root "build\agent-venv"
$browserRoot = Join-Path $root "build\playwright-browsers"
$pythonExe = Join-Path $buildVenv "Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    & $Python -3 -m venv $buildVenv
}
& $pythonExe -m pip install --disable-pip-version-check -r (Join-Path $root "requirements.txt") -r (Join-Path $root "requirements-agent.txt")
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot
& $pythonExe -m playwright install chromium
& $pythonExe -m PyInstaller --clean --noconfirm (Join-Path $root "deploy\local_agent\NexusAgent.spec") --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build\pyinstaller")
$agentExe = Join-Path $root "dist\NexusAgent.exe"
if (-not (Test-Path $agentExe)) { throw "PyInstaller não gerou NexusAgent.exe." }
if ([string]::IsNullOrWhiteSpace($Iscc)) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($Iscc) -or -not (Test-Path $Iscc)) {
    throw "Inno Setup 6 (ISCC.exe) não encontrado. O NexusAgent.exe foi gerado; instale o Inno Setup e execute novamente."
}
& $Iscc "/DMyAppVersion=$Version" "/DSourceRoot=$root" (Join-Path $root "deploy\local_agent\NexusAgentSetup.iss")
$installer = Join-Path $root "dist\installer\NexusAgentSetup.exe"
if (-not (Test-Path $installer)) { throw "Inno Setup não gerou NexusAgentSetup.exe." }
Get-FileHash -Algorithm SHA256 $agentExe, $installer | ForEach-Object {
    "{0}  {1}" -f $_.Hash.ToLowerInvariant(), (Split-Path $_.Path -Leaf)
} | Set-Content -Encoding ascii (Join-Path $root "dist\installer\SHA256SUMS.txt")
Write-Host "OK: artefatos reproduzíveis em dist\installer. Nenhum segredo foi incorporado."
