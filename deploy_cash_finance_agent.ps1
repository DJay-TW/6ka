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
$localExe = Join-Path $PSScriptRoot 'CashFinanceAgent.exe'
$remoteDir = 'C:\6KA\cash-finance-agent'
$remoteExe = Join-Path $remoteDir 'CashFinanceAgent.exe'
$remoteHiddenStarter = Join-Path $remoteDir 'start-hidden.vbs'
$localSqliteDll = Join-Path $PSScriptRoot 'System.Data.SQLite.dll'
$localSqliteX64 = Join-Path $PSScriptRoot 'x64\SQLite.Interop.dll'
$localSqliteX86 = Join-Path $PSScriptRoot 'x86\SQLite.Interop.dll'
$taskName = '6KA Cash Finance Agent'
$urlPrefix = 'http://+:3012/'
$firewallName = '6KA Cash Finance Agent API'

if (-not (Test-Path -LiteralPath $localExe)) {
    throw "Missing local executable: $localExe"
}
foreach ($dependency in @($localSqliteDll, $localSqliteX64, $localSqliteX86)) {
    if (-not (Test-Path -LiteralPath $dependency)) {
        throw "Missing local dependency: $dependency"
    }
}

$session = New-KioskSession -RemoteHost $remoteHost
try {
    Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir)
        New-Item -ItemType Directory -Force -Path $remoteDir | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'logs') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'snapshots') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'x64') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'x86') | Out-Null
    } -ArgumentList $remoteDir

    Invoke-Command -Session $session -ScriptBlock {
        param($remoteExe)
        $existing = Get-Process -Name 'CashFinanceAgent' -ErrorAction SilentlyContinue | Where-Object {
            try { $_.Path -eq $remoteExe } catch { $false }
        }
        foreach ($process in $existing) {
            Stop-Process -Id $process.Id -Force
        }
    } -ArgumentList $remoteExe

    Copy-Item -ToSession $session -LiteralPath $localExe -Destination $remoteExe
    Copy-Item -ToSession $session -LiteralPath $localSqliteDll -Destination (Join-Path $remoteDir 'System.Data.SQLite.dll')
    Copy-Item -ToSession $session -LiteralPath $localSqliteX64 -Destination (Join-Path $remoteDir 'x64\SQLite.Interop.dll')
    Copy-Item -ToSession $session -LiteralPath $localSqliteX86 -Destination (Join-Path $remoteDir 'x86\SQLite.Interop.dll')

    $result = Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir, $remoteExe, $remoteHiddenStarter, $taskName, $urlPrefix, $firewallName)

        $null = & netsh http delete urlacl url=$urlPrefix 2>$null
        $urlAclOutput = & netsh http add urlacl url=$urlPrefix sddl='D:(A;;GX;;;WD)'

        $existingRule = Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue
        if ($existingRule) {
            Remove-NetFirewallRule -DisplayName $firewallName | Out-Null
        }
        New-NetFirewallRule `
            -DisplayName $firewallName `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort 3012 `
            -RemoteAddress '100.64.0.0/10' | Out-Null

        $hiddenStarterBody = @"
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "$remoteDir"
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

        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 3

        $processes = Get-Process -Name 'CashFinanceAgent' -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime
        $task = Get-ScheduledTask -TaskName $taskName
        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $remoteExe

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
            url_acl = $urlAclOutput -join "`n"
        }
    } -ArgumentList $remoteDir, $remoteExe, $remoteHiddenStarter, $taskName, $urlPrefix, $firewallName

    $result | ConvertTo-Json -Depth 8
} finally {
    Remove-PSSession $session
}
