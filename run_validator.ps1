# run_validator.ps1
# Runs the Backtest Validator Agent using the Windows Python launcher (py)
Param(
    [switch]$Portfolio,
    [string]$Symbol,
    [string]$Timeframe,
    [int]$Candles,
    [switch]$SkipResearch,
    [switch]$NoHifi,
    [switch]$Email
)

$cmdArgs = @("validator\backtest_agent.py")

if ($Portfolio) { $cmdArgs += "--portfolio" }
if ($Symbol) { $cmdArgs += "--symbol"; $cmdArgs += $Symbol }
if ($Timeframe) { $cmdArgs += "--timeframe"; $cmdArgs += $Timeframe }
if ($Candles) { $cmdArgs += "--candles"; $cmdArgs += $Candles.ToString() }
if ($SkipResearch) { $cmdArgs += "--skip-research" }
if ($NoHifi) { $cmdArgs += "--no-hifi" }
if ($Email) { $cmdArgs += "--email" }

Write-Host "[VALIDATOR RUNNER] Executing Backtest Validator Agent..." -ForegroundColor Cyan
& py @cmdArgs
