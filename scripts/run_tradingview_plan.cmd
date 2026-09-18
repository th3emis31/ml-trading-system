@echo off
rem TradingView daily plan (task "SmartEntry TradingView Plan", hourly :45). Rebuilds the saved plan for both symbols
rem through the running app, so data\tradingview_plans and the Obsidian vault never carry a stale plan.
rem Read-only: building a plan places no order.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
for %%S in (XAUUSD BTCUSD) do (
  curl -s -o "data\tradingview_plans\_task_%%S.log" "http://127.0.0.1:5000/api/tradingview/plan?symbol=%%S&refresh=1"
)
