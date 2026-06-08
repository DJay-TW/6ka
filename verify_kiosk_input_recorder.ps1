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
    param([string]$RemoteHost)

    $openTimeout = [Environment]::GetEnvironmentVariable('RP_WINRM_OPEN_TIMEOUT_MS')
    if (-not $openTimeout) { $openTimeout = '5000' }

    $operationTimeout = [Environment]::GetEnvironmentVariable('RP_WINRM_OPERATION_TIMEOUT_MS')
    if (-not $operationTimeout) { $operationTimeout = '10000' }

    $sessionOption = New-PSSessionOption -OpenTimeout ([int]$openTimeout) -OperationTimeout ([int]$operationTimeout)
    $sessionArgs = @{
        ComputerName  = $RemoteHost
        SessionOption = $sessionOption
    }

    $authMode = [Environment]::GetEnvironmentVariable('KIOSK_AUTH_MODE')
    if (-not $authMode) { $authMode = 'password' }
    $authMode = $authMode.ToLowerInvariant()

    if ($authMode -eq 'cmdkey') {
        $sessionArgs.Authentication = 'Negotiate'
    } else {
        $remoteUser = [Environment]::GetEnvironmentVariable('KIOSK_USER')
        $remotePassword = [Environment]::GetEnvironmentVariable('KIOSK_PASSWORD')
        if (-not $remoteUser -or -not $remotePassword) {
            throw 'Missing KIOSK_USER or KIOSK_PASSWORD for password auth mode.'
        }
        $securePassword = ConvertTo-SecureString $remotePassword -AsPlainText -Force
        $sessionArgs.Credential = New-Object System.Management.Automation.PSCredential($remoteUser, $securePassword)
        $sessionArgs.Authentication = 'Negotiate'
    }

    New-PSSession @sessionArgs
}

Load-DotEnv -Path (Join-Path $PSScriptRoot '.env')

$remoteHost = [Environment]::GetEnvironmentVariable('KIOSK_HOST')
if (-not $remoteHost) { $remoteHost = '100.113.224.68' }

$session = New-KioskSession -RemoteHost $remoteHost
try {
    $result = Invoke-Command -Session $session -ScriptBlock {
        $remoteDir = 'C:\6KA\input-recorder'
        $today = Get-Date -Format 'yyyy-MM-dd'
        $logPath = Join-Path (Join-Path $remoteDir 'logs') ("input-$today.jsonl")
        $screenshotDir = Join-Path (Join-Path $remoteDir 'screenshots') $today
        $statePath = Join-Path $remoteDir 'input_recorder_state.json'

        $state = $null
        if (Test-Path -LiteralPath $statePath) {
            $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        }

        $recentLog = @()
        if (Test-Path -LiteralPath $logPath) {
            $recentLog = @(Get-Content -LiteralPath $logPath -Tail 10 -Encoding UTF8 | ForEach-Object { [string]$_ })
        }

        $screenshots = @()
        if (Test-Path -LiteralPath $screenshotDir) {
            $screenshots = @(Get-ChildItem -LiteralPath $screenshotDir -Filter *.png -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 10 FullName, LastWriteTime, Length)
        }

        [pscustomobject]@{
            ok = $true
            computer = $env:COMPUTERNAME
            user = "$env:USERDOMAIN\$env:USERNAME"
            processes = @(Get-Process -Name 'KioskInputRecorder' -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime)
            task = Get-ScheduledTask -TaskName '6KA Kiosk Input Recorder' -ErrorAction SilentlyContinue |
                Select-Object TaskName, State
            task_info = Get-ScheduledTaskInfo -TaskName '6KA Kiosk Input Recorder' -ErrorAction SilentlyContinue |
                Select-Object LastRunTime, LastTaskResult, NextRunTime
            state = $state
            today_log = $logPath
            today_log_exists = Test-Path -LiteralPath $logPath
            recent_log = $recentLog
            screenshot_dir = $screenshotDir
            screenshot_count = @($screenshots).Count
            recent_screenshots = $screenshots
            retained_logs = @(Get-ChildItem -LiteralPath (Join-Path $remoteDir 'logs') -Filter 'input-*.jsonl' -ErrorAction SilentlyContinue |
                Select-Object Name, LastWriteTime, Length)
            retained_screenshot_dirs = @(Get-ChildItem -LiteralPath (Join-Path $remoteDir 'screenshots') -Directory -ErrorAction SilentlyContinue |
                Select-Object Name, LastWriteTime)
        }
    }

    $result | ConvertTo-Json -Depth 10
} finally {
    Remove-PSSession $session
}
