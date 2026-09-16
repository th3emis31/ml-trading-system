# Desk tab for scripts\start_claude.cmd: resume the most recent Claude Code session in the live project
# folder, or start a new one when there is nothing to continue. Never trades.
# Kept in its own file because Windows Terminal splits its command line on ";" even inside quotes, which cut
# the fallback off when it was written inline.
Set-Location 'C:\Users\th_em\ml_trading_system'
claude --continue
if ($LASTEXITCODE -ne 0) {
    Write-Host 'No session to continue here - starting a new Claude Code session.'
    claude
}
