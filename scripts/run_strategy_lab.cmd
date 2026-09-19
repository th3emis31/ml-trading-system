@echo off
rem Strategy Lab search: research only, never places orders.
rem Scheduled hourly by the "SmartEntry Strategy Lab" task; a heartbeat lock stops overlapping runs.
rem After the search the strategy book re-scores, gathers evidence and re-checks saved strategies on new bars.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\strategy_lab mkdir data\strategy_lab
echo ===== %date% %time% >> data\strategy_lab\strategy_lab.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.strategy_lab run --minutes 35 --max-candidates 1000 >> data\strategy_lab\strategy_lab.log 2>&1
echo ----- strategy book %date% %time% >> data\strategy_lab\strategy_lab.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.strategy_book update --minutes 15 >> data\strategy_lab\strategy_lab.log 2>&1
echo ----- shadow book %date% %time% >> data\strategy_lab\strategy_lab.log
rem Shadow forward tests of every strategy the book keeps: simulated on real candles, places nothing.
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.shadow_book run >> data\strategy_lab\strategy_lab.log 2>&1
