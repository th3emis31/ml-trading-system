@echo off
title SmartEntry Pro AI - Trading System
cd /d C:\Users\th_em\ml_trading_system

:start
echo ========================================
echo  SmartEntry Pro AI - Starting...
echo  Dashboard: http://192.168.1.65:5000
echo ========================================
echo.

C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe app.py

echo.
echo [!] Server stopped or crashed. Restarting in 5 seconds...
timeout /t 5 /nobreak
goto start
