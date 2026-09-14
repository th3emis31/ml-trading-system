@echo off
rem Paper-trading cycle for the gold 4h research model. Never places orders.
rem Scheduled hourly by the "SmartEntry Paper Trader" task; each run acts once per closed 4h bar.
cd /d C:\Users\th_em\ml_trading_system
set RESEARCH_JOBS=2
set PYTHONIOENCODING=utf-8
set TF_CPP_MIN_LOG_LEVEL=3
if not exist data\paper_trading mkdir data\paper_trading
echo ===== %date% %time% >> data\paper_trading\paper_trader.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.paper_trader >> data\paper_trading\paper_trader.log 2>&1
