@echo off
rem System Doctor quick check (every 30 minutes, task "SmartEntry System Doctor"). Never trades.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\system_health mkdir data\system_health
echo ===== %date% %time% >> data\system_health\doctor.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.system_doctor --fix >> data\system_health\doctor.log 2>&1
