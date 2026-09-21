@echo off
rem Arm MT4 auto-trading: point the auto-trade session at MT4 and turn autonomy on.
rem All the logic is in scripts\mt4_autotrade.py - cmd.exe quoting cannot be trusted with a secret
rem inside an HTTP header, so this file is only a wrapper.
rem Undo with disable_mt4_autotrade.cmd. Touches no expert advisor.
cd /d C:\Users\th_em\ml_trading_system
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" scripts\mt4_autotrade.py enable
pause
