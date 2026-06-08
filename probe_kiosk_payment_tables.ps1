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
            "Server=$sqlInstance;Database=SuitRepository;Integrated Security=True;Connection Timeout=5;"
        )
        $conn.Open()
        try {
            $cmd = $conn.CreateCommand()
            $cmd.CommandTimeout = 20
            $cmd.CommandText = @"
select
    c.TABLE_NAME as table_name,
    c.ORDINAL_POSITION as ordinal_position,
    c.COLUMN_NAME as column_name,
    c.DATA_TYPE as data_type,
    c.CHARACTER_MAXIMUM_LENGTH as max_length,
    c.IS_NULLABLE as is_nullable
from INFORMATION_SCHEMA.COLUMNS c
where c.TABLE_SCHEMA = 'dbo'
  and c.TABLE_NAME in ('PaymentDevice', 'PaymentInstruction', 'PaymentSetting', 'Payment', 'PaymentType', 'Machine', 'Kiosk')
order by c.TABLE_NAME, c.ORDINAL_POSITION;

select 'PaymentDevice' as table_name, * from dbo.PaymentDevice;
select 'PaymentInstruction' as table_name, * from dbo.PaymentInstruction;
select 'PaymentSetting' as table_name, * from dbo.PaymentSetting;
select 'Payment' as table_name, * from dbo.Payment;
select 'PaymentType' as table_name, * from dbo.PaymentType;
select 'Machine' as table_name, * from dbo.Machine;
select 'Kiosk' as table_name, * from dbo.Kiosk;
"@
            $reader = $cmd.ExecuteReader()

            function Read-Rows {
                param($Reader)
                $rows = @()
                while ($Reader.Read()) {
                    $row = [ordered]@{}
                    for ($i = 0; $i -lt $Reader.FieldCount; $i++) {
                        $value = $Reader.GetValue($i)
                        $row[$Reader.GetName($i)] = if ($value -is [DBNull]) { $null } else { $value }
                    }
                    $rows += [pscustomobject]$row
                }
                return $rows
            }

            $columns = Read-Rows -Reader $reader
            $sets = [ordered]@{}
            foreach ($name in @('PaymentDevice', 'PaymentInstruction', 'PaymentSetting', 'Payment', 'PaymentType', 'Machine', 'Kiosk')) {
                $null = $reader.NextResult()
                $sets[$name] = @(Read-Rows -Reader $reader)
            }

            [pscustomobject]@{
                generated_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
                host = $env:COMPUTERNAME
                sql_instance = $sqlInstance
                database = 'SuitRepository'
                columns = $columns
                samples = $sets
            }
        } finally {
            $conn.Close()
        }
    } -ArgumentList $sqlInstance | ConvertTo-Json -Depth 10
} finally {
    Remove-PSSession $session
}
