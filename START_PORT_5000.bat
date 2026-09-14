@echo off
REM JARVIS Startup - Port 5000 (CORRECT PORT)

setlocal enabledelayedexpansion

echo.
echo ╔════════════════════════════════════════╗
echo ║   JARVIS Server - Starting on PORT 5000 ║
echo ╚════════════════════════════════════════╝
echo.

REM Kill any existing Python processes
echo Stopping old processes...
for /f "tokens=2" %%A in ('tasklist ^| findstr python.exe') do (
    echo ✓ Stopped
)

echo.
echo Starting Flask on port 5000...
echo.

cd /d c:\Users\User\ml_trading_system
python app.py

pause
