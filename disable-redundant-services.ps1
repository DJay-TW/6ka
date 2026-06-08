$ErrorActionPreference = 'Continue'

$patterns = @(
    'DiagTrack',
    'WSearch',
    'SysMain',
    'DoSvc',
    'Spooler',
    'lfsvc',
    'PimIndexMaintenanceSvc_*',
    'OneSyncSvc_*',
    'UserDataSvc_*',
    'UnistoreSvc_*',
    'WpnUserService_*',
    'WpnService',
    'CDPUserSvc_*',
    'CDPSvc',
    'cbdhsvc_*',
    'NPSMSvc_*',
    'wcncsvc',
    'SSDPSRV',
    'upnphost',
    'McAfee WebAdvisor',
    'XblAuthManager',
    'XblGameSave',
    'XboxGipSvc',
    'XboxNetApiSvc'
)

$backupPath = Join-Path $PSScriptRoot 'service-startup-backup-admin-20260601.csv'
$resultPath = Join-Path $PSScriptRoot 'disabled-services-admin-20260601.csv'

Get-Service |
    Select-Object Name,DisplayName,Status,StartType |
    Export-Csv -LiteralPath $backupPath -NoTypeInformation -Encoding UTF8

$targets = foreach ($pattern in $patterns) {
    Get-Service -Name $pattern -ErrorAction SilentlyContinue
}

$targets = $targets | Sort-Object Name -Unique

$results = foreach ($svc in $targets) {
    $row = [ordered]@{
        Name            = $svc.Name
        DisplayName     = $svc.DisplayName
        BeforeStatus    = $svc.Status
        BeforeStartType = $svc.StartType
        StopResult      = ''
        DisableResult   = ''
        AfterStatus     = ''
        AfterStartType  = ''
    }

    try {
        if ($svc.Status -ne 'Stopped') {
            Stop-Service -Name $svc.Name -Force -ErrorAction Stop
            $row.StopResult = 'stopped'
        } else {
            $row.StopResult = 'already stopped'
        }
    } catch {
        $row.StopResult = 'failed: ' + $_.Exception.Message
    }

    try {
        Set-Service -Name $svc.Name -StartupType Disabled -ErrorAction Stop
        $row.DisableResult = 'disabled'
    } catch {
        $row.DisableResult = 'failed: ' + $_.Exception.Message
    }

    $after = Get-Service -Name $svc.Name -ErrorAction SilentlyContinue
    if ($after) {
        $row.AfterStatus = $after.Status
        $row.AfterStartType = $after.StartType
    }

    [pscustomobject]$row
}

$results | Export-Csv -LiteralPath $resultPath -NoTypeInformation -Encoding UTF8
$results | Format-Table -AutoSize

Write-Host ''
Write-Host "Backup: $backupPath"
Write-Host "Result: $resultPath"
Write-Host ''
Write-Host 'Press Enter to close...'
[void][System.Console]::ReadLine()
