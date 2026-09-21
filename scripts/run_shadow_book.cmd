@echo off
rem Shadow forward test of every strategy the book keeps (src/shadow_book.py).
rem Task "SmartEntry Shadow Book", hourly :40. Simulates on real broker candles with the
rem real cost model and NEVER places an order - places_orders=False is asserted in the module.
rem Each strategy has a fixed start instant; only trades after it count. Nothing is refitted.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\strategy_lab mkdir data\strategy_lab
echo ===== %date% %time% >> data\strategy_lab\shadow_book.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.shadow_book run >> data\strategy_lab\shadow_book.log 2>&1
