param(
    [string]$PublishDir = '',
    [switch]$SkipStart
)

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

if (-not $PublishDir) {
    $PublishDir = Join-Path $PSScriptRoot 'outputs\ticket-pad-controller-release\publish'
}
$PublishDir = (Resolve-Path -LiteralPath $PublishDir).Path

$required = @(
    'TicketPadController.exe',
    'start-hidden.vbs',
    'start-ticket-pad-controller.bat',
    'install-ticket-pad-controller.ps1',
    'README.md',
    'macros.json',
    'static\index.html',
    'static\app.js'
)
foreach ($relative in $required) {
    $path = Join-Path $PublishDir $relative
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing publish file: $path"
    }
}

$remoteDir = 'C:\6KA\ticket-pad-controller'
$remoteExe = Join-Path $remoteDir 'TicketPadController.exe'
$remoteHiddenStarter = Join-Path $remoteDir 'start-hidden.vbs'
$taskName = '6KA Ticket Pad Controller'
$firewallName = '6KA Ticket Pad Controller'
$port = 9580
$skipStartFlag = [bool]$SkipStart.IsPresent

$session = New-KioskSession -RemoteHost $remoteHost
try {
    Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir)
        New-Item -ItemType Directory -Force -Path $remoteDir | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'static') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'backups') | Out-Null
    } -ArgumentList $remoteDir

    Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir, $remoteExe)
        $existing = Get-Process -Name 'TicketPadController' -ErrorAction SilentlyContinue | Where-Object {
            try { $_.Path -eq $remoteExe } catch { $false }
        }
        foreach ($process in $existing) {
            Stop-Process -Id $process.Id -Force
            try {
                Wait-Process -Id $process.Id -Timeout 5
            } catch {
            }
        }

        if (Test-Path -LiteralPath $remoteExe) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $backupDir = Join-Path (Join-Path $remoteDir 'backups') $stamp
            New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
            foreach ($name in @('TicketPadController.exe', 'macros.json', 'README.md', 'start-hidden.vbs', 'start-ticket-pad-controller.bat', 'install-ticket-pad-controller.ps1')) {
                $src = Join-Path $remoteDir $name
                if (Test-Path -LiteralPath $src) {
                    Copy-Item -LiteralPath $src -Destination (Join-Path $backupDir $name) -Force
                }
            }
        }
    } -ArgumentList $remoteDir, $remoteExe

    foreach ($item in Get-ChildItem -LiteralPath $PublishDir -Force) {
        Copy-Item -ToSession $session -LiteralPath $item.FullName -Destination $remoteDir -Recurse -Force
    }

    $result = Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir, $remoteExe, $remoteHiddenStarter, $taskName, $firewallName, $port, [bool]$skipStart)

        $action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\wscript.exe" -Argument "`"$remoteHiddenStarter`"" -WorkingDirectory $remoteDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Days 365) `
            -RestartCount 3 `
            -RestartInterval (New-TimeSpan -Minutes 1)

        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Force | Out-Null

        $rule = Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue
        if ($rule) {
            Remove-NetFirewallRule -DisplayName $firewallName | Out-Null
        }
        New-NetFirewallRule `
            -DisplayName $firewallName `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort $port `
            -RemoteAddress '100.64.0.0/10','LocalSubnet' | Out-Null

        if (-not $skipStart) {
            Start-ScheduledTask -TaskName $taskName
            Start-Sleep -Seconds 5
            $started = Get-Process -Name 'TicketPadController' -ErrorAction SilentlyContinue | Where-Object {
                try { $_.Path -eq $remoteExe } catch { $false }
            }
            if (-not $started) {
                & schtasks.exe /Run /TN $taskName | Out-Null
                Start-Sleep -Seconds 5
            }
        }

        $processes = @(Get-Process -Name 'TicketPadController' -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime)
        $task = Get-ScheduledTask -TaskName $taskName
        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $remoteExe
        $localState = $null
        try {
            $localState = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/state" -TimeoutSec 5
        } catch {
            $localState = [pscustomobject]@{ ok = $false; error = $_.Exception.Message }
        }

        [pscustomobject]@{
            ok = $true
            computer = $env:COMPUTERNAME
            user = "$env:USERDOMAIN\$env:USERNAME"
            remote_dir = $remoteDir
            remote_exe = $remoteExe
            sha256 = $hash.Hash
            port = $port
            task_state = $task.State.ToString()
            task_last_run_time = $taskInfo.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')
            task_last_result = $taskInfo.LastTaskResult
            processes = $processes
            local_state = $localState
            task_action = "$env:WINDIR\System32\wscript.exe `"$remoteHiddenStarter`""
        }
    } -ArgumentList $remoteDir, $remoteExe, $remoteHiddenStarter, $taskName, $firewallName, $port, $skipStartFlag

    $result | ConvertTo-Json -Depth 10
} finally {
    Remove-PSSession $session
}
