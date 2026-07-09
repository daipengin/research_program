@echo off
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $pattern='research_program[\\/]+web[\\/]+app\.py'; $targets=@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'streamlit' -and $_.CommandLine -match $pattern }); if ($targets.Count -eq 0) { Write-Host 'Streamlit server is not running for this project.'; exit 0 }; foreach ($p in $targets) { Write-Host ('Stopping PID {0}' -f $p.ProcessId); Stop-Process -Id $p.ProcessId -Force }; Write-Host ('Stopped {0} Streamlit process(es).' -f $targets.Count)"

pause
