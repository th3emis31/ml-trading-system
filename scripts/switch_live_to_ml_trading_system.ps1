# Switch the live SmartEntry app from C:\Users\th_em to C:\Users\th_em\ml_trading_system.
# Run it yourself (it stops and starts the live app):
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\th_em\ml_trading_system\scripts\switch_live_to_ml_trading_system.ps1
# Nothing in C:\Users\th_em is deleted or edited; data/models/logs are COPIED into the new folder.
# MetaTrader terminals are not touched. Rollback: scripts\rollback_live_to_home.ps1
$ErrorActionPreference = 'Stop'
$HomeDir = 'C:\Users\th_em'
$NewDir  = 'C:\Users\th_em\ml_trading_system'
$Python  = 'C:\Users\th_em\AppData\Local\Programs\Python\Python310\python.exe'
$Tasks = [ordered]@{
  'SmartEntry Paper Trader'        = 'run_paper_trader.cmd'
  'SmartEntry Strategy Lab'        = 'run_strategy_lab.cmd'
  'SmartEntry System Doctor'       = 'run_system_doctor.cmd'
  'SmartEntry System Doctor Daily' = 'run_system_doctor_deep.cmd'
  'SmartEntry Daily Report'        = 'run_daily_report.cmd'
  'SmartEntry AI Employee'         = 'run_ai_employee.cmd'
  'SmartEntry Daily Learning'      = 'run_daily_learning.cmd'
  'SmartEntry Obsidian Notes'      = 'run_obsidian_notes.cmd'
}
function Say($m) { Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }

# 0. Pre-checks: the new folder has the code and repointed launchers
foreach ($f in @('app.py', 'start_trading.bat')) { if (-not (Test-Path "$NewDir\$f")) { throw "$NewDir\$f missing" } }
if (-not (Select-String -Path "$NewDir\start_trading.bat" -SimpleMatch 'cd /d C:\Users\th_em\ml_trading_system' -Quiet)) { throw 'new start_trading.bat is not repointed' }
foreach ($s in $Tasks.Values) { if (-not (Select-String -Path "$NewDir\scripts\$s" -SimpleMatch 'cd /d C:\Users\th_em\ml_trading_system' -Quiet)) { throw "$s is not repointed" } }
try {
  $demo = Invoke-RestMethod -Uri 'http://127.0.0.1:5000/api/demo-model/status' -TimeoutSec 30
  if (@($demo.open_positions).Count -gt 0) { throw 'a demo model position is open; run the switch after it closes' }
} catch { if ($_.Exception.Message -like '*position is open*') { throw } ; Say "demo status not readable ($($_.Exception.Message)); continuing" }

# 1. Wait for running jobs (python -m src.*) so no job writes data/ during the copy
for ($i = 0; $i -lt 90; $i++) {
  $jobs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '-m src\.' }
  if (-not $jobs) { break }
  Say "waiting for $(@($jobs).Count) running job(s) to finish..."; Start-Sleep -Seconds 10
}

# 2. Pause the scheduled tasks during the switch (not deleted)
foreach ($t in $Tasks.Keys) { schtasks /change /tn $t /disable | Out-Null; Say "paused task: $t" }

# 3. Stop the home keep-alive loop first (so it does not relaunch), then the home app.py
$loop = Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape("$HomeDir\start_trading.bat") }
foreach ($p in @($loop)) { if ($p) { Stop-Process -Id $p.ProcessId -Force; Say "stopped home keep-alive loop PID $($p.ProcessId)" } }
$listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
foreach ($c in @($listener)) {
  if (-not $c) { continue }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)"
  if ($proc.CommandLine -notmatch 'app\.py') { throw "port 5000 is held by something other than app.py: $($proc.CommandLine)" }
  Stop-Process -Id $c.OwningProcess -Force; Say "stopped home app.py PID $($c.OwningProcess)"
}
for ($i = 0; $i -lt 30 -and (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }

# 4. Copy live state into the new folder (copy only; robocopy /E never deletes)
foreach ($d in @('data', 'models', 'logs')) {
  robocopy "$HomeDir\$d" "$NewDir\$d" /E /COPY:DAT /DCOPY:T /R:1 /W:1 /NFL /NDL /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy $d failed with code $LASTEXITCODE" }
  Say "copied $d (robocopy code $LASTEXITCODE)"
}

# 5. Point the tasks at the new folder and re-enable them
foreach ($t in $Tasks.Keys) {
  schtasks /change /tn $t /tr "$NewDir\scripts\$($Tasks[$t])" | Out-Null
  schtasks /change /tn $t /enable | Out-Null
  Say "task -> $NewDir\scripts\$($Tasks[$t])"
}

# 6. Startup shortcut -> new keep-alive loop
$lnkPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\SmartEntryProAI.lnk'
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = "$NewDir\start_trading.bat"; $lnk.WorkingDirectory = $NewDir; $lnk.Save()
Say "startup shortcut -> $NewDir\start_trading.bat"

# 7. Start the new keep-alive loop and verify
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$NewDir\start_trading.bat`"" -WorkingDirectory $NewDir -WindowStyle Minimized
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 3
  try { $st = Invoke-RestMethod -Uri 'http://127.0.0.1:5000/api/jarvis/autonomy-status' -TimeoutSec 5; break } catch { }
}
$ls = @(Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)
Say "listeners on :5000: $($ls.Count)"
foreach ($c in $ls) { $p = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)"; $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.ParentProcessId)"; Say "PID $($p.ProcessId): $($p.CommandLine) (parent: $($parent.CommandLine))" }
Start-Sleep -Seconds 15
Push-Location $NewDir
& $Python -m src.system_doctor
Pop-Location
Say 'done. Home copy is untouched; rollback with scripts\rollback_live_to_home.ps1'
