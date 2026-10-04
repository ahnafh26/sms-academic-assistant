# Registers a daily Windows scheduled task that runs the spike's run-all
# (fetch -> normalize -> report -> diff -> ANALYSIS.md) via run_daily.bat.
#
#   powershell -ExecutionPolicy Bypass -File schedule_task.ps1            # 9:00 AM daily
#   powershell -ExecutionPolicy Bypass -File schedule_task.ps1 -At 8:30am
#   powershell -ExecutionPolicy Bypass -File schedule_task.ps1 -Remove
#
# Runs as the current user only while logged in (no stored password).
# If the PC is off/asleep at the scheduled time, it runs at the next opportunity.

param(
    [string]$At = "9:00am",
    [string]$TaskName = "BrightspaceSpike",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed scheduled task '$TaskName'."
    return
}

$spikeDir = $PSScriptRoot
$bat = Join-Path $spikeDir "run_daily.bat"
if (-not (Test-Path $bat)) { throw "run_daily.bat not found in $spikeDir" }
if (-not (Test-Path (Join-Path $spikeDir ".venv\Scripts\python.exe"))) { throw "Create the .venv first (see README)." }

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $spikeDir
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Daily Brightspace iCal spike snapshot + analysis (SMS academic assistant, Phase 0)." -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "Registered '$TaskName': daily at $At. Next run: $($info.NextRunTime)"
