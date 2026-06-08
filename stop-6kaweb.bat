@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$targets = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like '*C:\6KAweb\server.js*' }; if (-not $targets) { Write-Host '6KAweb is not running.'; exit 0 }; $targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Stopped 6KAweb PID ' + $_.ProcessId) }"
