<# 
Dry-run ticket-machine checklist for 6KA.
This script prints checks only. It does not use WinRM, HTTP, UI automation,
printing, ordering, payment, reboot, or deployment actions.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

Write-Host '6KA ticket-machine check dry-run'
Write-Host ''
Write-Host 'No ticket-machine connection or control action is executed by this script.'
Write-Host ''
Write-Host 'Suggested read-only checks after approval:'
Write-Host '  1. Confirm whether kiosk host checks are allowed for this session.'
Write-Host '  2. If allowed, check KioskAgent /health and /api/status only.'
Write-Host '  3. If cross-host checks fail, ask before using WinRM or machine-local 127.0.0.1 checks.'
Write-Host '  4. Inspect logs without changing scheduled tasks, services, DB, or UI state.'
Write-Host ''
Write-Host 'Forbidden without explicit approval: UI clicks, printing, orders, payments, DB writes, restart, deploy.'

exit 0
