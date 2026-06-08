$ErrorActionPreference = 'Continue'

try { chcp 65001 | Out-Null } catch {}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$utf8 = New-Object System.Text.UTF8Encoding($false)
try { [Console]::InputEncoding = $utf8 } catch {}
try { [Console]::OutputEncoding = $utf8 } catch {}
$OutputEncoding = $utf8

$logDir = 'C:\RP_log'
$logPath = Join-Path $logDir 'rp5_console.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$maxBytes = 5MB
$backupCount = 2
if ($env:RP_CONSOLE_LOG_MAX_BYTES) {
    $parsedMaxBytes = 0L
    if ([Int64]::TryParse($env:RP_CONSOLE_LOG_MAX_BYTES, [ref]$parsedMaxBytes) -and $parsedMaxBytes -gt 0) {
        $maxBytes = $parsedMaxBytes
    }
}
if ($env:RP_CONSOLE_LOG_BACKUP_COUNT) {
    $parsedBackupCount = 0
    if ([Int32]::TryParse($env:RP_CONSOLE_LOG_BACKUP_COUNT, [ref]$parsedBackupCount) -and $parsedBackupCount -gt 0) {
        $backupCount = $parsedBackupCount
    }
}

function New-ConsoleLogWriter {
    New-Object System.IO.StreamWriter($logPath, $false, $utf8)
}

function Write-ConsoleOutput($line) {
    try {
        Write-Host $line
    } catch {
    }
}

function Rotate-ConsoleLogIfNeeded {
    if (-not $script:writer -or $script:writer.BaseStream.Length -lt $maxBytes) {
        return
    }

    $script:writer.Flush()
    $script:writer.Close()

    $oldest = "$logPath.$backupCount"
    if (Test-Path -LiteralPath $oldest) {
        Remove-Item -LiteralPath $oldest -Force
    }
    for ($index = $backupCount - 1; $index -ge 1; $index--) {
        $source = "$logPath.$index"
        $target = "$logPath.$($index + 1)"
        if (Test-Path -LiteralPath $source) {
            Move-Item -LiteralPath $source -Destination $target -Force
        }
    }
    if (Test-Path -LiteralPath $logPath) {
        Move-Item -LiteralPath $logPath -Destination "$logPath.1" -Force
    }

    $script:writer = New-ConsoleLogWriter
    $rotatedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $rotateLine = "[$rotatedAt] RP5 console log rotated. max_bytes=$maxBytes backups=$backupCount"
    Write-ConsoleOutput $rotateLine
    $script:writer.WriteLine($rotateLine)
    $script:writer.Flush()
}

function Write-ConsoleLogLine($line) {
    Write-ConsoleOutput $line
    $script:writer.WriteLine($line)
    $script:writer.Flush()
    Rotate-ConsoleLogIfNeeded
}

Set-Location -LiteralPath 'C:\RP'
$script:writer = New-ConsoleLogWriter
try {
    $startedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $startLine = "[$startedAt] RP5 console started. command=C:\Python312\python.exe -u C:\RP\rp_v5.0.py"
    Write-ConsoleLogLine $startLine

    & 'C:\Python312\python.exe' -u 'C:\RP\rp_v5.0.py' 2>&1 | ForEach-Object {
        $line = [string]$_
        Write-ConsoleLogLine $line
    }

    $exitCode = if ($LASTEXITCODE -ne $null) { $LASTEXITCODE } else { 0 }
    $stoppedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $stopLine = "[$stoppedAt] RP5 console exited. exit_code=$exitCode"
    Write-ConsoleLogLine $stopLine
} finally {
    if ($script:writer) {
        $script:writer.Close()
    }
}
exit $exitCode
