@echo off
chcp 65001 >nul
title 6KA RP5.0 營業額回報系統
cd /d C:\RP
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\RP\start-rp5-visible.ps1
