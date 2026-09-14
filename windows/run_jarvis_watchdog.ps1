param(
  [string]$ProjectRoot = "C:\Users\User\ml_trading_system",
  [string]$PythonExe = "python",
  [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

while ($true) {
  Write-Host "[$(Get-Date -Format s)] Starting JARVIS..."
  & $PythonExe app.py
  $exitCode = $LASTEXITCODE
  Write-Host "[$(Get-Date -Format s)] JARVIS stopped with exit code $exitCode. Restarting in $RestartDelaySeconds seconds..."
  Start-Sleep -Seconds $RestartDelaySeconds
}
