@echo off
REM JARVIS Server Restart Script
REM This fixes the "Voice stopped working" issue

setlocal enabledelayedexpansion

echo.
echo ╔═══════════════════════════════════════════════════════╗
echo ║        JARVIS Server Restart - Voice Fix             ║
echo ╚═══════════════════════════════════════════════════════╝
echo.

echo Killing all Python processes...
taskkill /PID * /F >nul 2>&1
for /f "tokens=2" %%A in ('tasklist ^| findstr python') do (
    for /f "tokens=1" %%B in ('echo %%A') do (
        taskkill /PID %%B /F >nul 2>&1
    )
)

echo ✓ Stopped old processes
echo.
echo Waiting 3 seconds...
timeout /t 3 /nobreak >nul

echo.
echo Starting Flask server...
echo.

cd /d c:\Users\User\ml_trading_system

REM Start Flask in background
start "JARVIS Server" cmd /c python app.py

echo ✓ Server starting...
echo.
echo ⏳ Waiting 25 seconds for initialization...
echo.

timeout /t 25 /nobreak >nul

echo.
echo ╔═══════════════════════════════════════════════════════╗
echo ║              SERVER READY - OPEN BROWSER!             ║
echo ╚═══════════════════════════════════════════════════════╝
echo.
echo Go to your browser and open:
echo.
echo   http://127.0.0.1:5001/jarvis-voice
echo.
echo Then:
echo   1. Wait for spinning blue circle
echo   2. Click ALLOW microphone
echo   3. Click microphone circle (turns red)
echo   4. Say: "Hey JARVIS, hello"
echo   5. VOICE WORKS!
echo.
pause
