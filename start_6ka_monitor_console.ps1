$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$wt = (Get-Command wt.exe -ErrorAction SilentlyContinue).Source
if (-not $wt) {
    Write-Host 'Windows Terminal wt.exe not found.'
    Write-Host 'Please install Windows Terminal or enable the wt.exe App Execution Alias.'
    Read-Host 'Press Enter to close'
    exit 1
}

$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$utf8 = '$ErrorActionPreference=''Continue''; chcp 65001 | Out-Null; $env:PYTHONUTF8=''1''; $env:PYTHONIOENCODING=''utf-8''; $u=New-Object System.Text.UTF8Encoding($false); [Console]::InputEncoding=$u; [Console]::OutputEncoding=$u; $OutputEncoding=$u;'

$python = 'C:\Python312\python.exe'
$rpScript = 'C:\RP\rp_v5.0.py'
$kakScript = 'C:\6KAK\6kak_v2.0.py'
foreach ($path in @($python, $rpScript, $kakScript)) {
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host "Preflight failed: missing $path"
        Read-Host 'Press Enter to close'
        exit 1
    }
}

$rpVersion = & $python $rpScript --version
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Preflight failed: RP5 version check failed.'
    Read-Host 'Press Enter to close'
    exit 1
}
$kakVersion = & $python $kakScript --version
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Preflight failed: 6KAK version check failed.'
    Read-Host 'Press Enter to close'
    exit 1
}
Write-Host "Preflight OK: $rpVersion / $kakVersion"

$rpCommand = "$utf8 Set-Location -LiteralPath 'C:\RP'; & 'C:\RP\start-rp5-visible.bat'"
$kakCommand = "$utf8 Set-Location -LiteralPath 'C:\6KAK'; & 'C:\6KAK\start-6kak.bat'"
$rpEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($rpCommand))
$kakEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($kakCommand))

$arguments = @(
    '-w', '0',
    'new-tab', '--title', '6KA RP5',
    $ps, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-EncodedCommand', $rpEncoded,
    ';',
    'split-pane', '-H', '--title', '6KA 6KAK',
    $ps, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-EncodedCommand', $kakEncoded
)

Set-Location -LiteralPath $scriptDir
& $wt @arguments
