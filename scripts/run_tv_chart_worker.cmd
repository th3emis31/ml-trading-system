@echo off
rem Keeps the SmartEntry map current on the owner's real TradingView chart (task "SmartEntry TV Chart
rem Worker", hourly at :35 - after the Strategy Lab at :20 so the board it writes is the fresh one).
rem
rem It attaches to the owner's OWN Microsoft Edge over its debug port, because that is where they are
rem already signed in to TradingView. Edge must have been started by scripts\start_edge_debug.cmd;
rem if it was not, the worker records that as its reason and changes nothing.
rem
rem It opens its own tab, saves one named Pine script ("SmartEntry Market Map"), closes that tab and
rem detaches. It never quits Edge, never touches another tab, never opens the Trade panel, never
rem places an order and never creates or renames a chart layout.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\tradingview mkdir data\tradingview
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.tradingview_chart_worker run >> data\tradingview\chart_worker.log 2>&1
