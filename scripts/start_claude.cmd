@echo off
rem Starts Claude Code at Windows logon in two tabs, from the live project folder.
rem   bridge : claude remote-control --spawn=same-dir
rem   desk   : scripts\claude_desk.ps1 (claude --continue, falling back to a plain claude when there is nothing to continue)
rem Registered as the scheduled task "SmartEntry Claude Code" (at logon, interactive token, limited rights,
rem no stored password). It never trades. It does not touch start_trading.bat, the Startup shortcut or any
rem of the other SmartEntry tasks. Log: logs\start_claude.log
rem
rem GUARD: it starts nothing when Claude Code or a remote-control bridge is already running. A second desk would
rem run claude --continue, resume the conversation that is already open and edit the same files as a second
rem writer (this happened on 16 Sep 2026). Windows does not expose a process's working folder to WMI, so the
rem guard is deliberately conservative: ANY running Claude Code process counts, not only one in this folder.
rem At logon nothing is running, so the normal start is unaffected. Do not test this script by launching it
rem while a Claude session is open; dry-run the guard instead (see :guard below).
setlocal
set "PROJECT=C:\Users\th_em\ml_trading_system"
cd /d "%PROJECT%"
if not exist logs mkdir logs
set "LOG=%PROJECT%\logs\start_claude.log"
echo ===== %date% %time% >> "%LOG%"

rem 0. Guard before waiting, so a manual run while a session is open exits at once.
call :guard
if errorlevel 1 goto :already_running

rem 1. Wait up to 60 seconds for the network and the trading app. Never waits longer, and starts anyway if either is down.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(60); $net=$false; $app=$false; while ((Get-Date) -lt $deadline -and -not ($net -and $app)) { if (-not $net) { $net = Test-Connection -Quiet -Count 1 -ComputerName 1.1.1.1 }; if (-not $app) { try { $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri 'http://127.0.0.1:5000/api/self-test'; $app = $true } catch { } }; if (-not ($net -and $app)) { Start-Sleep -Seconds 3 } }; 'waited: network={0} trading app={1}' -f $net, $app" >> "%LOG%" 2>&1

rem Guard again right before launching, in case a session started during the wait.
call :guard
if errorlevel 1 goto :already_running

rem 2. Windows Terminal with the two tabs, or two PowerShell windows when wt.exe is missing.
where wt.exe >nul 2>&1
if errorlevel 1 goto :no_wt

echo starting Windows Terminal with tabs bridge and desk >> "%LOG%"
rem Windows Terminal treats a bare ; as its own subcommand separator, even inside quotes, so the desk tab's
rem continue-or-new logic lives in scripts\claude_desk.ps1 (also used by the fallback below) instead of an
rem escaped inline command. --suppressApplicationTitle keeps the tab names bridge and desk (Claude Code
rem otherwise renames the tab to its session title).
wt -w 0 new-tab --title bridge --suppressApplicationTitle -d "%PROJECT%" powershell -NoExit -NoProfile -Command "claude remote-control --spawn=same-dir" ; new-tab --title desk --suppressApplicationTitle -d "%PROJECT%" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\scripts\claude_desk.ps1"
if errorlevel 1 (
  echo Windows Terminal failed with errorlevel %errorlevel%, falling back to PowerShell windows >> "%LOG%"
  goto :no_wt
)
goto :done

:no_wt
echo starting two PowerShell windows (no Windows Terminal) >> "%LOG%"
start "claude bridge" powershell -NoExit -NoProfile -Command "claude remote-control --spawn=same-dir"
start "claude desk" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\scripts\claude_desk.ps1"

:done
echo started %date% %time% >> "%LOG%"
endlocal
exit /b 0

:already_running
echo exiting without starting anything: Claude Code or a remote-control bridge is already running %date% %time% >> "%LOG%"
endlocal
exit /b 0

rem ---------------------------------------------------------------------------------------------------------
rem :guard  exit code 1 when a remote-control bridge or any Claude Code session is running, else 0.
rem Claude Code runs as claude.exe (native binary under npm\node_modules\@anthropic-ai\claude-code\bin), not node.exe.
rem   bridge : "remote-control" anywhere, or claude.exe with the short subcommand "rc"
rem   session: any claude.exe except the Chrome native host (--chrome-native-host) and headless workers
rem            (--print / stream-json), which are spawned by bridges and plugins and never resume a conversation
rem Its own PowerShell process is excluded ($PID), because its command line contains the search words.
rem Dry run (read-only, starts nothing): call only this PowerShell command and read the line it prints.
:guard
powershell -NoProfile -ExecutionPolicy Bypass -Command "$all = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.ProcessId -ne $PID }); $bridge = @($all | Where-Object { $_.CommandLine -match 'remote-control' -or ($_.Name -eq 'claude.exe' -and $_.CommandLine -match '\src(\s|$)') }); $claude = @($all | Where-Object { $_.Name -eq 'claude.exe' -and $_.CommandLine -notmatch '--chrome-native-host|--print|stream-json|remote-control' -and $_.CommandLine -notmatch '\src(\s|$)' }); if ($bridge.Count -or $claude.Count) { 'guard: already running - bridge PIDs [{0}] claude PIDs [{1}]' -f (($bridge | ForEach-Object { $_.ProcessId }) -join ','), (($claude | ForEach-Object { $_.ProcessId }) -join ','); exit 1 }; 'guard: no Claude Code process and no bridge running'; exit 0" >> "%LOG%" 2>&1
exit /b %errorlevel%
