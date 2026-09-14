@echo off
rem Daily market analysis + system brief (daily 06:45, task "SmartEntry Daily Report"). Never trades.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\daily_reports mkdir data\daily_reports
echo ===== %date% %time% >> data\daily_reports\daily_report.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.daily_report >> data\daily_reports\daily_report.log 2>&1
