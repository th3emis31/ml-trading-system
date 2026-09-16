# Switch the live SmartEntry app from C:\Users\th_em to C:\Users\th_em\ml_trading_system.
# Run it yourself (it stops and starts the live app):
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\th_em\ml_trading_system\scripts\switch_live_to_ml_trading_system.ps1
# Nothing in C:\Users\th_em is deleted or edited; data/models/logs are COPIED into the new folder.
# MetaTrader terminals are not touched. Safe to run again after a partial run.
# Tasks are repointed without any password: each task's XML is exported, only <Command> changes, the principal is
# the current user with an interactive token and least privilege, and it is re-registered under the same name
# with the same triggers and settings. Rollback: scripts\rollback_live_to_home.ps1
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

function Set-TaskCommand([string]$Name, [string]$Command) {
  # Export -> change only <Command> -> interactive token, least privilege, current user -> re-register same name.
  $xml = [xml](Export-ScheduledTask -TaskName $Name)
  $nsUri = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
  $ns = New-Object System.Xml.XmlNamespaceManager($xml.NameTable); $ns.AddNamespace('t', $nsUri)
  $exec = @($xml.SelectNodes('//t:Actions/t:Exec/t:Command', $ns))
  if ($exec.Count -ne 1) { throw "$Name has $($exec.Count) Exec actions; expected 1" }
  $exec[0].InnerText = $Command
  $principal = $xml.SelectSingleNode('//t:Principals/t:Principal', $ns)
  if (-not $principal) { throw "$Name has no principal" }
  foreach ($child in @('UserId', 'LogonType', 'RunLevel', 'GroupId', 'Password')) {
    $node = $principal.SelectSingleNode("t:$child", $ns); if ($node) { [void]$principal.RemoveChild($node) }
  }
  $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  foreach ($pair in @(@('UserId', $sid), @('LogonType', 'InteractiveToken'), @('RunLevel', 'LeastPrivilege'))) {
    $el = $xml.CreateElement($pair[0], $nsUri); $el.InnerText = $pair[1]; [void]$principal.AppendChild($el)
  }
  Register-ScheduledTask -TaskName $Name -Xml $xml.OuterXml -Force | Out-Null
  $t = Get-ScheduledTask -TaskName $Name
  $action = @($t.Actions)[0].Execute
  if ($action -ne $Command -or $t.Principal.LogonType -ne 'Interactive' -or $t.Principal.RunLevel -ne 'Limited') {
    throw "$Name re-registered but check failed: cmd=$action logon=$($t.Principal.LogonType) runlevel=$($t.Principal.RunLevel)"
  }
}

# 0. Pre-checks: the new folder has the code and repointed launchers; every task exists
foreach ($f in @('app.py', 'start_trading.bat')) { if (-not (Test-Path "$NewDir\$f")) { throw "$NewDir\$f missing" } }
if (-not (Select-String -Path "$NewDir\start_trading.bat" -SimpleMatch 'cd /d C:\Users\th_em\ml_trading_system' -Quiet)) { throw 'new start_trading.bat is not repointed' }
foreach ($s in $Tasks.Values) { if (-not (Select-String -Path "$NewDir\scripts\$s" -SimpleMatch 'cd /d C:\Users\th_em\ml_trading_system' -Quiet)) { throw "$s is not repointed" } }
foreach ($t in $Tasks.Keys) { [void](Get-ScheduledTask -TaskName $t) }
try {
  $demo = Invoke-RestMethod -Uri 'http://127.0.0.1:5000/api/demo-model/status' -TimeoutSec 30
  if (@($demo.open_positions).Count -gt 0) { throw 'a demo model position is open; run the switch after it closes' }
} catch { if ($_.Exception.Message -like '*position is open*') { throw } ; Say "demo status not readable ($($_.Exception.Message)); continuing" }

# 1. Pause the scheduled tasks during the switch (not deleted; no password needed)
foreach ($t in $Tasks.Keys) { Disable-ScheduledTask -TaskName $t | Out-Null; Say "paused task: $t" }

# 2. Wait for running jobs (python -m src.*) so no job writes data/ during the copy
for ($i = 0; $i -lt 90; $i++) {
  $jobs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '-m src\.' }
  if (-not $jobs) { break }
  Say "waiting for $(@($jobs).Count) running job(s) to finish..."; Start-Sleep -Seconds 10
}
if ($jobs) {
  foreach ($t in $Tasks.Keys) { Enable-ScheduledTask -TaskName $t | Out-Null }
  throw 'jobs still running after 15 min; tasks re-enabled, app untouched. Run the switch again later.'
}

# 3. Stop the home keep-alive loop first (so it does not relaunch), then the home app.py
$loop = Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape("$HomeDir\start_trading.bat") }
foreach ($p in @($loop)) { if ($p) { Stop-Process -Id $p.ProcessId -Force; Say "stopped home keep-alive loop PID $($p.ProcessId)" } }
foreach ($c in @(Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)) {
  if (-not $c) { continue }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)"
  if ($proc.CommandLine -notmatch 'app\.py') { throw "port 5000 is held by something other than app.py: $($proc.CommandLine)" }
  Stop-Process -Id $c.OwningProcess -Force; Say "stopped app.py PID $($c.OwningProcess)"
}
for ($i = 0; $i -lt 30 -and (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }

# 4. Copy live state into the new folder (copy only; robocopy /E never deletes)
foreach ($d in @('data', 'models', 'logs')) {
  robocopy "$HomeDir\$d" "$NewDir\$d" /E /COPY:DAT /DCOPY:T /R:1 /W:1 /NFL /NDL /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy $d failed with code $LASTEXITCODE" }
  Say "copied $d (robocopy code $LASTEXITCODE)"
}

# 5. Point the tasks at the new folder (no password) and re-enable them
foreach ($t in $Tasks.Keys) {
  Set-TaskCommand $t "$NewDir\scripts\$($Tasks[$t])"
  Enable-ScheduledTask -TaskName $t | Out-Null
  Say "task -> $NewDir\scripts\$($Tasks[$t]) (interactive, limited, enabled)"
}

# 6. Startup shortcut -> new keep-alive loop
$lnkPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\SmartEntryProAI.lnk'
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath)
$lnk.TargetPath = "$NewDir\start_trading.bat"; $lnk.WorkingDirectory = $NewDir; $lnk.Save()
Say "startup shortcut -> $NewDir\start_trading.bat"

# 7. Start the new keep-alive loop and verify
# Launched through explorer (as at login) so the loop is not a child of whatever shell ran this script.
Start-Process -FilePath 'explorer.exe' -ArgumentList "`"$lnkPath`""
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 3
  try { Invoke-RestMethod -Uri 'http://127.0.0.1:5000/api/jarvis/autonomy-status' -TimeoutSec 5 | Out-Null; break } catch { }
}
$ls = @(Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)
Say "listeners on :5000: $($ls.Count)"
foreach ($c in $ls) { $p = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)"; $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.ParentProcessId)"; Say "PID $($p.ProcessId): $($p.CommandLine) (parent: $($parent.CommandLine))" }
Start-Sleep -Seconds 15
Push-Location $NewDir
& $Python -m src.system_doctor
Pop-Location
Say 'done. Home copy is untouched; rollback with scripts\rollback_live_to_home.ps1'
