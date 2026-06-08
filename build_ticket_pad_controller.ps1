param(
    [string]$SourcePackage = 'C:\Users\88698\Downloads\ticket-pad-ui-20260607-220947.zip',
    [string]$ReleaseRoot = ''
)

$ErrorActionPreference = 'Stop'

function Resolve-Csc {
    $candidates = @(
        "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw 'Cannot find .NET Framework csc.exe.'
}

if (-not (Test-Path -LiteralPath $SourcePackage)) {
    throw "Missing source package: $SourcePackage"
}

if (-not $ReleaseRoot) {
    $ReleaseRoot = Join-Path $PSScriptRoot 'outputs\ticket-pad-controller-release'
}

$extractDir = Join-Path $ReleaseRoot 'payload'
$publishDir = Join-Path $ReleaseRoot 'publish'
$exePath = Join-Path $publishDir 'TicketPadController.exe'
$sourcePath = Join-Path $PSScriptRoot 'TicketPadController.cs'

if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Missing source: $sourcePath"
}

if (Test-Path -LiteralPath $ReleaseRoot) {
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
New-Item -ItemType Directory -Force -Path $publishDir | Out-Null
Expand-Archive -LiteralPath $SourcePackage -DestinationPath $extractDir -Force

Copy-Item -LiteralPath (Join-Path $extractDir 'static') -Destination (Join-Path $publishDir 'static') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $extractDir 'macros.json') -Destination (Join-Path $publishDir 'macros.json') -Force

$csc = Resolve-Csc
& $csc /nologo /target:winexe /platform:anycpu /optimize+ /reference:System.Web.Extensions.dll /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/out:$exePath" "$sourcePath"
if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE"
}

$starter = @'
@echo off
setlocal
set "APP_DIR=%~dp0"
set "TICKET_PAD_PORT=9580"
set "TICKET_PAD_ENABLE_DANGER_MACROS=1"
set "TICKET_PAD_CURSOR_OVERLAY=1"
set "TICKET_PAD_CURSOR_IDLE_MS=5000"
set "TICKET_PAD_SCREEN_ROTATION=none"
rem Optional: set TICKET_PAD_PIN=1234
start "" "%APP_DIR%TicketPadController.exe"
'@
Set-Content -LiteralPath (Join-Path $publishDir 'start-ticket-pad-controller.bat') -Value $starter -Encoding ASCII

$hiddenStarter = @'
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir
shell.Environment("PROCESS")("TICKET_PAD_PORT") = "9580"
shell.Environment("PROCESS")("TICKET_PAD_ENABLE_DANGER_MACROS") = "1"
shell.Environment("PROCESS")("TICKET_PAD_CURSOR_OVERLAY") = "1"
shell.Environment("PROCESS")("TICKET_PAD_CURSOR_IDLE_MS") = "5000"
shell.Environment("PROCESS")("TICKET_PAD_SCREEN_ROTATION") = "none"
shell.Run Chr(34) & fso.BuildPath(appDir, "TicketPadController.exe") & Chr(34), 0, False
'@
Set-Content -LiteralPath (Join-Path $publishDir 'start-hidden.vbs') -Value $hiddenStarter -Encoding ASCII

$install = @'
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $dir 'TicketPadController.exe'
$taskName = '6KA Ticket Pad Controller'
$port = if ($env:TICKET_PAD_PORT) { [int]$env:TICKET_PAD_PORT } else { 9580 }
if (-not (Test-Path -LiteralPath $exe)) { throw "Missing $exe" }
$action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
$rule = Get-NetFirewallRule -DisplayName '6KA Ticket Pad Controller' -ErrorAction SilentlyContinue
if ($rule) { Remove-NetFirewallRule -DisplayName '6KA Ticket Pad Controller' | Out-Null }
New-NetFirewallRule -DisplayName '6KA Ticket Pad Controller' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -RemoteAddress '100.64.0.0/10','LocalSubnet' | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 2
Get-Process -Name TicketPadController -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,Path,StartTime
'@
Set-Content -LiteralPath (Join-Path $publishDir 'install-ticket-pad-controller.ps1') -Value $install -Encoding UTF8

$readme = @'
# Ticket Pad Controller

用途：在售票機背景啟動本機 HTTP 控制器，提供觸控/滑鼠/鍵盤控制 UI。

## 檔案

- TicketPadController.exe：主程式，Windows GUI subsystem，背景執行不跳 console。
- static\index.html / static\app.js：控制介面。
- macros.json：巨集清單。
- start-hidden.vbs：隱藏啟動主程式。
- install-ticket-pad-controller.ps1：建立登入排程與防火牆規則。

## 預設

- Port：9580
- 危險巨集：已開啟
- PIN：未啟用

## 手動測試

1. 雙擊 start-hidden.vbs，或直接雙擊 TicketPadController.exe。
2. 在售票機本機打開 http://127.0.0.1:9580/
3. 從區網或 Tailscale 裝置打開 http://<售票機IP>:9580/

## 安裝成登入自動啟動

用系統管理員 PowerShell 執行：

powershell -NoProfile -ExecutionPolicy Bypass -File .\install-ticket-pad-controller.ps1

## 可選環境變數

- TICKET_PAD_PORT=9580
- TICKET_PAD_PIN=1234
- TICKET_PAD_ENABLE_DANGER_MACROS=1
- TICKET_APP_EXE=C:\Protech\Suit.Kiosk\Kiosk.Standard.App.exe
'@
Set-Content -LiteralPath (Join-Path $publishDir 'README.md') -Value $readme -Encoding UTF8

$zipPath = Join-Path $ReleaseRoot 'ticket-pad-controller-publish.zip'
$publishItems = Get-ChildItem -LiteralPath $publishDir -Force | Select-Object -ExpandProperty FullName
Compress-Archive -Path $publishItems -DestinationPath $zipPath -Force

[pscustomobject]@{
    ok = $true
    exe = $exePath
    publish_dir = $publishDir
    zip = $zipPath
    port = 9580
    files = @(Get-ChildItem -LiteralPath $publishDir -Recurse | Select-Object FullName,Length)
} | ConvertTo-Json -Depth 5
