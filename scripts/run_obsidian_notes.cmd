@echo off
rem Obsidian notes (hourly at :50, task "SmartEntry Obsidian Notes"). Never trades.
rem Writes the daily report, daily plan, learning verdict, research log and trade journal into the vault
rem saved in data\obsidian.json (default Documents\SmartEntry Vault). Your text under "## My notes" is kept.
cd /d C:\Users\th_em\ml_trading_system
set PYTHONIOENCODING=utf-8
if not exist data\obsidian mkdir data\obsidian
echo ===== %date% %time% >> data\obsidian\obsidian_notes.log
"C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe" -m src.obsidian_notes >> data\obsidian\obsidian_notes.log 2>&1
