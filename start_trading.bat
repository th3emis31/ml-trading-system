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

rem The port check above is necessary but not sufficient: it only sees a socket that is already
rem LISTENING, and app.py takes about 30 s to bind because it imports Keras and yfinance first. On
rem 26 September the logon task started the app at 14:25:15 and the Startup shortcut started a second
rem one at 14:25:44 - inside that window, so both passed the port check and two servers ended up on
rem port 5000. The deep System Doctor failed the next morning on exactly that ("App server", pids
rem 22972 and 4528).
rem
rem An app.py process exists from second zero, so looking for the process closes the window the port
rem check leaves open. This covers all three launchers, because start_everything.ps1 and the doctor's
rem repair both come through this file.
rem
rem It fails SAFE by design: only the literal token below refuses to start. If PowerShell is missing,
rem blocked or errors, it prints nothing, findstr finds nothing, and we go on and start the app - a
rem boot script must never refuse to start the system because a check could not be made. Never
rem replace this with an exit-code test, which would read a PowerShell failure as "already running".
powershell -NoProfile -Command "if ((Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*app.py*' })) { 'APPRUNNING' }" 2>nul | findstr /C:"APPRUNNING" >nul 2>&1
if not errorlevel 1 (
  echo An app.py process is already running or still starting up; leaving it alone.
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
set SMARTENTRY_BIND=0.0.0.0

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

rem CAPTURE THE CRASH. app.py ran with NO redirection at all, and logging.basicConfig has no
rem filename, so every traceback went to this console window and was thrown away.
rem logs\app_stderr.log is a relic of an older launcher: nothing has written it since
rem 13 September, yet the System Doctor still reads it and prints 'No errors in the app log'
rem every 30 minutes from 22-day-old bytes. On 5 October the app crash-looped 18 times, took the
rem broker interface down for minutes at a time, cost three strategies their hourly cycle, and
rem left no record of why.
rem
rem A UNIQUE file per launch, never a shared one. The doctor.log lesson of 1 October: a
rem long-lived child inheriting a redirected handle locked that log and silently killed every
rem later run. A name nothing else can hold cannot do that, and the file count itself becomes
rem the restart count.
if not exist logs\app mkdir logs\app
set "APPLOG=logs\app\app_%RANDOM%%RANDOM%.log"
echo ===== app.py launched %date% %time% >> "%APPLOG%"
C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe app.py >> "%APPLOG%" 2>&1

echo.
echo [!] Server stopped or crashed. Restarting in 5 seconds...
%SystemRoot%\System32\timeout.exe /t 5 /nobreak
goto start
