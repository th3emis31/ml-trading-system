@echo off
setlocal enabledelayedexpansion

title JARVIS - Stable Startup
color 0A

echo.
echo ============================================================
echo  JARVIS ML TRADING SYSTEM - STARTING
echo ============================================================
echo.

cd /d "C:\Users\User\ml_trading_system"

:RESTART
echo [%date% %time%] Starting Flask...
python app.py

echo.
echo ============================================================
echo Flask closed. Waiting 5 seconds before restart...
echo ============================================================
echo.

timeout /t 5 /nobreak
goto RESTART
