@echo off
title 6KA Cash Month Audit
cd /d C:\6KAweb
echo 6KA Cash Month Audit
echo.
set /p MONTH=Enter month YYYY-MM, example 2026-05: 
if "%MONTH%"=="" (
  echo No month entered.
  pause
  exit /b 1
)
C:\Python312\python.exe C:\6KAweb\cash_month_audit.py --month "%MONTH%"
echo.
echo Done. If there was an error, please leave this window open.
pause
