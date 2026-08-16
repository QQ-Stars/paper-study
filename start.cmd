@echo off
rem Paper-Study 一键启动（双击即可）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 pause
