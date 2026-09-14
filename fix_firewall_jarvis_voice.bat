@echo off
REM JARVIS Windows Firewall Whitelist - Add Chrome/Firefox/Edge to firewall exceptions
REM Run this as Administrator to fix the "Speech Recognition Error: Network" issue

setlocal enabledelayedexpansion

echo.
echo ╔═══════════════════════════════════════════════════════╗
echo ║   JARVIS FIREWALL FIX - Allow Browser Speech API     ║
echo ╚═══════════════════════════════════════════════════════╝
echo.

REM Check for Admin privileges
net session >nul 2>&1
if errorlevel 1 (
    echo ERROR: This script requires Administrator privileges!
    echo.
    echo Please:
    echo   1. Right-click this file
    echo   2. Select "Run as Administrator"
    echo   3. Click "Yes" when prompted
    echo.
    pause
    exit /b 1
)

echo Detected browsers and adding to firewall whitelist...
echo.

REM Chrome
set "chrome_path=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "!chrome_path!" (
    echo Adding Chrome to firewall...
    netsh advfirewall firewall add rule name="JARVIS Chrome Voice" dir=out action=allow program="!chrome_path!" enable=yes >nul
    echo ✓ Chrome added
) else (
    echo ℹ Chrome not found
)

REM Firefox
set "firefox_path=%ProgramFiles%\Mozilla Firefox\firefox.exe"
if exist "!firefox_path!" (
    echo Adding Firefox to firewall...
    netsh advfirewall firewall add rule name="JARVIS Firefox Voice" dir=out action=allow program="!firefox_path!" enable=yes >nul
    echo ✓ Firefox added
) else (
    echo ℹ Firefox not found
)

REM Edge
set "edge_path=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "!edge_path!" (
    echo Adding Edge to firewall...
    netsh advfirewall firewall add rule name="JARVIS Edge Voice" dir=out action=allow program="!edge_path!" enable=yes >nul
    echo ✓ Edge added
) else (
    echo ℹ Edge not found
)

echo.
echo ╔═══════════════════════════════════════════════════════╗
echo ║              FIREWALL RULES ADDED                     ║
echo ╚═══════════════════════════════════════════════════════╝
echo.
echo ✓ Your browser can now access Google Speech API
echo.
echo NEXT STEPS:
echo   1. Close all browser windows
echo   2. Reopen your browser
echo   3. Go to: http://127.0.0.1:5001/jarvis-voice
echo   4. Click "Allow Microphone"
echo   5. Click microphone and say: "Hey JARVIS, hello"
echo.
echo If still seeing "Network Error":
echo   - Try different browser
echo   - Try Incognito/Private mode
echo   - Check browser extensions (disable temporarily)
echo.
pause
