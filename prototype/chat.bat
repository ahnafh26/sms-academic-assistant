@echo off
REM Double-click to chat with the local assistant prototype.
cd /d "%~dp0"
.venv\Scripts\python -m chat %*
if errorlevel 1 pause
