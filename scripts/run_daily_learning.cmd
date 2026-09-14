@echo off
rem Daily self-learning (daily 05:30, task "SmartEntry Daily Learning"). Never trades.
rem Retrains the RF and LSTM for gold and bitcoin on the newest candles; the promotion gate keeps a new model
rem only when it is not worse on unseen bars. Decisions go to data\learning_decisions.json.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\learning mkdir data\learning
echo ===== %date% %time% >> data\learning\daily_learning.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.daily_learning --symbols XAUUSD BTCUSD >> data\learning\daily_learning.log 2>&1
echo ----- finished %date% %time% exit %errorlevel% >> data\learning\daily_learning.log
