@echo off
setlocal
set "APP_DIR=C:\6KAweb"
set "NODE=C:\Program Files\nodejs\node.exe"
set "SERVER_JS=C:\6KAweb\server.js"

powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like '*C:\6KAweb\server.js*' }; if ($p) { exit 0 }; Start-Process -FilePath $env:NODE -ArgumentList $env:SERVER_JS -WorkingDirectory $env:APP_DIR -WindowStyle Hidden"
