@echo off
rem Plan journal (task "SmartEntry Plan Journal", hourly at :07): records the breakout plans the TradingView Market Map
rem draws, settles them on broker 4H candles and updates what the system learned (data\plan_journal). Never trades.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\plan_journal mkdir data\plan_journal
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.plan_journal >> data\plan_journal\plan_journal.log 2>&1
