@echo off
REM Daily JARVIS Backup Script
REM Run this daily via Windows Task Scheduler to backup all data

setlocal enabledelayedexpansion
title JARVIS Daily Backup

echo.
echo ============================================
echo  JARVIS - AUTOMATIC DAILY BACKUP
echo ============================================
echo.

set "BACKUP_DIR=C:\Users\User\ml_trading_system\backups"
set "APP_DIR=C:\Users\User\ml_trading_system"
set "TIMESTAMP=%date:~-4,4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "BACKUP_FILE=jarvis_backup_%TIMESTAMP%.zip"

REM Create backup directory if needed
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

echo [%date% %time%] Creating backup...
echo.

REM Run Python backup manager
cd /d "%APP_DIR%"
python jarvis_backup_manager.py

if errorlevel 1 (
    echo [%date% %time%] WARNING: Backup completed with errors
) else (
    echo [%date% %time%] Backup completed successfully
)

echo.
echo Backup stored in: %BACKUP_DIR%
echo Keep at least 2-3 backups for safety
echo.
