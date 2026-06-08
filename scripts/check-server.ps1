<# 
Dry-run server checklist for 6KA.
This script prints read-only checks to run manually. It does not call endpoints,
restart services, copy files, or modify databases.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

Write-Host '6KA server check dry-run'
Write-Host ''
Write-Host 'No live endpoint calls are executed by this script.'
Write-Host ''
Write-Host 'Suggested read-only checks after approval:'
Write-Host '  1. Confirm running WEB process and command line.'
Write-Host '  2. GET /health on the WEB server.'
Write-Host '  3. GET /api/server/status on the WEB server.'
Write-Host '  4. GET /api/pi/status if Pi status is in scope.'
Write-Host '  5. Inspect latest logs under logs/ or deployed C:\6KAweb\logs.'
Write-Host '  6. Inspect sync_state.json files without editing them.'
Write-Host ''
Write-Host 'Forbidden without explicit approval: deploy, restart, DB writes, ticket-machine control.'

exit 0
