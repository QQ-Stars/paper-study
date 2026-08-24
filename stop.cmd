@echo off
rem Paper-Study 一键关闭本地 FastAPI 服务（双击即可）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
