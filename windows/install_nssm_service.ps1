param(
  [string]$ServiceName = "JarvisTradingAI",
  [string]$NssmPath = "C:\tools\nssm\win64\nssm.exe",
  [string]$ProjectRoot = "C:\Users\User\ml_trading_system",
  [string]$PowerShellExe = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $NssmPath)) {
  throw "nssm.exe not found at $NssmPath"
}

$scriptPath = Join-Path $ProjectRoot "windows\run_jarvis_watchdog.ps1"
if (-not (Test-Path $scriptPath)) {
  throw "Watchdog script not found: $scriptPath"
}

& $NssmPath install $ServiceName $PowerShellExe "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -ProjectRoot `"$ProjectRoot`""
& $NssmPath set $ServiceName AppDirectory $ProjectRoot
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout (Join-Path $ProjectRoot "logs\jarvis-service.out.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $ProjectRoot "logs\jarvis-service.err.log")
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateOnline 1
& $NssmPath set $ServiceName AppRotateBytes 10485760

Write-Host "Service installed. Run: nssm start $ServiceName"
