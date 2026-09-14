@echo off
rem AI Employee: daily read-only review by Claude Code on the owner's subscription (task "SmartEntry AI Employee", 07:15).
rem Never trades, never edits code or settings. Results in data\ai_employee and on the /ai-employee page.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\ai_employee mkdir data\ai_employee
echo ===== %date% %time% >> data\ai_employee\ai_employee.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.ai_employee run >> data\ai_employee\ai_employee.log 2>&1
