$ErrorActionPreference = 'Stop'

$targetDir = 'C:\6KAweb'
$workerSource = Join-Path $PSScriptRoot 'cash_finance_sync_worker.py'
$auditSource = Join-Path $PSScriptRoot 'cash_finance_audit.py'
$pushSource = Join-Path $PSScriptRoot 'cash_diff_cloudflare_push.py'
$tokenSource = Join-Path $PSScriptRoot 'cash_diff_push_token.local.txt'
$workerTarget = Join-Path $targetDir 'cash_finance_sync_worker.py'
$auditTarget = Join-Path $targetDir 'cash_finance_audit.py'
$pushTarget = Join-Path $targetDir 'cash_diff_cloudflare_push.py'
$tokenTarget = Join-Path $targetDir 'cash_diff_push_token.local.txt'
$taskName = '6KA Cash Finance Sync'
$python = $env:PYTHON_PATH
if (-not $python) { $python = 'C:\Python312\python.exe' }
$pythonw = $env:PYTHONW_PATH
if (-not $pythonw -and $python.EndsWith('python.exe', [System.StringComparison]::OrdinalIgnoreCase)) {
    $candidate = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    if (Test-Path -LiteralPath $candidate) {
        $pythonw = $candidate
    }
}
if (-not $pythonw) { $pythonw = $python }

foreach ($source in @($workerSource, $auditSource, $pushSource)) {
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing source: $source"
    }
}
if (-not (Test-Path -LiteralPath $targetDir)) {
    throw "Missing target dir: $targetDir"
}

Copy-Item -LiteralPath $workerSource -Destination $workerTarget -Force
Copy-Item -LiteralPath $auditSource -Destination $auditTarget -Force
Copy-Item -LiteralPath $pushSource -Destination $pushTarget -Force
if (Test-Path -LiteralPath $tokenSource) {
    Copy-Item -LiteralPath $tokenSource -Destination $tokenTarget -Force
}

$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*cash_finance_sync_worker.py*'
}
foreach ($process in $existing) {
    Stop-Process -Id $process.ProcessId -Force
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument 'cash_finance_sync_worker.py' -WorkingDirectory $targetDir
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

$processes = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*cash_finance_sync_worker.py*'
} | Select-Object ProcessId, Name, CommandLine

$task = Get-ScheduledTask -TaskName $taskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName

[pscustomobject]@{
    ok = $true
    worker = $workerTarget
    audit = $auditTarget
    push_helper = $pushTarget
    token_file_present = (Test-Path -LiteralPath $tokenTarget)
    task_name = $taskName
    task_state = $task.State.ToString()
    task_last_run_time = $taskInfo.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')
    task_last_result = $taskInfo.LastTaskResult
    processes = $processes
} | ConvertTo-Json -Depth 8
