@echo off
rem Opens the two things needed to install the SmartEntry TradingView panel.
rem
rem The install itself is three clicks and cannot be automated: edge://extensions is a browser
rem-internal page, and browsers deliberately refuse to let anything drive it - the same protection
rem that stops a page installing an extension behind your back.
rem
rem   1. Developer mode  (bottom-left toggle)
rem   2. Load unpacked
rem   3. Pick the folder that opens in Explorer
echo Opening edge://extensions and the extension folder...
start "" explorer.exe "C:\Users\th_em\ml_trading_system\extensions\smartentry_tv"
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" "edge://extensions/"
echo.
echo   1) Turn on Developer mode (bottom left)
echo   2) Click "Load unpacked"
echo   3) Choose the folder that just opened: extensions\smartentry_tv
echo.
echo Then refresh tradingview.com/chart - the panel appears top right.
