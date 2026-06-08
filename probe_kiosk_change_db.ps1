$ErrorActionPreference = 'Stop'

function Load-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) {
            continue
        }

        $parts = $line.Split('=', 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name -and -not [Environment]::GetEnvironmentVariable($name)) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

Load-DotEnv -Path (Join-Path $PSScriptRoot '.env')

$remoteHost = [Environment]::GetEnvironmentVariable('KIOSK_HOST')
if (-not $remoteHost) { $remoteHost = '100.113.224.68' }

$authMode = [Environment]::GetEnvironmentVariable('KIOSK_AUTH_MODE')
if (-not $authMode) { $authMode = 'password' }
$authMode = $authMode.ToLowerInvariant()

$sqlInstance = [Environment]::GetEnvironmentVariable('KIOSK_SQL_INSTANCE')
if (-not $sqlInstance) { $sqlInstance = 'localhost\SQLEXPRESS' }

$openTimeout = [Environment]::GetEnvironmentVariable('RP_WINRM_OPEN_TIMEOUT_MS')
if (-not $openTimeout) { $openTimeout = '5000' }

$operationTimeout = [Environment]::GetEnvironmentVariable('RP_WINRM_OPERATION_TIMEOUT_MS')
if (-not $operationTimeout) { $operationTimeout = '20000' }

$sessionOption = New-PSSessionOption -OpenTimeout ([int]$openTimeout) -OperationTimeout ([int]$operationTimeout)
$invokeArgs = @{
    ComputerName  = $remoteHost
    SessionOption = $sessionOption
}

if ($authMode -eq 'cmdkey') {
    $invokeArgs.Authentication = 'Negotiate'
} else {
    $remoteUser = [Environment]::GetEnvironmentVariable('KIOSK_USER')
    $remotePassword = [Environment]::GetEnvironmentVariable('KIOSK_PASSWORD')
    $securePassword = ConvertTo-SecureString $remotePassword -AsPlainText -Force
    $credential = New-Object System.Management.Automation.PSCredential($remoteUser, $securePassword)
    $invokeArgs.Credential = $credential
    $invokeArgs.Authentication = 'Negotiate'
}

$result = Invoke-Command @invokeArgs -ScriptBlock {
    param($sqlInstance)

    Add-Type -AssemblyName System.Data
    $conn = New-Object System.Data.SqlClient.SqlConnection(
        "Server=$sqlInstance;Database=master;Integrated Security=True;Connection Timeout=5;"
    )
    $conn.Open()
    try {
        $cmd = $conn.CreateCommand()
        $cmd.CommandTimeout = 20
        $cmd.CommandText = @"
select
    d.name as database_name,
    d.state_desc,
    d.create_date,
    d.collation_name,
    mf.physical_name,
    cast(mf.size * 8.0 / 1024 as decimal(18,2)) as size_mb
from sys.databases d
left join sys.master_files mf on mf.database_id = d.database_id and mf.type = 0
where d.database_id > 4
order by d.name;
"@
        $reader = $cmd.ExecuteReader()
        $databases = @()
        while ($reader.Read()) {
            $databases += [pscustomobject]@{
                database_name = [string]$reader['database_name']
                state_desc = [string]$reader['state_desc']
                create_date = ([DateTime]$reader['create_date']).ToString('yyyy-MM-dd HH:mm:ss')
                collation_name = if ($reader['collation_name'] -is [DBNull]) { $null } else { [string]$reader['collation_name'] }
                physical_name = if ($reader['physical_name'] -is [DBNull]) { $null } else { [string]$reader['physical_name'] }
                size_mb = if ($reader['size_mb'] -is [DBNull]) { $null } else { [decimal]$reader['size_mb'] }
            }
        }
        $reader.Close()

        $patterns = @(
            'cash',
            'coin',
            'change',
            'money',
            'bill',
            'denom',
            'cassette',
            'cashbox',
            'payout',
            'hopper',
            "$([char]0x96F6)",
            "$([char]0x627E)",
            "$([char]0x9322)",
            "$([char]0x786C)$([char]0x5E63)",
            "$([char]0x9214)"
        )
        $matches = @()
        foreach ($db in $databases | Where-Object { $_.state_desc -eq 'ONLINE' }) {
            $safeDb = $db.database_name.Replace(']', ']]')
            $cmd.CommandText = @"
use [$safeDb];
select
    db_name() as database_name,
    schema_name(t.schema_id) as schema_name,
    t.name as table_name,
    c.name as column_name,
    ty.name as data_type,
    isnull(ps.row_count, 0) as row_count
from sys.tables t
join sys.columns c on c.object_id = t.object_id
join sys.types ty on ty.user_type_id = c.user_type_id
outer apply (
    select sum(row_count) as row_count
    from sys.dm_db_partition_stats
    where object_id = t.object_id and index_id in (0, 1)
) ps
order by t.name, c.column_id;
"@
            $reader = $cmd.ExecuteReader()
            try {
                while ($reader.Read()) {
                    $table = [string]$reader['table_name']
                    $column = [string]$reader['column_name']
                    $haystack = ($table + ' ' + $column).ToLowerInvariant()
                    $hit = @($patterns | Where-Object { $haystack.Contains($_.ToLowerInvariant()) })
                    if ($hit.Count -gt 0) {
                        $matches += [pscustomobject]@{
                            database_name = [string]$reader['database_name']
                            schema_name = [string]$reader['schema_name']
                            table_name = $table
                            column_name = $column
                            data_type = [string]$reader['data_type']
                            row_count = [int64]$reader['row_count']
                            matched = ($hit -join ',')
                        }
                    }
                }
            } finally {
                $reader.Close()
            }
        }

        [pscustomobject]@{
            generated_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            host = $env:COMPUTERNAME
            sql_instance = $sqlInstance
            databases = $databases
            candidate_columns = $matches
        }
    } finally {
        $conn.Close()
    }
} -ArgumentList $sqlInstance

$result | ConvertTo-Json -Depth 8
