$ErrorActionPreference = 'Stop'

$session = New-PSSession -ComputerName '100.113.224.68' -Authentication Negotiate
try {
    Invoke-Command -Session $session -ScriptBlock {
        $taskName = '6KA Kiosk Agent'
        $task = Get-ScheduledTask -TaskName $taskName
        $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        $processes = Get-Process -Name 'KioskAgent' -ErrorAction SilentlyContinue |
            Select-Object Id, ProcessName, Path, StartTime

        [pscustomobject]@{
            task_name = $task.TaskName
            task_state = $task.State.ToString()
            task_action_execute = $task.Actions.Execute
            task_action_arguments = $task.Actions.Arguments
            task_last_run_time = $taskInfo.LastRunTime
            task_last_result = $taskInfo.LastTaskResult
            processes = $processes
            hidden_starter_exists = Test-Path -LiteralPath 'C:\6KA\kiosk-agent\start-hidden.vbs'
            hidden_starter = if (Test-Path -LiteralPath 'C:\6KA\kiosk-agent\start-hidden.vbs') {
                Get-Content -LiteralPath 'C:\6KA\kiosk-agent\start-hidden.vbs' -Raw
            } else {
                ''
            }
        }
    } | ConvertTo-Json -Depth 8
} finally {
    Remove-PSSession $session
}
