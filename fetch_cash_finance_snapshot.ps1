$ErrorActionPreference = 'Stop'

$remoteHost = '100.113.224.68'
$remoteDbPath = 'C:\Protech\Suit.Kiosk\Database\finance.db'
$snapshotDir = Join-Path $PSScriptRoot 'data\finance_cache\snapshots'
$currentDbPath = Join-Path $PSScriptRoot 'data\finance_cache\finance-current.db'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$snapshotPath = Join-Path $snapshotDir "finance-$stamp.db"

New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null

$session = New-PSSession -ComputerName $remoteHost -Authentication Negotiate
try {
    $status = Invoke-Command -Session $session -ScriptBlock {
        param($remoteDbPath)
        $info = Get-Item -LiteralPath $remoteDbPath -ErrorAction Stop
        [pscustomobject]@{
            exists = $true
            path = $info.FullName
            size_bytes = $info.Length
            last_write_time = $info.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
            last_write_time_utc = $info.LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
        }
    } -ArgumentList $remoteDbPath

    Copy-Item -FromSession $session -LiteralPath $remoteDbPath -Destination $snapshotPath
    Copy-Item -LiteralPath $snapshotPath -Destination $currentDbPath -Force

    [pscustomobject]@{
        ok = $true
        remote_host = $remoteHost
        remote = $status
        snapshot_path = $snapshotPath
        current_db_path = $currentDbPath
        local_size_bytes = (Get-Item -LiteralPath $snapshotPath).Length
    } | ConvertTo-Json -Depth 5
} finally {
    Remove-PSSession $session
}
