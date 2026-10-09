# Desk tab for scripts\start_claude.cmd: resume the most recent Claude Code session in the live project
# folder, or start a new one when there is nothing to continue. Never trades.
#
# Kept in its own file because Windows Terminal splits its command line on ";" even inside quotes, which cut
# the fallback off when it was written inline.
#
# The project folder is the one holding this script's parent, never a written-down path, so a copy on
# another machine or on the USB drive resumes in ITS OWN folder rather than reaching back to this PC.
#
# WHY THERE IS A LOG AND A SIZE CHECK HERE (9 October 2026)
# ---------------------------------------------------------
# The owner asked why Claude Code had not opened. The scheduled task had run at 06:54 and exited 0, and
# start_claude.log said "starting Windows Terminal with tabs bridge and desk ... started 06:56:45". The
# terminal was still open. But NO Claude session file had been created between 06:50 and 08:00, and the
# session the 8 October autostart produced was 0 bytes. So the desk tab had failed on two consecutive
# days and left no evidence at all, because this script recorded nothing.
#
# Everything below about WHY it failed had to be inferred, which is exactly the problem. The first fix is
# therefore the log: next time there is a transcript of what happened instead of a reconstruction.
#
# The size check is a mitigation for the most likely cause, and it is labelled as a hypothesis rather
# than a diagnosis. `claude --continue` resumes the most recent transcript, which after a long working
# day was 241,469,425 bytes across 61,119 lines - four times larger than anything that had resumed
# successfully before. Loading that on a machine with about 1.2 GB of free RAM is a plausible hang.
# Rather than wait on it forever and give the owner nothing, the script starts a fresh session and names
# the oversized transcript so it can still be resumed by hand with `claude --resume`.
#
# Set SMARTENTRY_MAX_RESUME_MB to change the threshold, or 0 to always attempt the resume.

Set-Location (Split-Path -Parent $PSScriptRoot)

$project = Get-Location
$logDir = Join-Path $project 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'claude_desk.log'

function Write-Desk([string]$message) {
    $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message)
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

Write-Desk ('===== desk tab starting in {0}' -f $project)

# The transcript `--continue` would pick: newest .jsonl for this project folder.
$limitMb = 150
if ($env:SMARTENTRY_MAX_RESUME_MB) {
    $parsed = 0
    if ([int]::TryParse($env:SMARTENTRY_MAX_RESUME_MB, [ref]$parsed)) { $limitMb = $parsed }
}

# Claude Code's folder name replaces : \ / AND underscores with dashes, so th_em becomes th-em and
# the directory is C--Users-th-em-ml-trading-system. Verified against the real folder rather than
# assumed: the first version left the underscore out, found no transcript, and would have made the
# size check a no-op that always resumed - a guard that silently never fires is worse than none.
$slug = ($project.Path -replace '[:\\/_]', '-')
$sessionDir = Join-Path $env:USERPROFILE ('.claude\projects\' + $slug)
$newest = $null
if (Test-Path $sessionDir) {
    $newest = Get-ChildItem -Path $sessionDir -Filter *.jsonl -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

$resume = $true
if ($newest) {
    $mb = [math]::Round($newest.Length / 1MB, 1)
    Write-Desk ('newest transcript {0} is {1} MB' -f $newest.Name, $mb)
    if ($limitMb -gt 0 -and $mb -gt $limitMb) {
        $resume = $false
        Write-Desk ('that is over the {0} MB resume limit, so starting a FRESH session instead.' -f $limitMb)
        Write-Desk ('nothing is lost: resume it by hand with  claude --resume {0}' -f $newest.BaseName)
    }
} else {
    Write-Desk 'no previous transcript found for this folder'
}

if ($resume) {
    Write-Desk 'running: claude --continue'
    claude --continue
    $code = $LASTEXITCODE
    Write-Desk ('claude --continue exited {0}' -f $code)
    if ($code -ne 0) {
        Write-Desk 'nothing to continue here - starting a new Claude Code session.'
        claude
        Write-Desk ('claude exited {0}' -f $LASTEXITCODE)
    }
} else {
    claude
    Write-Desk ('claude exited {0}' -f $LASTEXITCODE)
}

Write-Desk '===== desk tab finished'
