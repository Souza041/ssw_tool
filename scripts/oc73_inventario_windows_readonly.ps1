[CmdletBinding()]
param(
    [switch]$Explain,
    [switch]$PlanOnly,
    [switch]$Collect,
    [string]$AuthorizationReference,
    [switch]$WriteJson,
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Contract = [ordered]@{
    host = '127.0.0.1'
    port = 3308
    schema = 'oc73_autorizacao_transacional_test'
    user = 'oc73_tester'
    product = 'MySQL Community'
    version = '5.7.32'
}

$Sources = @(
    'CIM Win32_OperatingSystem (versão local)',
    'CIM Win32_Service (serviços registrados, estado e PID)',
    'CIM Win32_Process (PID, executável e resumo sanitizado)',
    'Get-NetTCPConnection (listeners TCP locais)',
    'Get-AuthenticodeSignature (assinatura do executável)',
    'Get-FileHash SHA256 (integridade do executável)',
    'Get-Volume (volume associado ao datadir)',
    'Get-SmbShare (possível compartilhamento local)'
)

function New-Evidence {
    param([string]$Status, [hashtable]$Values)
    $result = [ordered]@{ status = $Status }
    foreach ($key in $Values.Keys) { $result[$key] = $Values[$key] }
    return $result
}

function Get-ExecutableFromCommandLine {
    param([AllowNull()][string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $null }
    $match = [regex]::Match($CommandLine, '^\s*(?:"(?<exe>[^"]+)"|(?<exe>\S+))')
    if (-not $match.Success) { return $null }
    return $match.Groups['exe'].Value
}

function Test-ObservedLocalPathSyntax {
    param([AllowNull()][string]$LiteralPath)
    if (
        [string]::IsNullOrWhiteSpace($LiteralPath) -or
        $LiteralPath.Length -gt 512 -or
        $LiteralPath -match '[\x00-\x1f\x7f]' -or
        $LiteralPath -match '^(?:\\\\|//|\\[?.]\\|\\\?\?\\)' -or
        $LiteralPath -notmatch '^[C-Zc-z]:\\'
    ) {
        return $false
    }
    foreach ($component in @($LiteralPath.Substring(3) -split '\\')) {
        $baseName = ($component -split '\.', 2)[0]
        if (
            [string]::IsNullOrWhiteSpace($component) -or
            $component -in @('.', '..') -or
            $component -match '[/:*?"<>|]' -or
            $component -match '[ .]$' -or
            $baseName -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$'
        ) {
            return $false
        }
    }
    return $true
}

function Get-SanitizedCommandSummary {
    param([AllowNull()][string]$CommandLine)
    $flags = [System.Collections.Generic.List[string]]::new()
    $sensitive = $false
    if (-not [string]::IsNullOrWhiteSpace($CommandLine)) {
        $sensitive = [regex]::IsMatch(
            $CommandLine,
            '(?i)--?(password|passwd|pwd|token|secret)(?:\s|=)'
        )
        $portMatch = [regex]::Match(
            $CommandLine,
            '(?i)(?:^|\s)--port(?:=|\s+)(?<value>\d{1,5})(?:\s|$)'
        )
        if ($portMatch.Success) {
            $flags.Add(('port:{0}' -f $portMatch.Groups['value'].Value))
        }
        foreach ($name in @('server-id', 'relay-log', 'log-bin')) {
            $match = [regex]::Match(
                $CommandLine,
                ('(?i)(?:^|\s)--{0}(?:=|\s+)' -f [regex]::Escape($name))
            )
            if ($match.Success) {
                $flags.Add(('{0}:PRESENT' -f $name))
            }
        }
        foreach ($name in @('defaults-file', 'defaults-extra-file')) {
            if ([regex]::IsMatch($CommandLine, ('(?i)(?:^|\s)--{0}(?:=|\s+)' -f $name))) {
                $flags.Add(('{0}:PRESENT' -f $name))
            }
        }
    }
    return [ordered]@{
        sensitive_arguments_redacted = $true
        sensitive_arguments_detected = $sensitive
        observed_flags = @($flags)
    }
}

function Get-DatadirFromCommandLine {
    param([AllowNull()][string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $null }
    $match = [regex]::Match(
        $CommandLine,
        '(?i)(?:^|\s)--datadir(?:=|\s+)(?:"(?<path>[^"]+)"|(?<path>\S+))'
    )
    if (-not $match.Success) { return $null }
    return $match.Groups['path'].Value
}

function ConvertTo-UtcCreationTimestamp {
    param([AllowNull()][object]$CreationDate)
    if ($null -eq $CreationDate) { return $null }
    try {
        $value = $null
        if ($CreationDate -is [DateTimeOffset]) {
            $value = [DateTimeOffset]$CreationDate
        } elseif ($CreationDate -is [DateTime]) {
            $dateTime = [DateTime]$CreationDate
            if ($dateTime.Kind -eq [DateTimeKind]::Utc) {
                $value = [DateTimeOffset]::new($dateTime)
            } elseif ($dateTime.Kind -eq [DateTimeKind]::Local) {
                $value = [DateTimeOffset]::new($dateTime)
            } else {
                $utc = [TimeZoneInfo]::ConvertTimeToUtc(
                    [DateTime]::SpecifyKind($dateTime, [DateTimeKind]::Unspecified),
                    [TimeZoneInfo]::Local
                )
                $value = [DateTimeOffset]::new($utc)
            }
        } elseif ($CreationDate -is [string]) {
            $text = ([string]$CreationDate).Trim()
            $dmtf = [regex]::Match(
                $text,
                '^(?<year>\d{4})(?<month>\d{2})(?<day>\d{2})(?<hour>\d{2})(?<minute>\d{2})(?<second>\d{2})\.(?<microsecond>\d{6})(?<sign>[+-])(?<offset>\d{3})$'
            )
            if ($dmtf.Success) {
                $offsetMinutes = [int]$dmtf.Groups['offset'].Value
                if ($offsetMinutes -gt 840) { return $null }
                if ($dmtf.Groups['sign'].Value -eq '-') {
                    $offsetMinutes = -$offsetMinutes
                }
                $dateTime = [DateTime]::new(
                    [int]$dmtf.Groups['year'].Value,
                    [int]$dmtf.Groups['month'].Value,
                    [int]$dmtf.Groups['day'].Value,
                    [int]$dmtf.Groups['hour'].Value,
                    [int]$dmtf.Groups['minute'].Value,
                    [int]$dmtf.Groups['second'].Value,
                    [DateTimeKind]::Unspecified
                ).AddTicks(([int64]$dmtf.Groups['microsecond'].Value) * 10)
                $value = [DateTimeOffset]::new(
                    $dateTime, [TimeSpan]::FromMinutes($offsetMinutes)
                )
            } else {
                $parsed = [DateTimeOffset]::MinValue
                $valid = [DateTimeOffset]::TryParseExact(
                    $text,
                    'o',
                    [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::RoundtripKind,
                    [ref]$parsed
                )
                if (-not $valid) { return $null }
                $value = $parsed
            }
        } else {
            return $null
        }
        return $value.ToUniversalTime().ToString(
            "yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'",
            [Globalization.CultureInfo]::InvariantCulture
        )
    } catch {
        return $null
    }
}

function Get-Plan {
    return [ordered]@{
        mode = 'PLAN_ONLY'
        collects_data = $false
        contract = $Contract
        sources = $Sources
        prohibited = @(
            'TCP/socket/SQL/mysql.exe/mysqld.exe',
            'start/stop service, registry/firewall/ACL changes',
            'module installation, elevation, network discovery',
            'environment export, database/data-file reads, secrets'
        )
        classifications = @('OBSERVADO', 'NÃO OBSERVADO', 'INDETERMINADO', 'NÃO APLICÁVEL')
    }
}

function Test-OutputDestination {
    param([Parameter(Mandatory)][string]$LiteralPath)
    if (
        [string]::IsNullOrWhiteSpace($LiteralPath) -or
        $LiteralPath -match '[\x00-\x1f\x7f]' -or
        $LiteralPath -match '^(?:\\\\|//|\\[?.]\\|\\\?\?\\)' -or
        $LiteralPath -notmatch '^[C-Zc-z]:\\' -or
        -not [System.IO.Path]::IsPathRooted($LiteralPath)
    ) {
        throw 'Diretório de saída deve ser caminho local absoluto autorizado.'
    }
    $components = @($LiteralPath.Substring(3) -split '\\')
    foreach ($component in $components) {
        $baseName = ($component -split '\.', 2)[0]
        if (
            [string]::IsNullOrWhiteSpace($component) -or
            $component -in @('.', '..') -or
            $component -match '[/:*?"<>|]' -or
            $component -match '[ .]$' -or
            $baseName -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$'
        ) {
            throw 'Destino de saída contém componente inválido.'
        }
    }
    try {
        $fullPath = [System.IO.Path]::GetFullPath($LiteralPath)
    } catch {
        throw 'Destino de saída malformado.'
    }
    if (
        $fullPath -notmatch '^[C-Zc-z]:\\' -or
        [System.IO.Path]::GetExtension($fullPath) -cne '.json'
    ) {
        throw 'Destino de saída inválido.'
    }
    $fileName = [System.IO.Path]::GetFileNameWithoutExtension($fullPath)
    if ($fileName -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') {
        throw 'Destino de saída inválido.'
    }
    $parentPath = [System.IO.Path]::GetDirectoryName($fullPath)
    if ([string]::IsNullOrWhiteSpace($parentPath)) {
        throw 'Diretório pai inválido.'
    }
    try {
        $parent = Get-Item -LiteralPath $parentPath -Force -ErrorAction Stop
    } catch {
        throw 'Diretório pai autorizado não existe ou não pode ser validado.'
    }
    if (-not $parent.PSIsContainer -or $parent.PSProvider.Name -ne 'FileSystem') {
        throw 'Diretório pai não pertence ao filesystem local.'
    }
    $cursor = $parent
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Reparse point ou link não é aceito no destino.'
        }
        $cursor = $cursor.Parent
    }
    if (Test-Path -LiteralPath $fullPath) {
        throw 'Arquivo de saída já existe; o script não sobrescreve evidências.'
    }
    return [ordered]@{
        full_path = $fullPath
        parent_path = [string]$parent.FullName
    }
}

function Write-ExclusiveJson {
    param(
        [Parameter(Mandatory)][string]$Json,
        [Parameter(Mandatory)][System.Collections.IDictionary]$ValidatedDestination
    )
    $revalidated = Test-OutputDestination -LiteralPath $ValidatedDestination.full_path
    if (
        $revalidated.full_path -cne $ValidatedDestination.full_path -or
        $revalidated.parent_path -cne $ValidatedDestination.parent_path
    ) {
        throw 'Destino mudou após a validação; gravação cancelada.'
    }
    $stream = $null
    $writer = $null
    try {
        $stream = [System.IO.FileStream]::new(
            $revalidated.full_path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $writer = [System.IO.StreamWriter]::new(
            $stream, [System.Text.UTF8Encoding]::new($false)
        )
        $stream = $null
        $writer.Write($Json)
        $writer.Flush()
    } catch {
        throw 'Falha conservadora na criação exclusiva do relatório.'
    } finally {
        if ($null -ne $writer) { $writer.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Invoke-ReadonlyCollection {
    $collectedAt = [DateTimeOffset]::UtcNow
    $errors = [System.Collections.Generic.HashSet[string]]::new()

    try {
        $osRaw = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        if (
            [string]::IsNullOrWhiteSpace([string]$osRaw.Caption) -or
            [string]::IsNullOrWhiteSpace([string]$osRaw.Version) -or
            ([string]$osRaw.Caption).Length -gt 128 -or
            ([string]$osRaw.Version).Length -gt 64
        ) {
            $osEvidence = New-Evidence 'INDETERMINADO' @{ caption = $null; version = $null }
            [void]$errors.Add('OS_QUERY_FAILED')
        } else {
            $osEvidence = New-Evidence 'OBSERVADO' @{
                caption = [string]$osRaw.Caption
                version = [string]$osRaw.Version
            }
        }
    } catch {
        $osEvidence = New-Evidence 'INDETERMINADO' @{ caption = $null; version = $null }
        [void]$errors.Add('OS_QUERY_FAILED')
    }

    try {
        $listenerRaw = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalPort -eq 3308 })
        if (@($listenerRaw | Where-Object { [int64]$_.OwningProcess -le 0 }).Count -gt 0) {
            [void]$errors.Add('LISTENER_QUERY_FAILED')
        }
        if ($listenerRaw.Count -gt 16) {
            [void]$errors.Add('LISTENER_QUERY_FAILED')
            $listenerRaw = @($listenerRaw | Select-Object -First 16)
        }
    } catch {
        $listenerRaw = @()
        [void]$errors.Add('LISTENER_QUERY_FAILED')
    }

    try {
        $serviceRaw = @(Get-CimInstance -ClassName Win32_Service -ErrorAction Stop |
            Where-Object {
                $_.Name -match '(?i)(mysql|mariadb)' -or
                $_.DisplayName -match '(?i)(mysql|mariadb)'
            })
        if ($serviceRaw.Count -gt 64) {
            [void]$errors.Add('SERVICE_QUERY_FAILED')
            $serviceRaw = @($serviceRaw | Select-Object -First 64)
        }
    } catch {
        $serviceRaw = @()
        [void]$errors.Add('SERVICE_QUERY_FAILED')
    }

    $listeners = @($listenerRaw | Where-Object { [int64]$_.OwningProcess -gt 0 } |
        ForEach-Object {
            New-Evidence 'OBSERVADO' @{
                local_address = [string]$_.LocalAddress
                local_port = [int]$_.LocalPort
                pid = [int64]$_.OwningProcess
            }
        })
    $targetPids = @($listeners | ForEach-Object { $_.pid } | Sort-Object -Unique)

    $processes = @()
    $volumesById = @{}
    foreach ($targetPid in $targetPids) {
        try {
            $processMatches = @(Get-CimInstance -ClassName Win32_Process `
                -Filter ('ProcessId = {0}' -f [int64]$targetPid) -ErrorAction Stop)
        } catch {
            $processMatches = @()
            [void]$errors.Add('PROCESS_QUERY_FAILED')
        }
        if ($processMatches.Count -ne 1) {
            [void]$errors.Add('PROCESS_QUERY_FAILED')
            continue
        }
        $process = $processMatches[0]
        if (
            [string]::IsNullOrWhiteSpace([string]$process.Name) -or
            ([string]$process.Name).Length -gt 128
        ) {
            [void]$errors.Add('PROCESS_QUERY_FAILED')
            continue
        }
        $rawPath = [string]$process.ExecutablePath
        $path = if (Test-ObservedLocalPathSyntax -LiteralPath $rawPath) {
            $rawPath
        } else {
            if (-not [string]::IsNullOrWhiteSpace($rawPath)) {
                [void]$errors.Add('PROCESS_QUERY_FAILED')
            }
            $null
        }
        $summary = Get-SanitizedCommandSummary -CommandLine ([string]$process.CommandLine)
        $rawDatadir = Get-DatadirFromCommandLine -CommandLine ([string]$process.CommandLine)
        $datadir = if (Test-ObservedLocalPathSyntax -LiteralPath $rawDatadir) {
            $rawDatadir
        } else {
            if (-not [string]::IsNullOrWhiteSpace($rawDatadir)) {
                [void]$errors.Add('PROCESS_QUERY_FAILED')
            }
            $null
        }
        $hash = $null
        $signature = 'UNKNOWN'
        $productMetadata = 'UNKNOWN'
        $versionMetadata = $null
        if (-not [string]::IsNullOrWhiteSpace($path) -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            try {
                $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
            } catch {
                [void]$errors.Add('HASH_FAILED')
            }
            try {
                $signatureRaw = [string](Get-AuthenticodeSignature -LiteralPath $path -ErrorAction Stop).Status
                $signature = switch ($signatureRaw) {
                    'Valid' { 'VALID' }
                    'NotSigned' { 'NOT_SIGNED' }
                    'UnknownError' { 'UNKNOWN' }
                    default { 'INVALID' }
                }
            } catch {
                [void]$errors.Add('SIGNATURE_FAILED')
            }
            try {
                $versionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($path)
                $productName = [string]$versionInfo.ProductName
                if ($productName -match '(?i)mariadb') {
                    $productMetadata = 'MARIADB'
                } elseif ($productName -match '^(?i:MySQL Community Server(?: \(GPL\))?)$') {
                    $productMetadata = 'MYSQL_COMMUNITY'
                } elseif ($productName -match '(?i)mysql') {
                    $productMetadata = 'MYSQL_UNKNOWN_EDITION'
                } else {
                    $productMetadata = 'OTHER'
                }
                if ([string]$versionInfo.ProductVersion -match '(?<version>\d+\.\d+\.\d+)') {
                    $versionMetadata = $Matches['version']
                }
            } catch { }
        }

        $volumeId = $null
        if (-not [string]::IsNullOrWhiteSpace($datadir)) {
            try {
                $root = [System.IO.Path]::GetPathRoot($datadir)
                $drive = $root.TrimEnd('\').TrimEnd(':')
                $volume = Get-Volume -DriveLetter $drive -ErrorAction Stop
                $volumeId = ([string]$volume.UniqueId).Trim().ToLowerInvariant()
                if (
                    -not [string]::IsNullOrWhiteSpace($volumeId) -and
                    $volumeId.Length -le 256 -and
                    -not $volumesById.ContainsKey($volumeId)
                ) {
                    $volumesById[$volumeId] = New-Evidence 'OBSERVADO' @{ id = $volumeId }
                } elseif (
                    [string]::IsNullOrWhiteSpace($volumeId) -or
                    $volumeId.Length -gt 256
                ) {
                    [void]$errors.Add('VOLUME_QUERY_FAILED')
                }
            } catch {
                [void]$errors.Add('VOLUME_QUERY_FAILED')
            }
        }

        if ($process.Name -match '(?i)mariadb') { $productMetadata = 'MARIADB' }
        $creationTimestamp = ConvertTo-UtcCreationTimestamp -CreationDate $process.CreationDate
        $instanceMarker = if ($null -ne $creationTimestamp) {
            ('{0}|{1}' -f $process.ProcessId, $creationTimestamp)
        } else {
            $null
        }
        $stableObservation = $false
        $changeConfirmed = $false
        $observationIncomplete = ($null -eq $creationTimestamp -or $null -eq $path)
        try {
            $after = @(Get-CimInstance -ClassName Win32_Process `
                -Filter ('ProcessId = {0}' -f [int64]$targetPid) -ErrorAction Stop)
            if ($after.Count -ne 1) {
                $observationIncomplete = $true
                [void]$errors.Add('PROCESS_QUERY_FAILED')
            } else {
                $afterPid = [int64]$after[0].ProcessId
                $afterCreationTimestamp = ConvertTo-UtcCreationTimestamp `
                    -CreationDate $after[0].CreationDate
                $afterRawPath = [string]$after[0].ExecutablePath
                $afterPath = if (Test-ObservedLocalPathSyntax -LiteralPath $afterRawPath) {
                    $afterRawPath
                } else {
                    if (-not [string]::IsNullOrWhiteSpace($afterRawPath)) {
                        [void]$errors.Add('PROCESS_QUERY_FAILED')
                    }
                    $null
                }
                if (
                    $afterPid -le 0 -or
                    $null -eq $creationTimestamp -or
                    $null -eq $afterCreationTimestamp -or
                    $null -eq $path -or
                    $null -eq $afterPath
                ) {
                    $observationIncomplete = $true
                } elseif (
                    $afterPid -ne [int64]$process.ProcessId -or
                    $afterCreationTimestamp -cne $creationTimestamp -or
                    $afterPath -cne $path
                ) {
                    $changeConfirmed = $true
                } else {
                    $stableObservation = $true
                }
            }
        } catch {
            $observationIncomplete = $true
            [void]$errors.Add('PROCESS_QUERY_FAILED')
        }
        if ($changeConfirmed) {
            [void]$errors.Add('PROCESS_CHANGE_CONFIRMED')
        } elseif (-not $stableObservation -or $observationIncomplete) {
            [void]$errors.Add('PROCESS_OBSERVATION_INCOMPLETE')
        }

        $processes += New-Evidence 'OBSERVADO' @{
            pid = [int64]$process.ProcessId
            name = [string]$process.Name
            executable = if ($path) { $path } else { $null }
            product_metadata = $productMetadata
            version_metadata = $versionMetadata
            sha256 = $hash
            signature_status = $signature
            datadir = $datadir
            volume_id = $volumeId
            instance_marker = $instanceMarker
            stable_observation = $stableObservation
            command_line_summary = [ordered]@{
                sensitive_arguments_redacted = $summary.sensitive_arguments_redacted
                observed_flags = @($summary.observed_flags)
            }
        }
    }

    $services = @($serviceRaw | ForEach-Object {
        if (
            [string]::IsNullOrWhiteSpace([string]$_.Name) -or
            ([string]$_.Name).Length -gt 128
        ) {
            [void]$errors.Add('SERVICE_QUERY_FAILED')
        } else {
            $serviceState = switch ([string]$_.State) {
                'Running' { 'RUNNING' }
                'Stopped' { 'STOPPED' }
                default { 'OTHER' }
            }
            $servicePid = if (
                $serviceState -ne 'STOPPED' -and [int64]$_.ProcessId -gt 0
            ) {
                [int64]$_.ProcessId
            } else {
                $null
            }
            $serviceExecutable = Get-ExecutableFromCommandLine -CommandLine ([string]$_.PathName)
            if (
                $null -ne $serviceExecutable -and
                -not (Test-ObservedLocalPathSyntax -LiteralPath $serviceExecutable)
            ) {
                $serviceExecutable = $null
                [void]$errors.Add('SERVICE_QUERY_FAILED')
            }
            New-Evidence 'OBSERVADO' @{
                name = [string]$_.Name
                state = $serviceState
                pid = $servicePid
                executable = $serviceExecutable
            }
        }
    })

    try {
        $shareRaw = @(Get-SmbShare -ErrorAction Stop)
        if ($shareRaw.Count -gt 64) {
            [void]$errors.Add('SHARE_QUERY_FAILED')
            $shareRaw = @($shareRaw | Select-Object -First 64)
        }
        $shares = @($shareRaw | ForEach-Object {
            $sharePath = [string]$_.Path
            if (Test-ObservedLocalPathSyntax -LiteralPath $sharePath) {
                New-Evidence 'OBSERVADO' @{ path = $sharePath }
            } else {
                [void]$errors.Add('SHARE_QUERY_FAILED')
                New-Evidence 'INDETERMINADO' @{ path = $null }
            }
        })
    } catch {
        $shares = @()
        [void]$errors.Add('SHARE_QUERY_FAILED')
    }

    $replicationIndicators = @($processes | ForEach-Object {
        $_.command_line_summary.observed_flags | Where-Object {
            $_ -match '^(server-id|relay-log|log-bin):PRESENT$'
        } | ForEach-Object {
            ($_ -split ':', 2)[0]
        }
    } | Sort-Object -Unique)
    $replicationStatus = if ($replicationIndicators.Count -gt 0) { 'OBSERVADO' } else { 'INDETERMINADO' }

    return [ordered]@{
        schema_version = 'oc73-inventario-windows-v2'
        collected_at = $collectedAt.ToString('o')
        expires_at = $collectedAt.AddHours(1).ToString('o')
        host_contract = $Contract
        os = $osEvidence
        services = @($services)
        processes = @($processes)
        listeners = @($listeners)
        volumes = @($volumesById.Values)
        shares = @($shares)
        replication = New-Evidence $replicationStatus @{
            indicators = @($replicationIndicators)
        }
        errors = @($errors | Sort-Object)
    }
}

$modeCount = @(@($Explain, $PlanOnly, $Collect) | Where-Object { [bool]$_ }).Count
if ($modeCount -ne 1) {
    throw 'Escolha exatamente um modo: -Explain, -PlanOnly ou -Collect.'
}
if (-not $Collect -and (
    $WriteJson -or
    -not [string]::IsNullOrWhiteSpace($OutputPath) -or
    -not [string]::IsNullOrWhiteSpace($AuthorizationReference)
)) {
    throw 'Parâmetros de autorização/saída somente são aceitos com -Collect.'
}

if ($Explain) {
    Write-Output 'Este script não coleta por padrão. Fontes planejadas:'
    $Sources | ForEach-Object { Write-Output ('- ' + $_) }
    return
}

if ($PlanOnly) {
    Get-Plan | ConvertTo-Json -Depth 6
    return
}

if ([string]::IsNullOrWhiteSpace($AuthorizationReference)) {
    throw '-Collect exige referência de autorização operacional externa.'
}
if ($WriteJson -and [string]::IsNullOrWhiteSpace($OutputPath)) {
    throw '-WriteJson exige -OutputPath literal autorizado.'
}
if (-not $WriteJson -and -not [string]::IsNullOrWhiteSpace($OutputPath)) {
    throw '-OutputPath somente é aceito com -WriteJson.'
}

$validatedOutput = $null
if ($WriteJson) {
    $validatedOutput = Test-OutputDestination -LiteralPath $OutputPath
}

$report = Invoke-ReadonlyCollection
$json = $report | ConvertTo-Json -Depth 8
if ($WriteJson) {
    Write-ExclusiveJson -Json $json -ValidatedDestination $validatedOutput
} else {
    Write-Output $json
}
