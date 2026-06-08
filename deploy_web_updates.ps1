$ErrorActionPreference = 'Stop'

$sourceDir = $PSScriptRoot
$targetDir = 'C:\6KAweb'
$backupDir = Join-Path $targetDir ('backup-before-sqlite-agent-{0}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

if (-not (Test-Path -LiteralPath $targetDir)) {
    throw "Missing target WEB directory: $targetDir"
}

New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $targetDir 'public') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $targetDir 'data') | Out-Null

Copy-Item -LiteralPath (Join-Path $targetDir 'server.js') -Destination (Join-Path $backupDir 'server.js') -Force
Copy-Item -LiteralPath (Join-Path $targetDir 'public\index.html') -Destination (Join-Path $backupDir 'index.html') -Force
if (Test-Path -LiteralPath (Join-Path $targetDir 'cash_exception_monitor.py')) {
    Copy-Item -LiteralPath (Join-Path $targetDir 'cash_exception_monitor.py') -Destination (Join-Path $backupDir 'cash_exception_monitor.py') -Force
}
if (Test-Path -LiteralPath (Join-Path $targetDir 'cashbox_estimator.py')) {
    Copy-Item -LiteralPath (Join-Path $targetDir 'cashbox_estimator.py') -Destination (Join-Path $backupDir 'cashbox_estimator.py') -Force
}

Copy-Item -LiteralPath (Join-Path $sourceDir 'server.js') -Destination (Join-Path $targetDir 'server.js') -Force
Copy-Item -LiteralPath (Join-Path $sourceDir 'index.html') -Destination (Join-Path $targetDir 'public\index.html') -Force
Copy-Item -LiteralPath (Join-Path $sourceDir 'cash_exception_monitor.py') -Destination (Join-Path $targetDir 'cash_exception_monitor.py') -Force
Copy-Item -LiteralPath (Join-Path $sourceDir 'cashbox_estimator.py') -Destination (Join-Path $targetDir 'cashbox_estimator.py') -Force
Copy-Item -LiteralPath (Join-Path $sourceDir 'query_sales_cache.py') -Destination (Join-Path $targetDir 'query_sales_cache.py') -Force
Copy-Item -LiteralPath (Join-Path $sourceDir 'data\sales_cache.sqlite') -Destination (Join-Path $targetDir 'data\sales_cache.sqlite') -Force

[pscustomobject]@{
    ok = $true
    target = $targetDir
    backup = $backupDir
    files = @(
        'server.js',
        'public\index.html',
        'cash_exception_monitor.py',
        'cashbox_estimator.py',
        'query_sales_cache.py',
        'data\sales_cache.sqlite'
    )
} | ConvertTo-Json -Depth 4
