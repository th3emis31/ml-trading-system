@echo off
REM JARVIS System Health Check and Restart
REM Run this as a Windows Task Scheduler job every 5 minutes for extra protection
REM or every time Windows starts as a backup

setlocal enabledelayedexpansion

title JARVIS Health Monitor
echo [%date% %time%] Checking JARVIS system health...

REM Check if Flask server is running
for /f "tokens=5" %%a in ('netstat -ano ^| find ":5000"') do (
    set "PID=%%a"
)

if not defined PID (
    echo [%date% %time%] ERROR: Server not running on port 5000!
    echo [%date% %time%] Attempting restart...
    
    REM Kill any orphaned Python processes from previous runs
    taskkill /F /IM python.exe /FI "WINDOWTITLE eq JARVIS*" >nul 2>&1
    
    REM Start watchdog which will restart everything
    cd /d C:\Users\User\ml_trading_system
    start "" python jarvis_watchdog.py
    
    echo [%date% %time%] Server restart initiated.
    exit /b 1
) else (
    echo [%date% %time%] Server is running (PID: !PID!)
    
    REM Do a quick HTTP test
    timeout /t 2 /nobreak >nul
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $null = [System.Net.Sockets.TcpClient]::new().Connect('127.0.0.1', 5000); Write-Host '[%date% %time%] Server responding OK' } catch { Write-Host '[%date% %time%] ERROR: Server not responding'; exit 1 }"
    
    if errorlevel 1 (
        echo [%date% %time%] Server not responding! Restarting...
        taskkill /F /PID !PID! >nul 2>&1
        timeout /t 2 /nobreak >nul
        cd /d C:\Users\User\ml_trading_system
        start "" python jarvis_watchdog.py
        exit /b 1
    )
)

echo [%date% %time%] System health: OK
exit /b 0
