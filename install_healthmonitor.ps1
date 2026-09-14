# Install Windows Task Scheduler job for JARVIS monitoring
# This ensures JARVIS auto-restarts even if the watchdog somehow fails
# Run this script as Administrator

$TaskName = "JARVIS_HealthMonitor"
$TaskPath = "\JARVIS\"
$ScriptPath = "C:\Users\User\ml_trading_system\jarvis_healthcheck.bat"
$AppDir = "C:\Users\User\ml_trading_system"

Write-Host "Installing JARVIS Health Monitor Task Scheduler job..."
Write-Host "This will monitor JARVIS every 5 minutes and auto-restart if needed."

# Create folder if doesn't exist
$RootTask = Get-ScheduledTask -TaskPath "\" -TaskName "JARVIS_HealthMonitor" -ErrorAction SilentlyContinue
if (-not $RootTask) {
    Write-Host "Creating task folder..."
}

# Create trigger for every 5 minutes
$Trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At (Get-Date)

# Also trigger at system startup
$StartupTrigger = New-ScheduledTaskTrigger -AtStartup

# Combine triggers
$AllTriggers = @($Trigger, $StartupTrigger)

# Create action
$Action = New-ScheduledTaskAction -Execute $ScriptPath -WorkingDirectory $AppDir

# Create task settings
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false

# Register the task
try {
    Register-ScheduledTask -TaskName $TaskName `
        -Action $Action `
        -Trigger $AllTriggers `
        -Settings $Settings `
        -RunLevel Highest `
        -Force `
        -ErrorAction Stop | Out-Null
    
    Write-Host ""
    Write-Host "SUCCESS! Task installed: $TaskName"
    Write-Host ""
    Write-Host "Health Monitor Details:"
    Write-Host "  - Runs every 5 minutes"
    Write-Host "  - Also runs at system startup"
    Write-Host "  - Auto-restarts JARVIS if it crashes"
    Write-Host "  - Logs available in: $AppDir\logs"
    Write-Host ""
    Write-Host "To view the task:"
    Write-Host "  tasklist /V /FI ""TASKNANE eq JARVIS*"""
    Write-Host ""
    Write-Host "To uninstall:"
    Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    
} catch {
    Write-Host "ERROR: Failed to create task: $_"
    Write-Host ""
    Write-Host "Make sure you run this script as Administrator!"
    exit 1
}
