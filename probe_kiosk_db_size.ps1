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

$database = [Environment]::GetEnvironmentVariable('KIOSK_SQL_DATABASE')
if (-not $database) { $database = 'SuitRepository' }

$openTimeout = [Environment]::GetEnvironmentVariable('RP_WINRM_OPEN_TIMEOUT_MS')
if (-not $openTimeout) { $openTimeout = '5000' }

$operationTimeout = [Environment]::GetEnvironmentVariable('RP_WINRM_OPERATION_TIMEOUT_MS')
if (-not $operationTimeout) { $operationTimeout = '10000' }

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
    param($sqlInstance, $database)

    Add-Type -AssemblyName System.Data
    $conn = New-Object System.Data.SqlClient.SqlConnection(
        "Server=$sqlInstance;Database=$database;Integrated Security=True;Connection Timeout=5;"
    )
    $conn.Open()
    try {
        $cmd = $conn.CreateCommand()
        $cmd.CommandTimeout = 15
        $cmd.CommandText = @"
select
    db_name() as database_name,
    name as logical_name,
    type_desc,
    physical_name,
    cast(size * 8.0 / 1024 as decimal(18,2)) as size_mb,
    cast(fileproperty(name, 'SpaceUsed') * 8.0 / 1024 as decimal(18,2)) as used_mb
from sys.database_files
order by type_desc, name;

select
    schema_name(t.schema_id) as schema_name,
    t.name as table_name,
    isnull(rows.row_count, 0) as row_count,
    cast(isnull(pages.reserved_page_count, 0) * 8.0 / 1024 as decimal(18,2)) as total_mb,
    cast(isnull(pages.used_page_count, 0) * 8.0 / 1024 as decimal(18,2)) as used_mb
from sys.tables t
outer apply (
    select sum(ps.row_count) as row_count
    from sys.dm_db_partition_stats ps
    where ps.object_id = t.object_id
      and ps.index_id in (0, 1)
) rows
outer apply (
    select
        sum(ps.reserved_page_count) as reserved_page_count,
        sum(ps.used_page_count) as used_page_count
    from sys.dm_db_partition_stats ps
    where ps.object_id = t.object_id
) pages
order by total_mb desc, row_count desc;

select
    min(BusinessDate) as min_business_date,
    max(BusinessDate) as max_business_date,
    count(*) as order_rows
from dbo.[Order];

select
    count(*) as order_product_rows
from dbo.OrderProduct;

select
    count(*) as order_payment_rows
from dbo.OrderPayment;
"@

        $reader = $cmd.ExecuteReader()

        $files = @()
        while ($reader.Read()) {
            $files += [pscustomobject]@{
                database_name = [string]$reader['database_name']
                logical_name = [string]$reader['logical_name']
                type_desc = [string]$reader['type_desc']
                physical_name = [string]$reader['physical_name']
                size_mb = [decimal]$reader['size_mb']
                used_mb = if ($reader['used_mb'] -is [DBNull]) { $null } else { [decimal]$reader['used_mb'] }
            }
        }

        $null = $reader.NextResult()
        $tables = @()
        while ($reader.Read()) {
            $tables += [pscustomobject]@{
                schema_name = [string]$reader['schema_name']
                table_name = [string]$reader['table_name']
                row_count = [int64]$reader['row_count']
                total_mb = [decimal]$reader['total_mb']
                used_mb = [decimal]$reader['used_mb']
            }
        }

        $null = $reader.NextResult()
        $orderRange = @{}
        if ($reader.Read()) {
            $orderRange = @{
                min_business_date = if ($reader['min_business_date'] -is [DBNull]) { $null } else { ([DateTime]$reader['min_business_date']).ToString('yyyy-MM-dd') }
                max_business_date = if ($reader['max_business_date'] -is [DBNull]) { $null } else { ([DateTime]$reader['max_business_date']).ToString('yyyy-MM-dd') }
                order_rows = [int64]$reader['order_rows']
            }
        }

        $null = $reader.NextResult()
        $orderProductRows = 0
        if ($reader.Read()) {
            $orderProductRows = [int64]$reader['order_product_rows']
        }

        $null = $reader.NextResult()
        $orderPaymentRows = 0
        if ($reader.Read()) {
            $orderPaymentRows = [int64]$reader['order_payment_rows']
        }

        [pscustomobject]@{
            generated_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            host = $env:COMPUTERNAME
            sql_instance = $sqlInstance
            database = $database
            files = $files
            top_tables = @($tables | Select-Object -First 20)
            order_range = $orderRange
            key_table_counts = @{
                Order = $orderRange.order_rows
                OrderProduct = $orderProductRows
                OrderPayment = $orderPaymentRows
            }
        }
    } finally {
        $conn.Close()
    }
} -ArgumentList $sqlInstance, $database

$result | ConvertTo-Json -Depth 8
