$ErrorActionPreference = 'Continue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ScriptDir

chcp 65001 | Out-Null
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

Write-Host '6KA CashException monitor'
Write-Host 'Starting live monitor. Outside business hours it will not poll the kiosk.'
$liveDir = 'C:\6KAweb'
$workspaceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$monitorDir = if (Test-Path -LiteralPath (Join-Path $liveDir 'cash_exception_monitor.py')) { $liveDir } else { $workspaceDir }
Set-Location -LiteralPath $monitorDir
python .\cash_exception_monitor.py --source agent --interval 30
