$ErrorActionPreference = 'Stop'

$session = New-PSSession -ComputerName '100.113.224.68' -Authentication Negotiate
try {
    Invoke-Command -Session $session -ScriptBlock {
        $logPath = 'C:\6KA\kiosk-agent\logs\agent.log'
        $health = $null
        $status = $null
        $healthError = $null
        $statusError = $null
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:3010/health' -TimeoutSec 5
        } catch {
            $healthError = $_.Exception.Message
        }
        try {
            $status = Invoke-RestMethod -Uri 'http://127.0.0.1:3010/api/status' -TimeoutSec 5
        } catch {
            $statusError = $_.Exception.Message
        }

        [pscustomobject]@{
            processes = @(Get-Process -Name 'KioskAgent' -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime)
            netstat3010 = @(netstat -ano | Select-String ':3010')
            firewall = @(Get-NetFirewallRule -DisplayName '6KA Kiosk Agent API' -ErrorAction SilentlyContinue | Get-NetFirewallPortFilter | Select-Object Protocol, LocalPort)
            firewallAddress = @(Get-NetFirewallRule -DisplayName '6KA Kiosk Agent API' -ErrorAction SilentlyContinue | Get-NetFirewallAddressFilter | Select-Object RemoteAddress)
            urlAcl = @(netsh http show urlacl url='http://+:3010/')
            localHealth = $health
            localHealthError = $healthError
            localStatus = $status
            localStatusError = $statusError
            logTail = if (Test-Path -LiteralPath $logPath) { @(Get-Content -LiteralPath $logPath -Tail 20) } else { @('missing log') }
        }
    } | ConvertTo-Json -Depth 12
} finally {
    Remove-PSSession $session
}
