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

function New-KioskSession {
    param(
        [string]$RemoteHost,
        [string]$AuthMode,
        [int]$OpenTimeoutMs,
        [int]$OperationTimeoutMs
    )

    $sessionOption = New-PSSessionOption -OpenTimeout $OpenTimeoutMs -OperationTimeout $OperationTimeoutMs
    $sessionArgs = @{
        ComputerName  = $RemoteHost
        SessionOption = $sessionOption
    }

    if ($AuthMode -eq 'cmdkey') {
        $sessionArgs.Authentication = 'Negotiate'
    } else {
        $remoteUser = [Environment]::GetEnvironmentVariable('KIOSK_USER')
        $remotePassword = [Environment]::GetEnvironmentVariable('KIOSK_PASSWORD')
        $securePassword = ConvertTo-SecureString $remotePassword -AsPlainText -Force
        $credential = New-Object System.Management.Automation.PSCredential($remoteUser, $securePassword)
        $sessionArgs.Credential = $credential
        $sessionArgs.Authentication = 'Negotiate'
    }

    New-PSSession @sessionArgs
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
if (-not $operationTimeout) { $operationTimeout = '900000' }

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$remoteExportDir = "C:\Windows\Temp\6ka-sales-export-$timestamp"
$localExportDir = Join-Path (Join-Path $PSScriptRoot 'exports') "sales-$timestamp"
New-Item -ItemType Directory -Force -Path $localExportDir | Out-Null

$session = New-KioskSession `
    -RemoteHost $remoteHost `
    -AuthMode $authMode `
    -OpenTimeoutMs ([int]$openTimeout) `
    -OperationTimeoutMs ([int]$operationTimeout)

try {
    $exportInfo = Invoke-Command -Session $session -ScriptBlock {
        param($sqlInstance, $database, $remoteExportDir)

        function ConvertTo-CsvField {
            param($Value)
            if ($null -eq $Value -or $Value -is [DBNull]) {
                return ''
            }
            if ($Value -is [DateTime]) {
                $text = $Value.ToString('yyyy-MM-dd HH:mm:ss')
            } else {
                $text = [string]$Value
            }
            $text = $text.Replace('"', '""')
            if ($text.Contains(',') -or $text.Contains('"') -or $text.Contains("`n") -or $text.Contains("`r")) {
                return '"' + $text + '"'
            }
            return $text
        }

        function Export-SqlCsv {
            param(
                [System.Data.SqlClient.SqlConnection]$Connection,
                [string]$Sql,
                [string]$Path
            )

            $cmd = $Connection.CreateCommand()
            $cmd.CommandTimeout = 600
            $cmd.CommandText = $Sql
            $reader = $cmd.ExecuteReader()
            $writer = New-Object System.IO.StreamWriter($Path, $false, (New-Object System.Text.UTF8Encoding($false)))
            try {
                $headers = for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                    ConvertTo-CsvField $reader.GetName($i)
                }
                $writer.WriteLine(($headers -join ','))

                $count = 0
                while ($reader.Read()) {
                    $fields = for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                        ConvertTo-CsvField $reader.GetValue($i)
                    }
                    $writer.WriteLine(($fields -join ','))
                    $count += 1
                }
                return $count
            } finally {
                $writer.Close()
                $reader.Close()
            }
        }

        New-Item -ItemType Directory -Force -Path $remoteExportDir | Out-Null
        Add-Type -AssemblyName System.Data
        $conn = New-Object System.Data.SqlClient.SqlConnection(
            "Server=$sqlInstance;Database=$database;Integrated Security=True;Connection Timeout=5;"
        )
        $conn.Open()
        try {
            $exports = @(
                @{
                    name = 'orders'
                    file = 'orders.csv'
                    sql = @"
select
    convert(varchar(36), Guid) as guid,
    ID as id,
    Provider as provider,
    Type as type,
    Status as status,
    TotalAmount as total_amount,
    convert(varchar(36), KioskGuid) as kiosk_guid,
    convert(varchar(10), BusinessDate, 120) as business_date,
    convert(varchar(36), StoreGuid) as store_guid,
    convert(varchar(19), OrderTime, 120) as order_time,
    convert(varchar(19), VoidTime, 120) as void_time,
    convert(varchar(19), Timestamp, 120) as timestamp,
    DisplayID as display_id
from dbo.[Order]
order by BusinessDate, Timestamp;
"@
                },
                @{
                    name = 'order_products'
                    file = 'order_products.csv'
                    sql = @"
select
    convert(varchar(36), OrderGuid) as order_guid,
    convert(varchar(36), Parent) as parent,
    convert(varchar(36), Guid) as guid,
    ID as id,
    Name as name,
    Type as type,
    Tax as tax,
    UnitPrice as unit_price,
    AdditionalPrice as additional_price,
    Quantity as quantity,
    TotalPrice as total_price,
    Sequence as sequence,
    convert(varchar(36), StoreGuid) as store_guid,
    convert(varchar(19), Timestamp, 120) as timestamp
from dbo.OrderProduct
order by Timestamp;
"@
                },
                @{
                    name = 'order_payments'
                    file = 'order_payments.csv'
                    sql = @"
select
    convert(varchar(36), OrderGuid) as order_guid,
    convert(varchar(36), Guid) as guid,
    PaymentTypeID as payment_type_id,
    Amount as amount,
    RedeemAmount as redeem_amount,
    [Change] as change_amount,
    convert(varchar(36), KioskGuid) as kiosk_guid,
    convert(varchar(36), StoreGuid) as store_guid,
    convert(varchar(19), Timestamp, 120) as timestamp
from dbo.OrderPayment
order by Timestamp;
"@
                },
                @{
                    name = 'product_categories'
                    file = 'product_categories.csv'
                    sql = @"
select ID as id, Name as name, cast(Enabled as int) as enabled, Sequence as sequence,
       convert(varchar(36), StoreGuid) as store_guid, convert(varchar(19), Timestamp, 120) as timestamp
from dbo.ProductCategory;
"@
                },
                @{
                    name = 'product_category_items'
                    file = 'product_category_items.csv'
                    sql = @"
select ProductCategoryID as product_category_id, ProductID as product_id, Sequence as sequence,
       convert(varchar(36), StoreGuid) as store_guid, convert(varchar(19), Timestamp, 120) as timestamp
from dbo.ProductCategoryItem;
"@
                },
                @{
                    name = 'payment_types'
                    file = 'payment_types.csv'
                    sql = @"
select ID as id, Name as name, Type as type, convert(varchar(19), Timestamp, 120) as timestamp
from dbo.PaymentType;
"@
                }
            )

            $files = @()
            foreach ($export in $exports) {
                $path = Join-Path $remoteExportDir $export.file
                $count = Export-SqlCsv -Connection $conn -Sql $export.sql -Path $path
                $item = Get-Item -LiteralPath $path
                $files += [pscustomobject]@{
                    name = $export.name
                    file = $export.file
                    rows = $count
                    size_bytes = $item.Length
                }
            }

            $rangeCmd = $conn.CreateCommand()
            $rangeCmd.CommandText = @"
select
    convert(varchar(10), min(BusinessDate), 120) as min_business_date,
    convert(varchar(10), max(BusinessDate), 120) as max_business_date,
    convert(varchar(19), max(Timestamp), 120) as latest_order_time,
    count(*) as order_rows
from dbo.[Order];
"@
            $rangeReader = $rangeCmd.ExecuteReader()
            $range = @{}
            if ($rangeReader.Read()) {
                $range = @{
                    min_business_date = [string]$rangeReader['min_business_date']
                    max_business_date = [string]$rangeReader['max_business_date']
                    latest_order_time = [string]$rangeReader['latest_order_time']
                    order_rows = [int64]$rangeReader['order_rows']
                }
            }
            $rangeReader.Close()

            [pscustomobject]@{
                generated_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
                remote_export_dir = $remoteExportDir
                range = $range
                files = $files
            }
        } finally {
            $conn.Close()
        }
    } -ArgumentList $sqlInstance, $database, $remoteExportDir

    foreach ($file in $exportInfo.files) {
        Copy-Item -FromSession $session -LiteralPath (Join-Path $remoteExportDir $file.file) -Destination (Join-Path $localExportDir $file.file)
    }

    $manifest = [pscustomobject]@{
        ok = $true
        generated_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        remote_host = $remoteHost
        sql_instance = $sqlInstance
        database = $database
        local_export_dir = $localExportDir
        remote_export = $exportInfo
    }
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $localExportDir 'manifest.json') -Encoding UTF8
    $manifest | ConvertTo-Json -Depth 8
} finally {
    Remove-PSSession -Session $session
}
