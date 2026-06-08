$ErrorActionPreference = 'Stop'

$Version = '0.1.0'
$HostName = $env:COMPUTERNAME
$StartedAt = Get-Date
$ListenPrefix = $env:KIOSK_AGENT_LISTEN_PREFIX
if (-not $ListenPrefix) { $ListenPrefix = 'http://+:3010/' }
$SqlInstance = $env:KIOSK_SQL_INSTANCE
if (-not $SqlInstance) { $SqlInstance = 'localhost\SQLEXPRESS' }
$SqlDatabase = $env:KIOSK_SQL_DATABASE
if (-not $SqlDatabase) { $SqlDatabase = 'SuitRepository' }
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StatePath = Join-Path $BaseDir 'kiosk_agent_state.json'
$LogDir = Join-Path $BaseDir 'logs'
$LogPath = Join-Path $LogDir 'agent.log'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-AgentLog {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    $item = Get-Item -LiteralPath $LogPath -ErrorAction SilentlyContinue
    if ($item -and $item.Length -gt 5MB) {
        $archive = Join-Path $LogDir ('agent-{0}.log' -f (Get-Date).ToString('yyyyMMdd-HHmmss'))
        Move-Item -LiteralPath $LogPath -Destination $archive
    }
}

function ConvertTo-IsoString {
    param([datetime]$Value)
    return $Value.ToString('yyyy-MM-ddTHH:mm:sszzz')
}

function Read-AgentState {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return @{
            running = $false
            last_success_at = $null
            last_error_at = $null
            last_error = $null
            source_latest_order_time = $null
            last_exported_order_time = $null
        }
    }
    try {
        $raw = Get-Content -Raw -LiteralPath $StatePath -Encoding UTF8
        return $raw | ConvertFrom-Json -AsHashtable
    } catch {
        return @{
            running = $false
            last_success_at = $null
            last_error_at = (ConvertTo-IsoString (Get-Date))
            last_error = $_.Exception.Message
            source_latest_order_time = $null
            last_exported_order_time = $null
        }
    }
}

function Write-AgentState {
    param([hashtable]$State)
    $State | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

function Invoke-LocalSqlRows {
    param(
        [string]$Sql,
        [int]$TimeoutSeconds = 10
    )

    Add-Type -AssemblyName System.Data
    $conn = New-Object System.Data.SqlClient.SqlConnection(
        "Server=$SqlInstance;Database=$SqlDatabase;Integrated Security=True;Connection Timeout=5;"
    )
    $conn.Open()
    try {
        $cmd = $conn.CreateCommand()
        $cmd.CommandTimeout = $TimeoutSeconds
        $cmd.CommandText = $Sql
        $reader = $cmd.ExecuteReader()
        try {
            $rows = @()
            while ($reader.Read()) {
                $row = [ordered]@{}
                for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                    $name = $reader.GetName($i)
                    $value = $reader.GetValue($i)
                    if ($value -is [DBNull]) {
                        $row[$name] = $null
                    } elseif ($value -is [DateTime]) {
                        $row[$name] = $value.ToString('yyyy-MM-dd HH:mm:ss')
                    } else {
                        $row[$name] = $value
                    }
                }
                $rows += [pscustomobject]$row
            }
            return $rows
        } finally {
            $reader.Close()
        }
    } finally {
        $conn.Close()
    }
}

function Get-HealthPayload {
    $now = Get-Date
    return [ordered]@{
        ok = $true
        service = '6ka-kiosk-agent'
        version = $Version
        host = $HostName
        started_at = ConvertTo-IsoString $StartedAt
        uptime_seconds = [int]($now - $StartedAt).TotalSeconds
        time = ConvertTo-IsoString $now
    }
}

function Get-DatabaseStatus {
    $start = Get-Date
    try {
        $rows = Invoke-LocalSqlRows -Sql @'
select
    convert(varchar(10), max(BusinessDate), 120) as max_business_date,
    convert(varchar(19), max(Timestamp), 120) as latest_order_time,
    count(*) as order_rows
from dbo.[Order];
'@
        $row = if ($rows.Count -gt 0) { $rows[0] } else { $null }
        return [ordered]@{
            ok = $true
            instance = $SqlInstance
            database = $SqlDatabase
            latency_ms = [int]((Get-Date) - $start).TotalMilliseconds
            latest_order_time = if ($row) { $row.latest_order_time } else { $null }
            max_business_date = if ($row) { $row.max_business_date } else { $null }
            order_rows = if ($row) { [int64]$row.order_rows } else { 0 }
        }
    } catch {
        return [ordered]@{
            ok = $false
            instance = $SqlInstance
            database = $SqlDatabase
            latency_ms = [int]((Get-Date) - $start).TotalMilliseconds
            error = $_.Exception.Message
        }
    }
}

function Get-StatusPayload {
    $health = Get-HealthPayload
    $state = Read-AgentState
    $db = Get-DatabaseStatus
    if ($db.ok -and $db.latest_order_time) {
        $state.source_latest_order_time = $db.latest_order_time
    }

    return [ordered]@{
        ok = [bool]$db.ok
        agent = [ordered]@{
            online = $true
            version = $Version
            host = $health.host
            started_at = $health.started_at
            uptime_seconds = $health.uptime_seconds
        }
        database = $db
        sync = [ordered]@{
            running = [bool]$state.running
            last_success_at = $state.last_success_at
            last_error_at = $state.last_error_at
            last_error = $state.last_error
            source_latest_order_time = $state.source_latest_order_time
            last_exported_order_time = $state.last_exported_order_time
        }
    }
}

function Send-JsonResponse {
    param(
        [System.Net.HttpListenerContext]$Context,
        [int]$StatusCode,
        [object]$Payload
    )
    $json = $Payload | ConvertTo-Json -Depth 10
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $Context.Response.StatusCode = $StatusCode
    $Context.Response.ContentType = 'application/json; charset=utf-8'
    $Context.Response.ContentLength64 = $bytes.Length
    $Context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $Context.Response.OutputStream.Close()
}

function Accept-SyncRun {
    $state = Read-AgentState
    $state.running = $false
    $state.last_error = 'sync worker not implemented yet'
    $state.last_error_at = ConvertTo-IsoString (Get-Date)
    Write-AgentState $state
    return [ordered]@{
        ok = $true
        accepted = $true
        job_id = (Get-Date).ToString('yyyyMMdd-HHmmss')
        message = 'sync worker not implemented yet'
    }
}

Write-AgentLog "starting 6ka-kiosk-agent $Version on $ListenPrefix"

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($ListenPrefix)
$listener.Start()

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        try {
            $path = $context.Request.Url.AbsolutePath
            $method = $context.Request.HttpMethod.ToUpperInvariant()
            Write-AgentLog "$method $path from $($context.Request.RemoteEndPoint)"

            if ($method -eq 'GET' -and $path -eq '/health') {
                Send-JsonResponse -Context $context -StatusCode 200 -Payload (Get-HealthPayload)
                continue
            }
            if ($method -eq 'GET' -and $path -eq '/api/status') {
                $payload = Get-StatusPayload
                Send-JsonResponse -Context $context -StatusCode 200 -Payload $payload
                continue
            }
            if ($method -eq 'POST' -and $path -eq '/api/sync/run') {
                Send-JsonResponse -Context $context -StatusCode 202 -Payload (Accept-SyncRun)
                continue
            }

            Send-JsonResponse -Context $context -StatusCode 404 -Payload @{ ok = $false; error = 'not found' }
        } catch {
            Write-AgentLog "request error: $($_.Exception.Message)"
            try {
                Send-JsonResponse -Context $context -StatusCode 500 -Payload @{ ok = $false; error = $_.Exception.Message }
            } catch {}
        }
    }
} finally {
    $listener.Stop()
    $listener.Close()
    Write-AgentLog 'stopped'
}
