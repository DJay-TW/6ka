param(
    [string]$RemoteHost = '',
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

function Resolve-Csc {
    $candidates = @(
        "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw 'Cannot find .NET Framework csc.exe.'
}

function New-KioskSession {
    param([string]$ComputerName)

    $openTimeout = [Environment]::GetEnvironmentVariable('RP_WINRM_OPEN_TIMEOUT_MS')
    if (-not $openTimeout) { $openTimeout = '5000' }

    $operationTimeout = [Environment]::GetEnvironmentVariable('RP_WINRM_OPERATION_TIMEOUT_MS')
    if (-not $operationTimeout) { $operationTimeout = '10000' }

    $sessionOption = New-PSSessionOption -OpenTimeout ([int]$openTimeout) -OperationTimeout ([int]$operationTimeout)
    $sessionArgs = @{
        ComputerName  = $ComputerName
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

if (-not $RemoteHost) {
    $RemoteHost = [Environment]::GetEnvironmentVariable('KIOSK_HOST')
}
if (-not $RemoteHost) {
    $RemoteHost = '100.113.224.68'
}

$sourcePath = Join-Path $PSScriptRoot 'KioskMonitorAgent.cs'
$releaseRoot = Join-Path $PSScriptRoot 'outputs\kiosk-monitor-agent-release'
$publishDir = Join-Path $releaseRoot 'publish'
$exePath = Join-Path $publishDir 'KioskMonitorAgent.exe'
$port = 9581

if (Test-Path -LiteralPath $releaseRoot) {
    Remove-Item -LiteralPath $releaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $publishDir | Out-Null

$csc = Resolve-Csc
& $csc /nologo /target:winexe /platform:anycpu /optimize+ /reference:System.Web.Extensions.dll /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/out:$exePath" "$sourcePath"
if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE"
}

$hiddenStarter = @'
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir
shell.Environment("PROCESS")("KIOSK_MONITOR_PORT") = "9581"
shell.Run Chr(34) & fso.BuildPath(appDir, "KioskMonitorAgent.exe") & Chr(34), 0, False
'@
Set-Content -LiteralPath (Join-Path $publishDir 'start-hidden.vbs') -Value $hiddenStarter -Encoding ASCII

$readme = @'
# Kiosk Monitor Agent

Read-only local/Tailscale monitor channel for the kiosk.

- Port: 9581
- Health: /health
- Status JSON: /api/status
- Screenshot PNG: /api/screenshot?max_width=720
- Processes JSON: /api/processes
- Visible windows JSON: /api/windows
- Desktop file list JSON: /api/desktop
- Allowlisted log tail JSON: /api/logs?name=ticket-pad&lines=80

Screenshots are streamed from memory and are not stored on the kiosk.
'@
Set-Content -LiteralPath (Join-Path $publishDir 'README.md') -Value $readme -Encoding UTF8

$remoteDir = 'C:\6KA\kiosk-monitor-agent'
$remoteExe = Join-Path $remoteDir 'KioskMonitorAgent.exe'
$remoteHiddenStarter = Join-Path $remoteDir 'start-hidden.vbs'
$taskName = '6KA Kiosk Monitor Agent'
$firewallName = '6KA Kiosk Monitor Agent'
$skipStartFlag = [bool]$SkipStart.IsPresent

$session = New-KioskSession -ComputerName $RemoteHost
try {
    Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir)
        New-Item -ItemType Directory -Force -Path $remoteDir | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'backups') | Out-Null
    } -ArgumentList $remoteDir

    Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir, $remoteExe)
        $existing = Get-Process -Name 'KioskMonitorAgent' -ErrorAction SilentlyContinue | Where-Object {
            try { $_.Path -eq $remoteExe } catch { $false }
        }
        foreach ($process in $existing) {
            Stop-Process -Id $process.Id -Force
            try { Wait-Process -Id $process.Id -Timeout 5 } catch {}
        }

        if (Test-Path -LiteralPath $remoteExe) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $backupDir = Join-Path (Join-Path $remoteDir 'backups') $stamp
            New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
            foreach ($name in @('KioskMonitorAgent.exe', 'start-hidden.vbs', 'README.md')) {
                $src = Join-Path $remoteDir $name
                if (Test-Path -LiteralPath $src) {
                    Copy-Item -LiteralPath $src -Destination (Join-Path $backupDir $name) -Force
                }
            }
        }
    } -ArgumentList $remoteDir, $remoteExe

    foreach ($item in Get-ChildItem -LiteralPath $publishDir -Force) {
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
            Start-Sleep -Seconds 4
        }

        $processes = @(Get-Process -Name 'KioskMonitorAgent' -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,Path,StartTime)
        $task = Get-ScheduledTask -TaskName $taskName
        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $remoteExe
        $localStatus = $null
        try {
            $localStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/status" -TimeoutSec 5
        } catch {
            $localStatus = [pscustomobject]@{ ok = $false; error = $_.Exception.Message }
        }

        [pscustomobject]@{
            ok = $true
            computer = $env:COMPUTERNAME
            remote_dir = $remoteDir
            remote_exe = $remoteExe
            sha256 = $hash.Hash
            port = $port
            task_state = $task.State.ToString()
            task_last_run_time = $taskInfo.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')
            task_last_result = $taskInfo.LastTaskResult
            task_action = "$env:WINDIR\System32\wscript.exe `"$remoteHiddenStarter`""
            processes = $processes
            local_status = $localStatus
        }
    } -ArgumentList $remoteDir, $remoteExe, $remoteHiddenStarter, $taskName, $firewallName, $port, $skipStartFlag

    $result | ConvertTo-Json -Depth 10
} finally {
    Remove-PSSession $session
}
