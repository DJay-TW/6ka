@echo off
cd /d "%~1"
if /I "%~3"=="rp_v5.0.py" if exist "C:\RP\start-rp5-visible.bat" (
    call "C:\RP\start-rp5-visible.bat"
    exit /b %ERRORLEVEL%
)
"%~2" "%~3"
