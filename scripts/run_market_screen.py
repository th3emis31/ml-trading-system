"""Run the market screen over a list of symbols, saving after each one.

A file rather than an inline -c command because the symbol list, the terminal path and the Windows
quoting fought each other every time it was launched inline, and a launch that silently does nothing
is worse than one that fails loudly.

    python scripts/run_market_screen.py                 # the default watchlist
    python scripts/run_market_screen.py DELL INTEL      # any symbols

Re-running the same list after a kill resumes: symbols already in data/market_screen.json are kept
and only the missing ones are screened.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.market_screener import screen

TERMINAL = r"C:\Users\th_em\AppData\Roaming\MetaTrader\terminal64.exe"
DEFAULT = ["XAUUSD", "NAS100", "BTCUSD", "INTEL", "AAPL", "MSFT", "NVDAUSD", "TSLA", "SP500"]

if __name__ == "__main__":
    symbols = sys.argv[1:] or DEFAULT
    print(f"screening {len(symbols)}: {', '.join(symbols)}", flush=True)
    report = screen(symbols, TERMINAL, 12000, 5, timeout=700)
    print(f"done: {report.get('done')} symbols, {len(report.get('with_evidence') or [])} with evidence",
          flush=True)
