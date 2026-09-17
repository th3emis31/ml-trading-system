#!/usr/bin/env bash
# Premarket gappers scanner.
#
# 1. WebFetch https://finance.yahoo.com/markets/stocks/gainers/ -> ticker, price, % change, volume per row.
# 2. Filters: gap_pct > 5, price > 3, premarket_volume > 50000; top 10 by gap_pct.
# 3. For each of the 10, WebFetch https://www.benzinga.com/quote/{TICKER} for the catalyst (in parallel).
#    (Not Yahoo's /quote/{TICKER}/news/ endpoint: it returns HTTP 503.)
# 4. Writes ./premarket_gappers_YYYY-MM-DD.json and prints a one-line summary.
#
# WebFetch is a Claude Code tool, so each fetch runs a headless `claude -p` call allowed to use WebFetch only
# (no Bash, no file edits), started from a temporary folder so no project context or hooks are loaded.
#
# Data caveat: Yahoo's gainers page lists the % change and volume of the CURRENT session. Before the US open
# that is the premarket move; during or after the session it is the regular-session change and day volume.
# They are stored in the requested gap_pct / premarket_volume fields, and "scanned_at" (UTC) shows when.
#
# Usage: ./premarket_gappers.sh        (expected runtime 60-90 s; needs `claude` and `python` on PATH)

set -u
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
STAMP_DATE="$(date -u +%Y-%m-%d)"
OUT_FILE="$OUT_DIR/premarket_gappers_${STAMP_DATE}.json"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PY="$(command -v python || command -v python3)"
CLAUDE_FLAGS=(-p --model sonnet --output-format text --allowedTools WebFetch
              --disallowedTools "Bash,Edit,Write,NotebookEdit,Task,Agent,Glob,Grep,Read")

fetch_with_claude() {   # $1 = instruction text, $2 = output file
  # The prompt goes through stdin: --disallowedTools takes a variable-length list and would swallow a trailing argument.
  (cd "$WORK" && printf '%s' "$1" | timeout 150 claude "${CLAUDE_FLAGS[@]}" > "$2" 2> "$2.err") || true
}

# ------------------------------------------------------------------ 1. the gainers table
fetch_with_claude "Call the WebFetch tool on https://finance.yahoo.com/markets/stocks/gainers/ with the prompt: \
\"List every row of the gainers table. For each row give symbol, price, percent change and volume.\" \
Then output ONLY a JSON array, no prose, no code fences, one object per row: \
{\"symbol\": \"ABC\", \"price\": 12.34, \"change_pct\": 7.5, \"volume\": 1200000}. \
price and change_pct are plain numbers (no %, no +). volume is an integer: convert 1.2M to 1200000 and 850.3K to 850300. \
If the fetch fails, output []." "$WORK/gainers.txt"

"$PY" - "$WORK/gainers.txt" "$WORK/top10.json" <<'PY'
import json, re, sys
raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
match = re.search(r"\[.*\]", raw, re.S)
rows = []
if match:
    try:
        rows = json.loads(match.group(0))
    except ValueError:
        rows = []
def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").replace(",", "").replace("%", "").replace("+", "").strip()
    mult = 1.0
    if s[-1:].upper() in "KMB" and s[:-1]:
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}[s[-1].upper()]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None
clean = []
for r in rows if isinstance(rows, list) else []:
    sym = str(r.get("symbol") or "").strip().upper()
    price, gap, vol = num(r.get("price")), num(r.get("change_pct")), num(r.get("volume"))
    if sym and price is not None and gap is not None and vol is not None and gap > 5 and price > 3 and vol > 50000:
        clean.append({"symbol": sym, "price": round(price, 2), "gap_pct": round(gap, 2), "premarket_volume": int(vol)})
clean.sort(key=lambda r: r["gap_pct"], reverse=True)
json.dump({"parsed_rows": len(rows) if isinstance(rows, list) else 0, "top": clean[:10]}, open(sys.argv[2], "w"))
PY

mapfile -t SYMBOLS < <("$PY" -c "import json,sys; [print(r['symbol']) for r in json.load(open(sys.argv[1]))['top']]" "$WORK/top10.json")

# ------------------------------------------------------------------ 2. catalysts, in parallel
for T in "${SYMBOLS[@]}"; do
  fetch_with_claude "Call the WebFetch tool on https://www.benzinga.com/quote/${T} with exactly this prompt: \
\"What recent news or catalyst is driving ${T} stock today? Return a one-sentence summary, then up to 2 recent headlines verbatim. Just the data — no commentary.\" \
Then output ONLY a JSON object, no prose, no code fences: {\"catalyst\": \"one sentence\", \"headlines\": [\"headline 1\", \"headline 2\"]}. \
If the fetch fails or the page has no usable news, output {\"catalyst\": null, \"headlines\": []}." "$WORK/news_${T}.txt" &
done
wait

# ------------------------------------------------------------------ 3. assemble, save, summarise
"$PY" - "$WORK" "$OUT_FILE" <<'PY'
import json, re, sys, os
from datetime import datetime, timezone
work, out_file = sys.argv[1], sys.argv[2]
top = json.load(open(os.path.join(work, "top10.json")))["top"]
gappers = []
for rank, row in enumerate(top, 1):
    catalyst, headlines = None, []
    path = os.path.join(work, f"news_{row['symbol']}.txt")
    try:
        raw = open(path, encoding="utf-8", errors="replace").read()
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
        if isinstance(data.get("catalyst"), str) and data["catalyst"].strip():
            catalyst = data["catalyst"].strip()
        if isinstance(data.get("headlines"), list):
            headlines = [str(h).strip() for h in data["headlines"] if str(h).strip()][:2]
    except (OSError, ValueError, AttributeError):
        catalyst, headlines = None, []
    if catalyst is None:
        headlines = []
    gappers.append({"rank": rank, **row, "catalyst": catalyst, "headlines": headlines})
result = {"scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "gappers": gappers}
with open(out_file, "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=2, ensure_ascii=False)
top3 = ", ".join(f"{g['symbol']} ({g['gap_pct']}%) — {g['catalyst'] or 'no catalyst found'}" for g in gappers[:3])
print(f"Premarket Gappers: {len(gappers)} names. Top: {top3}" if gappers else "Premarket Gappers: 0 names.")
PY
