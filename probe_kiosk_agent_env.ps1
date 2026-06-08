$ErrorActionPreference = 'Stop'

$session = New-PSSession -ComputerName '100.113.224.68' -Authentication Negotiate
try {
    Invoke-Command -Session $session -ScriptBlock {
        [pscustomobject]@{
            ComputerName = $env:COMPUTERNAME
            User = $env:USERNAME
            Python = (Get-Command python -ErrorAction SilentlyContinue).Source
            Py = (Get-Command py -ErrorAction SilentlyContinue).Source
            Tailscale = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
            Temp = $env:TEMP
        }
    } | ConvertTo-Json -Depth 4
} finally {
    Remove-PSSession $session
}
