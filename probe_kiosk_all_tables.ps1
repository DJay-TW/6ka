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

$sqlInstance = [Environment]::GetEnvironmentVariable('KIOSK_SQL_INSTANCE')
if (-not $sqlInstance) { $sqlInstance = 'localhost\SQLEXPRESS' }

$session = New-PSSession -ComputerName $remoteHost -Authentication Negotiate
try {
    Invoke-Command -Session $session -ScriptBlock {
        param($sqlInstance)

        Add-Type -AssemblyName System.Data
        $conn = New-Object System.Data.SqlClient.SqlConnection(
            "Server=$sqlInstance;Database=master;Integrated Security=True;Connection Timeout=5;"
        )
        $conn.Open()
        try {
            $cmd = $conn.CreateCommand()
            $cmd.CommandTimeout = 20
            $cmd.CommandText = "select name from sys.databases where database_id > 4 and state_desc = 'ONLINE' order by name"
            $reader = $cmd.ExecuteReader()
            $databases = @()
            while ($reader.Read()) {
                $databases += [string]$reader['name']
            }
            $reader.Close()

            $tables = @()
            foreach ($db in $databases) {
                $safeDb = $db.Replace(']', ']]')
                $cmd.CommandText = @"
use [$safeDb];
select
    db_name() as database_name,
    schema_name(t.schema_id) as schema_name,
    t.name as table_name,
    isnull(ps.row_count, 0) as row_count,
    cast(isnull(pages.used_page_count, 0) * 8.0 / 1024 as decimal(18,2)) as used_mb
from sys.tables t
outer apply (
    select sum(row_count) as row_count
    from sys.dm_db_partition_stats
    where object_id = t.object_id and index_id in (0, 1)
) ps
outer apply (
    select sum(used_page_count) as used_page_count
    from sys.dm_db_partition_stats
    where object_id = t.object_id
) pages
order by t.name;
"@
                $reader = $cmd.ExecuteReader()
                try {
                    while ($reader.Read()) {
                        $tables += [pscustomobject]@{
                            database_name = [string]$reader['database_name']
                            schema_name = [string]$reader['schema_name']
                            table_name = [string]$reader['table_name']
                            row_count = [int64]$reader['row_count']
                            used_mb = [decimal]$reader['used_mb']
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
                tables = $tables
            }
        } finally {
            $conn.Close()
        }
    } -ArgumentList $sqlInstance | ConvertTo-Json -Depth 6
} finally {
    Remove-PSSession $session
}
