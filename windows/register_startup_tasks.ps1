param(
  [string]$ProjectRoot = "C:\Users\User\ml_trading_system",
  [string]$TaskPrefix = "JarvisAI"
)

$ErrorActionPreference = "Stop"

$watchdogScript = Join-Path $ProjectRoot "windows\run_jarvis_watchdog.ps1"
$openUiScript = Join-Path $ProjectRoot "windows\open_jarvis_at_login.ps1"

if (-not (Test-Path $watchdogScript)) {
  throw "Missing watchdog script: $watchdogScript"
}
if (-not (Test-Path $openUiScript)) {
  throw "Missing UI launch script: $openUiScript"
}

$psExe = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

$backendAction = New-ScheduledTaskAction -Execute $psExe -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`" -ProjectRoot `"$ProjectRoot`""
$uiAction = New-ScheduledTaskAction -Execute $psExe -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$openUiScript`""

$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName "$TaskPrefix-Backend" -Action $backendAction -Trigger $trigger -Principal $principal -Force | Out-Null
Register-ScheduledTask -TaskName "$TaskPrefix-UI" -Action $uiAction -Trigger $trigger -Principal $principal -Force | Out-Null

Write-Host "Startup tasks created:" 
Write-Host "- $TaskPrefix-Backend"
Write-Host "- $TaskPrefix-UI"
Write-Host "These will start JARVIS backend and open JARVIS Voice page when you log in."
