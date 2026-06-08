@echo off
title 6KA Cashbox Monitor
cd /d C:\6KAweb
C:\Python312\python.exe C:\6KAweb\cashbox_estimator.py --watch --interval 30
pause
