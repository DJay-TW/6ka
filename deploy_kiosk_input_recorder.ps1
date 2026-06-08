param(
    [switch]$NoBuild,
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

function Resolve-FrameworkReference {
    param([string]$Name)

    $candidates = @(
        "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\WPF\$Name",
        "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\WPF\$Name",
        "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\$Name",
        "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\$Name"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $Name
}

function Build-Recorder {
    param(
        [string]$SourcePath,
        [string]$OutputPath
    )

    $csc = Resolve-Csc
    $refs = @(
        '/reference:System.Windows.Forms.dll',
        '/reference:System.Drawing.dll',
        '/reference:System.Web.Extensions.dll',
        ('/reference:' + (Resolve-FrameworkReference 'UIAutomationClient.dll')),
        ('/reference:' + (Resolve-FrameworkReference 'UIAutomationTypes.dll')),
        ('/reference:' + (Resolve-FrameworkReference 'WindowsBase.dll'))
    )
    & $csc /nologo /target:winexe /platform:anycpu /optimize+ "/out:$OutputPath" @refs "$SourcePath"
    if ($LASTEXITCODE -ne 0) {
        throw "Build failed with exit code $LASTEXITCODE"
    }
}

Load-DotEnv -Path (Join-Path $PSScriptRoot '.env')

$remoteHost = [Environment]::GetEnvironmentVariable('KIOSK_HOST')
if (-not $remoteHost) { $remoteHost = '100.113.224.68' }

$localSource = Join-Path $PSScriptRoot 'KioskInputRecorder.cs'
$localExe = Join-Path $PSScriptRoot 'KioskInputRecorder.exe'
$remoteDir = 'C:\6KA\input-recorder'
$remoteExe = Join-Path $remoteDir 'KioskInputRecorder.exe'
$remoteHiddenStarter = Join-Path $remoteDir 'start-hidden.vbs'
$taskName = '6KA Kiosk Input Recorder'

if (-not (Test-Path -LiteralPath $localSource)) {
    throw "Missing local source: $localSource"
}

if (-not $NoBuild) {
    Build-Recorder -SourcePath $localSource -OutputPath $localExe
}

if (-not (Test-Path -LiteralPath $localExe)) {
    throw "Missing local executable: $localExe"
}

$skipStartFlag = [bool]$SkipStart.IsPresent

$session = New-KioskSession -RemoteHost $remoteHost
try {
    Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir)
        New-Item -ItemType Directory -Force -Path $remoteDir | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'logs') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'screenshots') | Out-Null
    } -ArgumentList $remoteDir

    Invoke-Command -Session $session -ScriptBlock {
        param($remoteExe)
        $existing = Get-Process -Name 'KioskInputRecorder' -ErrorAction SilentlyContinue | Where-Object {
            try { $_.Path -eq $remoteExe } catch { $false }
        }
        foreach ($process in $existing) {
            Stop-Process -Id $process.Id -Force
            try {
                Wait-Process -Id $process.Id -Timeout 5
            } catch {
            }
        }
    } -ArgumentList $remoteExe

    Copy-Item -ToSession $session -LiteralPath $localExe -Destination $remoteExe

    $result = Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir, $remoteExe, $remoteHiddenStarter, $taskName, [bool]$skipStart)

        $hiddenStarterBody = @"
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "$remoteDir"
shell.Environment("PROCESS")("INPUT_RECORDER_LOG_TEXT_KEYS") = "1"
shell.Run Chr(34) & "$remoteExe" & Chr(34), 0, False
"@
        Set-Content -LiteralPath $remoteHiddenStarter -Value $hiddenStarterBody -Encoding ASCII

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

        if (-not $skipStart) {
            Start-ScheduledTask -TaskName $taskName
            Start-Sleep -Seconds 5
            $started = Get-Process -Name 'KioskInputRecorder' -ErrorAction SilentlyContinue | Where-Object {
                try { $_.Path -eq $remoteExe } catch { $false }
            }
            if (-not $started) {
                & schtasks.exe /Run /TN $taskName | Out-Null
                Start-Sleep -Seconds 5
            }
        }

        $processes = Get-Process -Name 'KioskInputRecorder' -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime
        $task = Get-ScheduledTask -TaskName $taskName
        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $remoteExe
        $statePath = Join-Path $remoteDir 'input_recorder_state.json'
        $state = $null
        if (Test-Path -LiteralPath $statePath) {
            $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        $today = Get-Date -Format 'yyyy-MM-dd'
        $logPath = Join-Path (Join-Path $remoteDir 'logs') ("input-$today.jsonl")
        $recentLog = @()
        if (Test-Path -LiteralPath $logPath) {
            $recentLog = @(Get-Content -LiteralPath $logPath -Tail 5 -Encoding UTF8 | ForEach-Object { [string]$_ })
        }

        [pscustomobject]@{
            ok = $true
            computer = $env:COMPUTERNAME
            user = "$env:USERDOMAIN\$env:USERNAME"
            remote_exe = $remoteExe
            sha256 = $hash.Hash
            task_state = $task.State.ToString()
            task_last_run_time = $taskInfo.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')
            task_last_result = $taskInfo.LastTaskResult
            processes = $processes
            state = $state
            today_log = $logPath
            recent_log = $recentLog
            task_action = "$env:WINDIR\System32\wscript.exe `"$remoteHiddenStarter`""
            text_key_logging = $true
        }
    } -ArgumentList $remoteDir, $remoteExe, $remoteHiddenStarter, $taskName, $skipStartFlag

    $result | ConvertTo-Json -Depth 10
} finally {
    Remove-PSSession $session
}
