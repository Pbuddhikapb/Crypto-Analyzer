# start_scheduler.ps1
# Starts the Crypto Analyzer Validator 4-Hour Background Scheduler
Param(
    [float]$Interval = 4.0,
    [string]$Mode = "portfolio",
    [switch]$NoImmediate
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Starting Validator 4-Hour Automated Scheduler..." -ForegroundColor Cyan
Write-Host " Interval: Every $Interval hours" -ForegroundColor Yellow
Write-Host " Mode: $Mode" -ForegroundColor Yellow
Write-Host " Email reports will be sent to pbuddhika.pb@gmail.com" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan

$argsList = @("validator\scheduler.py", "--interval", $Interval.ToString(), "--mode", $Mode)
if ($NoImmediate) { $argsList += "--no-immediate" }

& py @argsList
