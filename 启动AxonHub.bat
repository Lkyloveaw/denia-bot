@echo off
chcp 65001 >nul
title AxonHub - AI 网关
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Software\qqbot\tools\AxonHub.ps1" start
echo.
echo   WebUI : http://127.0.0.1:8090
echo   API   : http://127.0.0.1:8090/v1
echo.
pause
