$ErrorActionPreference = 'Stop'

$targetDir = 'C:\6KAweb'
$workerSource = Join-Path $PSScriptRoot 'sales_cache_sync_worker.py'
$workerTarget = Join-Path $targetDir 'sales_cache_sync_worker.py'
$python = $env:PYTHON_PATH
if (-not $python) { $python = 'C:\Python312\python.exe' }

if (-not (Test-Path -LiteralPath $workerSource)) {
    throw "Missing worker source: $workerSource"
}
if (-not (Test-Path -LiteralPath $targetDir)) {
    throw "Missing target dir: $targetDir"
}

Copy-Item -LiteralPath $workerSource -Destination $workerTarget -Force

$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*sales_cache_sync_worker.py*'
}
foreach ($process in $existing) {
    Stop-Process -Id $process.ProcessId -Force
}

$started = Start-Process -FilePath $python -ArgumentList 'sales_cache_sync_worker.py' -WorkingDirectory $targetDir -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3

$processes = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*sales_cache_sync_worker.py*'
} | Select-Object ProcessId, Name, CommandLine

[pscustomobject]@{
    ok = $true
    worker = $workerTarget
    start_method = 'Start-Process'
    started_pid = $started.Id
    processes = $processes
} | ConvertTo-Json -Depth 8
