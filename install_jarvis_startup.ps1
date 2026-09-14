# JARVIS Windows Auto-Startup Installer
# Run this script ONCE as Administrator to register JARVIS to start on login

$StartupFolder = [System.Environment]::GetFolderPath('Startup')
$ShortcutPath   = Join-Path $StartupFolder "JARVIS_AutoStart.lnk"
$ScriptRoot     = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetPath     = Join-Path $ScriptRoot "jarvis_startup.bat"

Write-Host "Installing JARVIS auto-startup..."
Write-Host "Startup folder: $StartupFolder"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath   = $TargetPath
$Shortcut.WorkingDirectory = $ScriptRoot
$Shortcut.Description  = "JARVIS AI Trading Assistant Auto-Start"
$Shortcut.WindowStyle  = 7  # Minimized
$Shortcut.Save()

Write-Host ""
Write-Host "SUCCESS! JARVIS will auto-start every time you log in."
Write-Host "Shortcut created at: $ShortcutPath"
Write-Host ""
Write-Host "To test right now, run: jarvis_startup.bat"
Write-Host "To uninstall, delete: $ShortcutPath"
