# Desk tab for scripts\start_claude.cmd: resume the most recent Claude Code session in the live project
# folder, or start a new one when there is nothing to continue. Never trades.
# Kept in its own file because Windows Terminal splits its command line on ";" even inside quotes, which cut
# the fallback off when it was written inline.
# The project folder is the one holding this script's parent, never a written-down path, so a copy on
# another machine or on the USB drive resumes in ITS OWN folder rather than reaching back to this PC.
Set-Location (Split-Path -Parent $PSScriptRoot)
claude --continue
if ($LASTEXITCODE -ne 0) {
    Write-Host 'No session to continue here - starting a new Claude Code session.'
    claude
}
