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
        $cmd.CommandTimeout = 20
        $cmd.CommandText = @"
select
    c.TABLE_SCHEMA as schema_name,
    c.TABLE_NAME as table_name,
    c.ORDINAL_POSITION as ordinal_position,
    c.COLUMN_NAME as column_name,
    c.DATA_TYPE as data_type,
    c.CHARACTER_MAXIMUM_LENGTH as max_length,
    c.NUMERIC_PRECISION as numeric_precision,
    c.NUMERIC_SCALE as numeric_scale,
    c.IS_NULLABLE as is_nullable,
    case when pk.COLUMN_NAME is null then cast(0 as bit) else cast(1 as bit) end as is_primary_key
from INFORMATION_SCHEMA.COLUMNS c
left join (
    select ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
    from INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
    join INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
      on ku.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
     and ku.TABLE_SCHEMA = tc.TABLE_SCHEMA
     and ku.TABLE_NAME = tc.TABLE_NAME
    where tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
) pk
  on pk.TABLE_SCHEMA = c.TABLE_SCHEMA
 and pk.TABLE_NAME = c.TABLE_NAME
 and pk.COLUMN_NAME = c.COLUMN_NAME
where c.TABLE_SCHEMA = 'dbo'
  and c.TABLE_NAME in ('Order', 'OrderProduct', 'OrderPayment', 'ProductCategory', 'ProductCategoryItem', 'PaymentType')
order by c.TABLE_NAME, c.ORDINAL_POSITION;

select
    object_schema_name(i.object_id) as schema_name,
    object_name(i.object_id) as table_name,
    i.name as index_name,
    i.type_desc,
    i.is_unique,
    i.is_primary_key,
    stuff((
        select ', ' + col.name
        from sys.index_columns ic
        join sys.columns col on col.object_id = ic.object_id and col.column_id = ic.column_id
        where ic.object_id = i.object_id
          and ic.index_id = i.index_id
          and ic.is_included_column = 0
        order by ic.key_ordinal
        for xml path(''), type
    ).value('.', 'nvarchar(max)'), 1, 2, '') as key_columns
from sys.indexes i
where object_schema_name(i.object_id) = 'dbo'
  and object_name(i.object_id) in ('Order', 'OrderProduct', 'OrderPayment', 'ProductCategory', 'ProductCategoryItem', 'PaymentType')
  and i.index_id > 0
order by table_name, i.is_primary_key desc, index_name;

select
    min(BusinessDate) as min_business_date,
    max(BusinessDate) as max_business_date,
    min(Timestamp) as min_timestamp,
    max(Timestamp) as max_timestamp,
    count(*) as order_rows,
    count(distinct convert(date, BusinessDate)) as business_days
from dbo.[Order];

select top 5
    Guid,
    ID,
    DisplayID,
    Status,
    BusinessDate,
    Timestamp
from dbo.[Order]
order by Timestamp desc;
"@

        $reader = $cmd.ExecuteReader()
        $columns = @()
        while ($reader.Read()) {
            $columns += [pscustomobject]@{
                table_name = [string]$reader['table_name']
                ordinal_position = [int]$reader['ordinal_position']
                column_name = [string]$reader['column_name']
                data_type = [string]$reader['data_type']
                max_length = if ($reader['max_length'] -is [DBNull]) { $null } else { [int]$reader['max_length'] }
                numeric_precision = if ($reader['numeric_precision'] -is [DBNull]) { $null } else { [int]$reader['numeric_precision'] }
                numeric_scale = if ($reader['numeric_scale'] -is [DBNull]) { $null } else { [int]$reader['numeric_scale'] }
                is_nullable = [string]$reader['is_nullable']
                is_primary_key = [bool]$reader['is_primary_key']
            }
        }

        $null = $reader.NextResult()
        $indexes = @()
        while ($reader.Read()) {
            $indexes += [pscustomobject]@{
                table_name = [string]$reader['table_name']
                index_name = [string]$reader['index_name']
                type_desc = [string]$reader['type_desc']
                is_unique = [bool]$reader['is_unique']
                is_primary_key = [bool]$reader['is_primary_key']
                key_columns = if ($reader['key_columns'] -is [DBNull]) { $null } else { [string]$reader['key_columns'] }
            }
        }

        $null = $reader.NextResult()
        $range = @{}
        if ($reader.Read()) {
            $range = @{
                min_business_date = if ($reader['min_business_date'] -is [DBNull]) { $null } else { ([DateTime]$reader['min_business_date']).ToString('yyyy-MM-dd') }
                max_business_date = if ($reader['max_business_date'] -is [DBNull]) { $null } else { ([DateTime]$reader['max_business_date']).ToString('yyyy-MM-dd') }
                min_timestamp = if ($reader['min_timestamp'] -is [DBNull]) { $null } else { ([DateTime]$reader['min_timestamp']).ToString('yyyy-MM-dd HH:mm:ss') }
                max_timestamp = if ($reader['max_timestamp'] -is [DBNull]) { $null } else { ([DateTime]$reader['max_timestamp']).ToString('yyyy-MM-dd HH:mm:ss') }
                order_rows = [int64]$reader['order_rows']
                business_days = [int]$reader['business_days']
            }
        }

        $null = $reader.NextResult()
        $latest = @()
        while ($reader.Read()) {
            $latest += [pscustomobject]@{
                guid = [string]$reader['Guid']
                id = [string]$reader['ID']
                display_id = [string]$reader['DisplayID']
                status = [int]$reader['Status']
                business_date = if ($reader['BusinessDate'] -is [DBNull]) { $null } else { ([DateTime]$reader['BusinessDate']).ToString('yyyy-MM-dd') }
                timestamp = if ($reader['Timestamp'] -is [DBNull]) { $null } else { ([DateTime]$reader['Timestamp']).ToString('yyyy-MM-dd HH:mm:ss') }
            }
        }

        [pscustomobject]@{
            generated_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            host = $env:COMPUTERNAME
            sql_instance = $sqlInstance
            database = $database
            columns = $columns
            indexes = $indexes
            order_range = $range
            latest_orders_sample = $latest
        }
    } finally {
        $conn.Close()
    }
} -ArgumentList $sqlInstance, $database

$result | ConvertTo-Json -Depth 8
