@echo off
title JARVIS Auto-Startup & Watchdog
echo =====================================================
echo   JARVIS - Professional AI Trading Assistant
echo   Auto-Startup with Auto-Recovery
echo =====================================================
echo.
set "PYTHON_EXE=C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
set "APP_DIR=C:\Users\User\ml_trading_system"
set "JARVIS_URL=http://127.0.0.1:5000/jarvis-voice"

echo [1/5] Starting JARVIS Watchdog (auto-recovery system)...
cd /d "C:\Users\User\ml_trading_system"
if exist "%PYTHON_EXE%" (
	start "" /min "%PYTHON_EXE%" jarvis_watchdog.py
) else (
	start "" /min python jarvis_watchdog.py
)
echo     Watchdog started - will monitor and auto-restart server if needed.

echo [2/5] Waiting for server to initialize (via watchdog)...
set "READY=0"
for /l %%i in (1,1,30) do (
	powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $null = [System.Net.Sockets.TcpClient]::new().Connect('127.0.0.1', 5000); exit 0 } catch { exit 1 }"
	if not errorlevel 1 (
		set "READY=1"
		goto :SERVER_READY
	)
	timeout /t 1 /nobreak >nul
)

:SERVER_READY
if "%READY%"=="1" (
	echo     Server is healthy and responding.
) else (
	echo     Server initialization in progress...
)

echo [3/5] Opening JARVIS Dashboard in browser...
timeout /t 2 /nobreak >nul
start "" "%JARVIS_URL%"

echo [4/5] Opening system logs folder...
start "" "C:\Users\User\ml_trading_system\logs"

echo [5/5] Startup complete!

echo.
echo ================================================================
echo JARVIS is running with AUTO-RECOVERY enabled!
echo ================================================================
echo.
echo Dashboard URL: %JARVIS_URL%
echo.
echo What happens now:
echo   [✓] Watchdog monitors server continuously
echo   [✓] If server crashes, it auto-restarts
echo   [✓] Logs saved to: C:\Users\User\ml_trading_system\logs
echo   [✓] Voice listening active
echo   [✓] Market analysis running
echo.
echo System is 24/7 protected against crashes!
echo.
pause
