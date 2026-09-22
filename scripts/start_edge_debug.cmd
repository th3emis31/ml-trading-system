@echo off
rem Starts Microsoft Edge normally - same Default profile, same tabs, same TradingView sign-in - but
rem with a debug port open so the SmartEntry TV Chart Worker can attach to it.
rem
rem Why this exists: the worker first used a separate Chromium with its own profile, which had no
rem TradingView session and asked the owner to sign in twice. Their browser is Edge, and the point of
rem using it is that the session is already there. Attaching needs a port; Edge only opens one if it
rem is started this way, and a second Edge started against an already-running profile just hands over
rem and exits - so Edge has to be started by this script rather than the usual shortcut.
rem
rem The worker opens its own tab, saves one Pine script, closes that tab, and detaches. It never quits
rem Edge, never touches another tab, and never places an order.
if not "%~1"=="" goto :run
tasklist /FI "IMAGENAME eq msedge.exe" 2>nul | find /I "msedge.exe" >nul
if not errorlevel 1 (
  echo Edge is already running WITHOUT the debug port.
  echo Close Edge completely, then run this again. Edge will restore your tabs.
  pause
  exit /b 1
)
:run
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --profile-directory=Default
