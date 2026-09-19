#!/usr/bin/env bash
# Trend Join Long scanner — gold and bitcoin, on the broker candles the system trades.
#
#   ./tjl_scanner.sh              scan now; refuses outside 10:00-15:30 New York
#   ./tjl_scanner.sh --force      scan anyway and mark the report as forced
#   TJL_DIR=/somewhere ./tjl_scanner.sh    write the report elsewhere
#
# Writes ./tjl_watchlist_YYYY-MM-DD_HHMMET.json and prints one line per ticker.
# Reads candles through the running app; it never connects to MetaTrader itself and
# never places an order.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${TJL_PYTHON:-python}"
DIR="${TJL_DIR:-$HERE}"

cd "$HERE"
if ! "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=10)" >/dev/null 2>&1; then
  echo "the trading app is not answering on port 5000 — the scanner reads its broker candles through it" >&2
  exit 1
fi

exec "$PY" -m src.tjl_scanner --dir "$DIR" "$@"
