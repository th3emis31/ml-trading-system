@echo off
title SmartEntry Pro AI - Trading System
cd /d C:\Users\th_em\ml_trading_system

rem Refuse to become a SECOND server. Three things can now launch this - the Startup shortcut, the
rem logon task, and the System Doctor's repair when nothing is listening - and two app.py processes on
rem port 5000 is a real failure the doctor reports but deliberately will not fix for you. Checked once
rem here rather than inside the loop, because once this instance owns the port the loop must keep it.
netstat -ano | findstr /R /C:"TCP.*:5000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo Another server already answers on port 5000; leaving it alone.
  %SystemRoot%\System32\timeout.exe /t 5 /nobreak >nul
  exit /b 0
)

:start
echo ========================================
echo  SmartEntry Pro AI - Starting...
echo  Dashboard: http://192.168.1.65:5000
echo ========================================
echo.

rem Two MT5 terminals run on this machine and MetaTrader5.initialize() with no path binds to
rem whichever Windows offers. That is why the demo strategies kept halting with "logged-in
rem account 25446287 is not the configured demo account 11581419": 25446287 is the Program
rem Files terminal, which runs the ATOMIC ANALYST indicator and the SwingTrendPullback expert.
rem Pinning the path keeps the system on its own account. The Atomic panel is unaffected - it
rem is read from that terminal's MQL5\Files folder on disk, not over this connection.
set MT5_PATH=C:\Users\th_em\AppData\Roaming\MetaTrader\terminal64.exe

rem The SECOND MT4 account, so one signal trades both MT4 terminals. Three DWX bridges answer on
rem this machine - 12755139 (ICMarketsSC-Demo01), 176028792 (MetaQuotes-Demo) and this one - and they
rem move between port sets whenever a terminal restarts, so nothing is pinned to a port: MT4Service
rem scans the sets and accepts a bridge only when its heartbeat reports the account named here.
rem That account check is the whole safety of it; without it the system would trade whichever
rem terminal answered first.
rem
rem Harmless until a DWX bridge is actually running on 1420704416: the engine simply finds nothing
rem and the second account stays idle while the first trades as normal.
set MT4_ACCOUNT_2=1420704416

C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe app.py

echo.
echo [!] Server stopped or crashed. Restarting in 5 seconds...
%SystemRoot%\System32\timeout.exe /t 5 /nobreak
goto start
