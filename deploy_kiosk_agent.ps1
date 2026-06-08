$ErrorActionPreference = 'Stop'

$remoteHost = '100.113.224.68'
$localExe = Join-Path $PSScriptRoot 'KioskAgent.exe'
$remoteDir = 'C:\6KA\kiosk-agent'
$remoteExe = Join-Path $remoteDir 'KioskAgent.exe'
$remoteHiddenStarter = Join-Path $remoteDir 'start-hidden.vbs'
$taskName = '6KA Kiosk Agent'
$urlPrefix = 'http://+:3010/'
$firewallName = '6KA Kiosk Agent API'

if (-not (Test-Path -LiteralPath $localExe)) {
    throw "Missing local executable: $localExe"
}

$session = New-PSSession -ComputerName $remoteHost -Authentication Negotiate
try {
    Invoke-Command -Session $session -ScriptBlock {
        param($remoteDir)
        New-Item -ItemType Directory -Force -Path $remoteDir | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $remoteDir 'logs') | Out-Null
    } -ArgumentList $remoteDir

    Invoke-Command -Session $session -ScriptBlock {
        param($remoteExe)
        $existing = Get-Process -Name 'KioskAgent' -ErrorAction SilentlyContinue | Where-Object {
            try { $_.Path -eq $remoteExe } catch { $false }
        }
        foreach ($process in $existing) {
            Stop-Process -Id $process.Id -Force
        }
    } -ArgumentList $remoteExe

    Copy-Item -ToSession $session -LiteralPath $localExe -Destination $remoteExe

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
            -LocalPort 3010 `
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

        $processes = Get-Process -Name 'KioskAgent' -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime
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
            task_action = "$env:WINDIR\System32\wscript.exe `"$remoteHiddenStarter`""
            url_acl = $urlAclOutput -join "`n"
        }
    } -ArgumentList $remoteDir, $remoteExe, $remoteHiddenStarter, $taskName, $urlPrefix, $firewallName

    $result | ConvertTo-Json -Depth 8
} finally {
    Remove-PSSession $session
}
