@echo off
rem System Doctor deep check (daily 06:30, task "SmartEntry System Doctor Daily"): compiles code and runs tests. Never trades.
rem
rem Same handle-inheritance fix as run_system_doctor.cmd: the run writes to a private temp file and
rem the shared log is appended afterwards, best effort. --fix can launch start_trading.bat, and a
rem child that inherits a redirected log handle holds it for its whole life, which silently killed
rem every later doctor run for a day on 1 October 2026.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
set RESEARCH_JOBS=1
if not exist data\system_health mkdir data\system_health

set "RUNLOG=%TEMP%\smartentry_doctor_deep_%RANDOM%.txt"
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.system_doctor --deep --fix > "%RUNLOG%" 2>&1
set "RC=%ERRORLEVEL%"

>>data\system_health\doctor.log 2>nul echo ===== DEEP %date% %time%
type "%RUNLOG%" >> data\system_health\doctor.log 2>nul
del "%RUNLOG%" >nul 2>&1

exit /b %RC%
