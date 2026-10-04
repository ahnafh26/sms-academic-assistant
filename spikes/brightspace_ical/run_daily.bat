@echo off
REM Daily snapshot for the Brightspace iCal spike (used by Windows Task Scheduler).
REM Output is appended to data\run_log.txt (gitignored).
cd /d "%~dp0"
if not exist data mkdir data
echo ==== %date% %time% ==== >> data\run_log.txt
.venv\Scripts\python -m spike run-all >> data\run_log.txt 2>&1
