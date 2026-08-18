@echo off
rem Paper-Study 一键关闭前后端（双击即可）
rem 先优雅停止后端，未停干净则强制结束；同时关闭 ui-redesign 开发服务器（5180）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
