# Back the whole system up to the owner's external drive, whenever it is plugged in.
#
# Run it as often as you like: it copies only what changed, so a run with nothing new takes seconds
# and a run with the drive absent does nothing at all and says so.
#
# Three rules this script is built around:
#
# 1. FIND THE DRIVE BY LABEL, NEVER BY LETTER. A USB drive is D: today and E: tomorrow depending on
#    what else is plugged in, and a backup that writes to whatever happens to be D: could one day
#    write into a different disk entirely.
# 2. NEVER DELETE. No /MIR and no /PURGE. Something removed from the PC stays on the drive, because
#    a backup that deletes is not a backup - it is a mirror of today's mistakes.
# 3. ONLY INSIDE THE OWNER'S FOLDER. The drive holds their personal documents. Everything this script
#    writes goes under "A. Trading system Themis", and it refuses to run if that folder is absent
#    rather than creating one somewhere unexpected.

param(
    [string]$VolumeLabel = 'TOSHIBA EXT',
    [string]$FolderName  = 'A. Trading system Themis',
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$logDir = Join-Path $PSScriptRoot '..\data\system_health'
$null = New-Item -ItemType Directory -Force -Path $logDir -ErrorAction SilentlyContinue
$log = Join-Path $logDir 'usb_backup.log'

function Write-Line($text) {
    "$stamp  $text" | Out-File -FilePath $log -Append -Encoding utf8
    if (-not $Quiet) { Write-Output $text }
}

# --- is the drive here? ---
$vol = Get-CimInstance Win32_LogicalDisk | Where-Object { $_.VolumeName -eq $VolumeLabel }
if (-not $vol) {
    Write-Line "drive '$VolumeLabel' is not plugged in - nothing to do"
    exit 0
}
$drive = $vol.DeviceID
$target = Join-Path $drive $FolderName

if (-not (Test-Path $target)) {
    # Deliberately does NOT create it. If the folder is missing, either the wrong disk carries this
    # label or the owner moved it, and guessing is how a backup ends up somewhere nobody looks.
    Write-Line "FOUND $drive but '$FolderName' does not exist on it - refusing to create a folder; check the drive"
    exit 1
}

$freeGB = [math]::Round($vol.FreeSpace / 1GB, 1)
$dated = Join-Path $target ('i40 Pilot full system ' + (Get-Date -Format 'yyyy-MM-dd'))
$null = New-Item -ItemType Directory -Force -Path $dated

$jobs = @(
    @{ src = 'C:\Users\th_em\ml_trading_system';                                          dst = (Join-Path $dated 'ml_trading_system'); name = 'system' },
    @{ src = 'C:\Users\th_em\.claude\projects\C--Users-th-em-ml-trading-system\memory';   dst = (Join-Path $dated 'claude-memory');      name = 'memory' },
    @{ src = 'C:\Users\th_em\Desktop\Trading Dashboard';                                  dst = (Join-Path $dated 'excel\Trading Dashboard'); name = 'excel' },
    @{ src = 'C:\Users\th_em\Desktop\MT5 data for Excel';                                 dst = (Join-Path $dated 'excel\MT5 data');     name = 'mt5 data' }
)

Write-Line "backing up to $dated  ($freeGB GB free on $drive)"
$copiedTotal = 0
$failedTotal = 0

foreach ($job in $jobs) {
    if (-not (Test-Path $job.src)) { Write-Line "  skip $($job.name): $($job.src) is missing"; continue }
    # /E subfolders including empty. /XJ skips junctions so it cannot wander outside the source.
    # /R:1 /W:1 so one locked file cannot hang the whole backup - the live app holds files open.
    $null = robocopy $job.src $job.dst /E /R:1 /W:1 /XJ /NFL /NDL /NP /NJH /NJS
    $code = $LASTEXITCODE
    # robocopy: 0 nothing to do, 1 copied, 2 extras, 3 both. 8 and above means some file failed.
    if ($code -ge 8) {
        $failedTotal++
        # Almost always one cause: a workbook open in Excel cannot be read by robocopy, and /R:1 gives
        # up fast by design. Everything else in the folder still copied, so say WHICH file and what
        # the drive already holds - "FAILED excel (robocopy 8)" on its own sends the reader hunting.
        $locks = @(Get-ChildItem -Path $job.src -Filter '~$*' -Force -Recurse -ErrorAction SilentlyContinue)
        $excelOpen = [bool](Get-Process EXCEL -ErrorAction SilentlyContinue)
        if ($locks.Count -gt 0 -or $excelOpen) {
            $held = ($locks | ForEach-Object { $_.Name -replace '^~\$', '' }) -join ', '
            Write-Line "  LOCKED $($job.name): open in Excel, so it was skipped$(if ($held) { " ($held)" })"
            foreach ($existing in @(Get-ChildItem -Path $job.dst -Filter '*.xlsx' -ErrorAction SilentlyContinue)) {
                if ($existing.Name -notlike '~$*') {
                    Write-Line "    the drive still holds $($existing.Name) from $($existing.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))"
                }
            }
            Write-Line "    close Excel and run this again to capture the current version"
        } else {
            Write-Line "  FAILED $($job.name) (robocopy $code)"
        }
    }
    else { $copiedTotal++; Write-Line "  ok $($job.name) (robocopy $code)" }
}

if ($failedTotal -gt 0) {
    Write-Line "finished WITH $failedTotal failure(s)"
    exit 1
}
Write-Line "finished - $copiedTotal folder(s) up to date"
exit 0
