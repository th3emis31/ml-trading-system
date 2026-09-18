@echo off
rem Paper forward test of the gold CRT watchlist candidates (EA entry + M15 EMA50, and previous-day sweep + MSS + FVG).
rem Task "SmartEntry CRT Forward", hourly :25. Research only - it never places an order.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\strategy_lab mkdir data\strategy_lab
echo ===== %date% %time% >> data\strategy_lab\crt_forward.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.crt_forward >> data\strategy_lab\crt_forward.log 2>&1
