$ErrorActionPreference = 'Stop'

$logPath = Join-Path $PSScriptRoot 'install-6ka-web-startup-task.log'
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] starting install, admin=$(([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))" | Out-File -LiteralPath $logPath -Encoding UTF8

try {
    $taskName = '6KA Web Server'
    $action = New-ScheduledTaskAction `
        -Execute 'C:\Windows\System32\cmd.exe' `
        -Argument '/c "C:\6KAweb\start-6kaweb.bat"' `
        -WorkingDirectory 'C:\6KAweb'

    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT30S'

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    $principal = New-ScheduledTaskPrincipal `
        -UserId 'SYSTEM' `
        -LogonType ServiceAccount `
        -RunLevel Highest

    $task = New-ScheduledTask `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description 'Start 6KA web dashboard at boot via C:\6KAweb\start-6kaweb.bat'

    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-String | Add-Content -LiteralPath $logPath -Encoding UTF8
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 3
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State,TaskPath | Format-List | Out-String | Add-Content -LiteralPath $logPath -Encoding UTF8
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] install ok" | Add-Content -LiteralPath $logPath -Encoding UTF8
} catch {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] install failed: $($_.Exception.Message)" | Add-Content -LiteralPath $logPath -Encoding UTF8
    throw
}
