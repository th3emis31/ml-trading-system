@echo off
rem System Doctor deep check (daily 06:30, task "SmartEntry System Doctor Daily"): compiles code and runs tests. Never trades.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
set RESEARCH_JOBS=1
if not exist data\system_health mkdir data\system_health
echo ===== DEEP %date% %time% >> data\system_health\doctor.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.system_doctor --deep --fix >> data\system_health\doctor.log 2>&1
