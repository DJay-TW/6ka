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
if (-not $operationTimeout) { $operationTimeout = '600000' }

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupName = "${database}_${timestamp}.bak"
$remoteBackupPath = "C:\Windows\Temp\$backupName"
$localBackupDir = Join-Path $PSScriptRoot 'backups'
$localBackupPath = Join-Path $localBackupDir $backupName

New-Item -ItemType Directory -Force -Path $localBackupDir | Out-Null

$session = New-KioskSession `
    -RemoteHost $remoteHost `
    -AuthMode $authMode `
    -OpenTimeoutMs ([int]$openTimeout) `
    -OperationTimeoutMs ([int]$operationTimeout)

try {
    $backupInfo = Invoke-Command -Session $session -ScriptBlock {
        param($sqlInstance, $database, $remoteBackupPath)

        Add-Type -AssemblyName System.Data
        $conn = New-Object System.Data.SqlClient.SqlConnection(
            "Server=$sqlInstance;Database=master;Integrated Security=True;Connection Timeout=5;"
        )
        $conn.Open()
        try {
            $cmd = $conn.CreateCommand()
            $cmd.CommandTimeout = 600
            $safeDatabase = $database.Replace(']', ']]')
            $safeBackupPath = $remoteBackupPath.Replace("'", "''")
            $cmd.CommandText = "BACKUP DATABASE [$safeDatabase] TO DISK = N'$safeBackupPath' WITH COPY_ONLY, INIT, COMPRESSION, STATS = 10"
            try {
                $cmd.ExecuteNonQuery() | Out-Null
            } catch {
                if ($_.Exception.Message -notmatch 'COMPRESSION') {
                    throw
                }
                $cmd.CommandText = "BACKUP DATABASE [$safeDatabase] TO DISK = N'$safeBackupPath' WITH COPY_ONLY, INIT, STATS = 10"
                $cmd.ExecuteNonQuery() | Out-Null
            }
        } finally {
            $conn.Close()
        }

        $item = Get-Item -LiteralPath $remoteBackupPath
        [pscustomobject]@{
            remote_path = $item.FullName
            size_bytes = $item.Length
            last_write_time = $item.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
            host = $env:COMPUTERNAME
        }
    } -ArgumentList $sqlInstance, $database, $remoteBackupPath

    Copy-Item -FromSession $session -LiteralPath $remoteBackupPath -Destination $localBackupPath
    $localItem = Get-Item -LiteralPath $localBackupPath

    [pscustomobject]@{
        ok = $true
        generated_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        remote_host = $remoteHost
        sql_instance = $sqlInstance
        database = $database
        remote_backup = $backupInfo
        local_path = $localItem.FullName
        local_size_bytes = $localItem.Length
        local_size_mb = [Math]::Round($localItem.Length / 1MB, 2)
    } | ConvertTo-Json -Depth 6
} finally {
    Remove-PSSession -Session $session
}
