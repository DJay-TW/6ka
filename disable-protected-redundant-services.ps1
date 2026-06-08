$ErrorActionPreference = 'Continue'

$serviceNames = @(
    'cbdhsvc',
    'cbdhsvc_88761',
    'CDPUserSvc',
    'CDPUserSvc_88761',
    'DoSvc',
    'NPSMSvc',
    'NPSMSvc_88761',
    'OneSyncSvc',
    'OneSyncSvc_88761',
    'PimIndexMaintenanceSvc',
    'PimIndexMaintenanceSvc_88761',
    'UnistoreSvc',
    'UnistoreSvc_88761',
    'UserDataSvc',
    'UserDataSvc_88761',
    'WpnUserService',
    'WpnUserService_88761'
)

$resultPath = Join-Path $PSScriptRoot 'disabled-protected-services-20260601.csv'

$results = foreach ($name in $serviceNames) {
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$name"
    $row = [ordered]@{
        Name = $name
        RegistryPath = $regPath
        Exists = $false
        OldStart = ''
        NewStart = ''
        Result = ''
    }

    if (Test-Path -LiteralPath $regPath) {
        $row.Exists = $true
        try {
            $old = Get-ItemProperty -LiteralPath $regPath -Name Start -ErrorAction Stop
            $row.OldStart = $old.Start
        } catch {
            $row.OldStart = 'unknown'
        }

        try {
            Set-ItemProperty -LiteralPath $regPath -Name Start -Type DWord -Value 4 -ErrorAction Stop
            $new = Get-ItemProperty -LiteralPath $regPath -Name Start -ErrorAction Stop
            $row.NewStart = $new.Start
            $row.Result = 'disabled in registry'
        } catch {
            $row.Result = 'failed: ' + $_.Exception.Message
        }
    } else {
        $row.Result = 'not found'
    }

    [pscustomobject]$row
}

$results | Export-Csv -LiteralPath $resultPath -NoTypeInformation -Encoding UTF8
$results | Format-Table -AutoSize

Write-Host ''
Write-Host "Result: $resultPath"
Write-Host 'A reboot may be required for per-user services to disappear completely.'
Write-Host ''
Write-Host 'Press Enter to close...'
[void][System.Console]::ReadLine()
