$ErrorActionPreference = 'Stop'

$logPath = Join-Path $PSScriptRoot 'verify-6ka-web-startup-task.log'
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] verify, admin=$(([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))" | Out-File -LiteralPath $logPath -Encoding UTF8

try {
    Get-ScheduledTask -TaskName '6KA Web Server' |
        Select-Object TaskName,TaskPath,State,
            @{Name='Action';Expression={$_.Actions.Execute + ' ' + $_.Actions.Arguments}},
            @{Name='TriggerCount';Expression={$_.Triggers.Count}} |
        Format-List |
        Out-String |
        Add-Content -LiteralPath $logPath -Encoding UTF8

    Get-ScheduledTaskInfo -TaskName '6KA Web Server' |
        Select-Object LastRunTime,LastTaskResult,NextRunTime,NumberOfMissedRuns |
        Format-List |
        Out-String |
        Add-Content -LiteralPath $logPath -Encoding UTF8

    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] verify ok" | Add-Content -LiteralPath $logPath -Encoding UTF8
} catch {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] verify failed: $($_.Exception.Message)" | Add-Content -LiteralPath $logPath -Encoding UTF8
    throw
}
