# Disabled autostart entries

Nothing here runs. These were moved out of a live autostart location on purpose, and each is kept
rather than deleted so it can be put back with a single move.

## SmartEntryProAI.lnk — moved 27 September 2026 out of the user's Startup folder

It pointed at `start_trading.bat` and was the **second** of two launchers firing at every login. The
other is the scheduled task **SmartEntry Autostart**, which runs `scripts/start_everything.ps1` and is
the better of the two: it starts the five MetaTrader terminals first and waits 45 s for them to log in
before starting the app. The shortcut started the app alone, and it was the omission of the terminals
that caused the seven-hour outage on 22 September 2026.

Both launchers passed `start_trading.bat`'s "refuse to become a second server" check on 26 September,
because that check only looked for a socket already LISTENING and app.py needs about 30 s to bind (it
imports Keras and yfinance first). The logon task started the app at 14:25:15 and this shortcut started
a second one at 14:25:44 — inside that window. Two servers then shared port 5000 until 27 September, and
the deep System Doctor failed the next morning on exactly that: "App server", pids 22972 and 4528.

`start_trading.bat` now also checks for a running app.py **process**, which exists from second zero, so
the window is under a second rather than thirty. That guard is the fix; removing this second launcher is
the belt to its braces, because the window is narrowed rather than eliminated.

**Redundancy is not lost.** The scheduled task **SmartEntry System Doctor** runs every 30 minutes, brings
the app back when nothing is listening, and recreates missing scheduled tasks — that is the safety net,
not this shortcut.

To restore it, move the file back into
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`.

## One way it can come back

`scripts/switch_live_to_ml_trading_system.ps1` (line 100) and `scripts/rollback_live_to_home.ps1`
(line 78) both create this shortcut, by design — they are the folder-switch scripts. Neither is
scheduled, so nothing recreates it on its own, but re-running either one will put a second launcher
back. The System Doctor will not: its only safe fixes are `recreate_missing_scheduled_task`,
`restart_dead_app` and `start_missing_terminals`, and none of them touches the Startup folder.
