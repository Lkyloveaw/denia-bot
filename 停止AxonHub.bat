@echo off
chcp 65001 >nul
title AxonHub - 停止
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Software\qqbot\tools\AxonHub.ps1" stop
echo.
pause
