@echo off
REM JARVIS Auto-Startup Script
REM This script starts the JARVIS watchdog at system startup
REM The watchdog ensures Flask never stops

cd /d C:\Users\User\ml_trading_system

echo [JARVIS STARTUP] Starting JARVIS Super Watchdog System...
echo [JARVIS STARTUP] System will run continuously and auto-restart if needed

python jarvis_super_watchdog.py

pause
