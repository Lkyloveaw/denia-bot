@echo off
chcp 65001 >nul
title QQ 机器人 - 启动
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Software\qqbot\tools\启动所有.ps1"
echo.
pause
