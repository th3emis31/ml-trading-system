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

# Everything below goes to a log as well as stdout. Under a scheduled task stdout goes nowhere, and
# the reason the 22 September outage ran for seven hours unnoticed is that nothing recorded what did
# or did not start. A boot that fails should leave evidence behind.
$logDir = 'C:\Users\th_em\ml_trading_system\data\system_health'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'autostart.log'
function Write-Line([string]$text) {
    Write-Output $text
    Add-Content -Path $logFile -Value $text
}

# Measured 23 Sep 2026: 689 MB for all five together, so the set is affordable on this 7.5 GB machine.
# Edge alone was holding twice that. Keep the comment - the terminals were suspected of the memory
# pressure that killed a deep test run, and they were not the cause.
$terminals = @(
    # The strategies' own account. MT5_PATH in start_trading.bat pins this one, and without it
    # MetaTrader5.initialize() binds to whichever terminal Windows offers - which is how the demo
    # strategies once halted on account 25446287 instead of 11581419.
    @{ Path = 'C:\Users\th_em\AppData\Roaming\MetaTrader\terminal64.exe';        Name = 'MT5 demo 11581419 (SmartEntry strategies)' },
    # ATOMIC ANALYST writes the panel files this system reads from disk, and SwingTrendPullback runs here.
    @{ Path = 'C:\Program Files\MetaTrader 5\terminal64.exe';                    Name = 'MT5 demo 25446287 (Atomic panel, SwingTrendPullback)' },
    # The DWX ZeroMQ bridge, account 12755139: the app's MT4 quotes and orders go through it.
    @{ Path = 'C:\Users\th_em\AppData\Roaming\CMC Markets MetaTrader 4\terminal.exe'; Name = 'MT4 bridge 12755139' },
    @{ Path = 'C:\Users\th_em\AppData\Roaming\MetaTrader 4\terminal.exe';        Name = 'MT4 (Roaming)' },
    @{ Path = 'C:\Program Files (x86)\MetaTrader 4\terminal.exe';                Name = 'MT4 (Program Files)' }
)

function Test-Running([string]$exe) {
    $name = [IO.Path]::GetFileNameWithoutExtension($exe)
    $null -ne (Get-CimInstance Win32_Process -Filter "Name='$name.exe'" |
               Where-Object { $_.ExecutablePath -eq $exe } | Select-Object -First 1)
}

Write-Line "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] SmartEntry autostart"

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

$app = $null
try { $app = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/api/signals' -TimeoutSec 8 -UseBasicParsing } catch { }
if ($app) {
    Write-Line "  already trading app (port 5000 answering)"
} else {
    Start-Process -FilePath 'C:\Users\th_em\ml_trading_system\start_trading.bat' `
                  -WorkingDirectory 'C:\Users\th_em\ml_trading_system' -WindowStyle Minimized
    Write-Line "  started trading app"
}
Write-Line "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] autostart done"
