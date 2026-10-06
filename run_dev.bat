@echo off
REM Text the assistant from a fake phone in this window (starts the web server too).
cd /d "%~dp0"
.venv\Scripts\python -m app.dev %*
if errorlevel 1 pause
