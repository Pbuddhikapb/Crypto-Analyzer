# register_windows_task.ps1
# Registers a Windows Scheduled Task to run the Validator every 4 hours automatically
$TaskName = "CryptoAnalyzer_Validator_4H"
$WorkingDir = $PSScriptRoot
$PythonExe = "python.exe"
$ScriptPath = Join-Path $PSScriptRoot "validator\backtest_agent.py"

try {
    $Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`" --portfolio" -WorkingDirectory $WorkingDir
    $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 4)
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Runs Crypto Analyzer backtest validation every 4 hours and emails reports." -Force | Out-Null

    Write-Host "[OK] Windows Scheduled Task '$TaskName' successfully registered!" -ForegroundColor Green
    Write-Host "Action: $PythonExe `"$ScriptPath`" --portfolio" -ForegroundColor Cyan
    Write-Host "Schedule: Every 4 hours" -ForegroundColor Yellow
} catch {
    Write-Host "[WARN] Could not register Windows Scheduled Task directly: $_" -ForegroundColor Yellow
    Write-Host "You can also run the background daemon directly via: .\start_scheduler.ps1" -ForegroundColor Cyan
}
