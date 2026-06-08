<# 
Dry-run deploy checklist for 6KA.
This script intentionally does not deploy, copy files, restart services, or edit
runtime state. It only prints the deployment gates that must be satisfied first.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

Write-Host '6KA deploy dry-run'
Write-Host ''
Write-Host 'No deployment actions are implemented or executed by this script.'
Write-Host ''
Write-Host 'Deployment gates:'
Write-Host '  1. Identify component: WEB, RP5, 6KAK, Pi Agent, KioskAgent, CashFinanceAgent, TicketPadController.'
Write-Host '  2. Confirm source file path and deployed target path.'
Write-Host '  3. Record current live process command line and recent logs.'
Write-Host '  4. Prepare rollback copy or exact restore command.'
Write-Host '  5. Get explicit user approval for deployment and restart, if restart is required.'
Write-Host '  6. Run post-deploy read-only health checks.'
Write-Host ''
Write-Host 'Forbidden in this skeleton: copy, move, service restart, scheduled task edit, DB write, ticket-machine control.'

exit 0
