@echo off
chcp 65001 >nul
title QQ 机器人 - 停止
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Software\qqbot\tools\停止所有.ps1"
echo.
pause
