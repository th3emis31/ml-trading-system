@echo off
rem System Doctor quick check (every 30 minutes, task "SmartEntry System Doctor"). Never trades.
rem
rem THE LOG IS WRITTEN AFTER THE RUN, NOT DURING IT, AND ON PURPOSE.
rem
rem This used to redirect straight into data\system_health\doctor.log with >>. On 1 October 2026 at
rem 18:28 the doctor found port 5000 dead during an app restart and its --fix launched
rem start_trading.bat. That child inherited the redirected stdout handle and kept doctor.log open for
rem good. Every scheduled run after it died instantly on the locked file: task result 1, nothing
rem written, no report. The dashboard then showed the same fourteen hour old snapshot for a day,
rem with a transient restart frozen in it as two hard failures.
rem
rem So the run writes to a private temp file that no child can outlive, and the append to the shared
rem log is best effort afterwards. A locked log can no longer stop the doctor running or writing its
rem report, which is the thing every page actually reads.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\system_health mkdir data\system_health

set "RUNLOG=%TEMP%\smartentry_doctor_%RANDOM%.txt"
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.system_doctor --fix > "%RUNLOG%" 2>&1
set "RC=%ERRORLEVEL%"

rem Best effort, and allowed to fail. 2>nul so a locked log is silent, not fatal.
>>data\system_health\doctor.log 2>nul echo ===== %date% %time%
type "%RUNLOG%" >> data\system_health\doctor.log 2>nul
del "%RUNLOG%" >nul 2>&1

exit /b %RC%
