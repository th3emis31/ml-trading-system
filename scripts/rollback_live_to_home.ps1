# Roll the live SmartEntry app back from C:\Users\th_em\ml_trading_system to C:\Users\th_em.
# Run it yourself:
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\th_em\ml_trading_system\scripts\rollback_live_to_home.ps1 [-CopyDataBack]
# Default: the home copy is used exactly as it was left at the switch (state written after the switch stays
# in ml_trading_system\data). -CopyDataBack first copies ml_trading_system\data and models back to home
# (robocopy /E /XO copies newer files, never deletes). Nothing is deleted anywhere. MetaTrader is not touched.
# Tasks are repointed without any password (export XML, change only <Command>, interactive token, least privilege).
param([switch]$CopyDataBack)
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

foreach ($t in $Tasks.Keys) { Disable-ScheduledTask -TaskName $t | Out-Null; Say "paused task: $t" }
for ($i = 0; $i -lt 90; $i++) {
  $jobs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '-m src\.' }
  if (-not $jobs) { break }
  Say "waiting for $(@($jobs).Count) running job(s)..."; Start-Sleep -Seconds 10
}
$loop = Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape("$NewDir\start_trading.bat") }
foreach ($p in @($loop)) { if ($p) { Stop-Process -Id $p.ProcessId -Force; Say "stopped new keep-alive loop PID $($p.ProcessId)" } }
foreach ($c in @(Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)) {
  if (-not $c) { continue }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)"
  if ($proc.CommandLine -notmatch 'app\.py') { throw "port 5000 is held by something other than app.py: $($proc.CommandLine)" }
  Stop-Process -Id $c.OwningProcess -Force; Say "stopped app.py PID $($c.OwningProcess)"
}
for ($i = 0; $i -lt 30 -and (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }

if ($CopyDataBack) {
  foreach ($d in @('data', 'models', 'logs')) {
    robocopy "$NewDir\$d" "$HomeDir\$d" /E /XO /COPY:DAT /DCOPY:T /R:1 /W:1 /NFL /NDL /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $d failed with code $LASTEXITCODE" }
    Say "copied newer $d back to home (robocopy code $LASTEXITCODE)"
  }
}

foreach ($t in $Tasks.Keys) {
  Set-TaskCommand $t "$HomeDir\scripts\$($Tasks[$t])"
  Enable-ScheduledTask -TaskName $t | Out-Null
  Say "task -> $HomeDir\scripts\$($Tasks[$t]) (interactive, limited, enabled)"
}
$lnkPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\SmartEntryProAI.lnk'
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath)
$lnk.TargetPath = "$HomeDir\start_trading.bat"; $lnk.WorkingDirectory = $HomeDir; $lnk.Save()
Say "startup shortcut -> $HomeDir\start_trading.bat"

# Launched through explorer (as at login) so the loop is not a child of whatever shell ran this script.
Start-Process -FilePath 'explorer.exe' -ArgumentList "`"$lnkPath`""
for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Seconds 3; try { Invoke-RestMethod -Uri 'http://127.0.0.1:5000/api/jarvis/autonomy-status' -TimeoutSec 5 | Out-Null; break } catch { } }
Say "listeners on :5000: $(@(Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue).Count)"
Start-Sleep -Seconds 15
Push-Location $HomeDir
& $Python -m src.system_doctor
Pop-Location
Say 'rollback done'
