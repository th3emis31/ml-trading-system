# Brings the whole trading system up at logon: the MetaTrader terminals first, then the app.
#
# Why this exists. On 22 September 2026 the machine rebooted at 23:33 and the app never came back, so
# every hourly cycle of all four demo strategies failed with "connection refused" for seven hours and
# the 05:30 retrain silently fell back to Yahoo prices. The Startup shortcut alone was not enough, and
# it only ever started the app - the terminals were never part of it, even though the strategies
# cannot trade and the bridge cannot quote without them.
#
# Order matters. The terminals are started first and given time to log in, because the app reads
# broker candles through MT5 and the DWX bridge through MT4; starting the app into terminals that are
# not ready just produces a first cycle that reports everything disconnected.
#
# Everything here is idempotent: each terminal is started only if that exact executable is not already
# running, and the app is left alone if port 5000 already answers. Running this twice changes nothing,
# which is what lets it be safe both at logon and as a repair.
#
# It starts terminals. It never attaches, modifies, disables or removes an expert on any of them.

$ErrorActionPreference = 'SilentlyContinue'

# This machine's own locations come from config\machine.json (build map step 9), and the root is worked
# out from where this script sits rather than written down - so the same script works from a copy on
# another PC or on the USB drive. If the config cannot be read the literals below still apply: a boot
# script is the last place that should fail because a json file is malformed.
$Root = Split-Path -Parent $PSScriptRoot
$machine = $null
try { $machine = Get-Content (Join-Path $Root 'config\machine.json') -Raw | ConvertFrom-Json } catch { }
function Get-TerminalPath([string]$key, [string]$fallback) {
    if ($machine -and $machine.terminals -and $machine.terminals.$key) { return [string]$machine.terminals.$key }
    return $fallback
}

# Everything below goes to a log as well as stdout. Under a scheduled task stdout goes nowhere, and
# the reason the 22 September outage ran for seven hours unnoticed is that nothing recorded what did
# or did not start. A boot that fails should leave evidence behind.
$logDir = Join-Path $Root 'data\system_health'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'autostart.log'
# Concurrent copies of this script fight over the log. Add-Content fails while another process holds the
# file, and $ErrorActionPreference = 'SilentlyContinue' above swallows that failure, so the lines simply
# vanished. Measured 27 Sep 2026: four copies launched in parallel, three were correctly refused by the
# single-instance mutex, and NONE of the three appeared in the log. Retrying alone was not enough either -
# all four write their header in the same instant, and only one survived.
#
# This matters beyond tidiness. This log was the evidence used to work out how many copies ran on
# 26 September, and a log that silently drops the lines of every concurrent run cannot answer that
# question - it made one run look like the only run. A boot script whose whole purpose is to leave
# evidence behind must not lose it under exactly the condition being investigated.
#
# So writers serialise on their own named mutex, with the retry kept as a backstop. Both are bounded and
# neither can throw: a log write must never be able to stop the trading system coming up.
$logMutex = $null
try { $logMutex = New-Object System.Threading.Mutex($false, 'Global\SmartEntryAutostartLog') } catch { }

function Write-Line([string]$text) {
    Write-Output $text
    $held = $false
    if ($logMutex) {
        try { $held = $logMutex.WaitOne(2000) }
        catch [System.Threading.AbandonedMutexException] { $held = $true }
        catch { }
    }
    try {
        for ($attempt = 0; $attempt -lt 12; $attempt++) {
            try { Add-Content -Path $logFile -Value $text -ErrorAction Stop; break }
            catch { Start-Sleep -Milliseconds (25 * ($attempt + 1)) }
        }
    } finally {
        if ($held) { try { $logMutex.ReleaseMutex() } catch { } }
    }
}

# Measured 23 Sep 2026: 689 MB for all five together, so the set is affordable on this 7.5 GB machine.
# Edge alone was holding twice that. Keep the comment - the terminals were suspected of the memory
# pressure that killed a deep test run, and they were not the cause.
$terminals = @(
    # The strategies' own account. MT5_PATH in start_trading.bat pins this one, and without it
    # MetaTrader5.initialize() binds to whichever terminal Windows offers - which is how the demo
    # strategies once halted on account 25446287 instead of 11581419.
    @{ Path = (Get-TerminalPath 'mt5_strategies' 'C:\Users\th_em\AppData\Roaming\MetaTrader\terminal64.exe'); Name = 'MT5 demo 11581419 (SmartEntry strategies)' },
    # ATOMIC ANALYST writes the panel files this system reads from disk, and SwingTrendPullback runs here.
    @{ Path = (Get-TerminalPath 'mt5_panel' 'C:\Program Files\MetaTrader 5\terminal64.exe'); Name = 'MT5 demo 25446287 (Atomic panel, SwingTrendPullback)' },
    # The DWX ZeroMQ bridge, account 12755139: the app's MT4 quotes and orders go through it.
    @{ Path = (Get-TerminalPath 'mt4_bridge' 'C:\Users\th_em\AppData\Roaming\CMC Markets MetaTrader 4\terminal.exe'); Name = 'MT4 bridge 12755139' },
    @{ Path = (Get-TerminalPath 'mt4_roaming' 'C:\Users\th_em\AppData\Roaming\MetaTrader 4\terminal.exe'); Name = 'MT4 (Roaming)' },
    @{ Path = (Get-TerminalPath 'mt4_program_files' 'C:\Program Files (x86)\MetaTrader 4\terminal.exe'); Name = 'MT4 (Program Files)' }
)

function Test-Running([string]$exe) {
    $name = [IO.Path]::GetFileNameWithoutExtension($exe)
    # Normalise BOTH sides before comparing. config\machine.json writes forward slashes (a json file of
    # escaped backslashes is a trap to edit by hand) and Windows reports ExecutablePath with backslashes,
    # so the plain -eq that used to be here was ALWAYS false. Measured 27 Sep 2026: all five terminals
    # running, all five reported not running. CLAUDE.md names this exact trap and says to compare with
    # same_path() rather than -eq; this is the PowerShell equivalent. Case-insensitive too, because
    # Windows paths are.
    #
    # The cost of the bug was not a duplicate terminal - MetaTrader refuses a second instance of the same
    # install, so Start-Process was a no-op that still logged "started". The cost was the 45-second sleep
    # below firing at every boot, which is both the slowest possible path to getting the app up and the
    # window in which a second launcher can race this one.
    $want = ($exe -replace '/', '\').TrimEnd('\')
    $null -ne (Get-CimInstance Win32_Process -Filter "Name='$name.exe'" |
               Where-Object { ($_.ExecutablePath -replace '/', '\').TrimEnd('\') -ieq $want } |
               Select-Object -First 1)
}

Write-Line "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] SmartEntry autostart"

# Single instance. Nothing stopped two copies of this script running at once, and two did run on
# 23 September 2026 thirteen seconds apart (07:06:46 and 07:06:59). That time both found port 5000
# answering and neither started anything, so it was harmless - but the port probe below happens AFTER
# the 45 s sleep, so a second copy starting during that sleep would launch the five terminals a second
# time and reach start_trading.bat a second time as well.
#
# A NAMED MUTEX, not a lock file under data\. A lock file left behind by a crash or a hard reboot would
# block every later boot, and a script whose job is to bring the trading system up must never be able
# to lock itself out. Windows releases a mutex when the owning process dies, so it cannot go stale.
#
# Fails safe in both directions: if the mutex cannot even be created the script carries on, because not
# starting the system is worse than starting it twice. An abandoned mutex (previous owner killed) counts
# as acquired, which is exactly right - that owner is gone.
$autostartMutex = $null
try {
    $autostartMutex = New-Object System.Threading.Mutex($false, 'Global\SmartEntryAutostart')
    $owned = $false
    try { $owned = $autostartMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) {
        Write-Line "  another copy of this script is already running; leaving it to finish"
        Write-Line "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] autostart skipped (already running)"
        exit 0
    }
} catch {
    Write-Line "  note: could not create the single-instance mutex ($($_.Exception.Message)); continuing anyway"
}

$started = 0
foreach ($t in $terminals) {
    if (-not (Test-Path $t.Path)) { Write-Line "  skip    $($t.Name): not installed"; continue }
    if (Test-Running $t.Path)     { Write-Line "  already $($t.Name)";               continue }
    Start-Process -FilePath $t.Path -WindowStyle Minimized
    Write-Line "  started $($t.Name)"
    $started++
}

# Only wait when something was actually started, so a repair run on a healthy machine is instant.
if ($started -gt 0) {
    Write-Line "  waiting 45 s for $started terminal(s) to log in before starting the app"
    Start-Sleep -Seconds 45
}

# Is a server already on port 5000? A TCP connect answers exactly that, in about 250 ms.
#
# This used to GET /api/signals with -TimeoutSec 8, which is a coin flip rather than a check: that endpoint
# runs RF and LSTM inference for every symbol and measured 5.0-5.5 s warm, 11 s on a cold or loaded first
# call, and once 60 s+ while four copies of this script were hammering the machine (GET / answers in
# 0.04-0.9 s and a TCP connect in 255 ms). An 8 s timeout against a 5-11 s endpoint fails exactly when the
# machine is busiest, which is at boot - and every failure made the script conclude the app was down and
# launch start_trading.bat. That is how a second server came to exist on 26 September.
# Asking a heavy endpoint whether a process is alive is the mistake; the question is only ever "does
# something own the port".
$app = $false
try {
    $probe = New-Object Net.Sockets.TcpClient
    try { $probe.Connect('127.0.0.1', 5000); $app = $probe.Connected } finally { $probe.Close() }
} catch { }
if ($app) {
    Write-Line "  already trading app (port 5000 answering)"
} else {
    Start-Process -FilePath (Join-Path $Root 'start_trading.bat') `
                  -WorkingDirectory $Root -WindowStyle Minimized
    Write-Line "  started trading app"
}
Write-Line "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] autostart done"

# Released here for the normal path; Windows releases it anyway if this process is killed, which is the
# whole reason a mutex was chosen over a file.
if ($autostartMutex) {
    try { $autostartMutex.ReleaseMutex() } catch { }
    $autostartMutex.Dispose()
}
