"""Re-test the screen's best markets through the LIVE ENGINE, which is the test that counts.

The screener ranks on rf_proba because the live engine is fifteen times slower and an eleven-symbol
screen in live mode is many hours. That makes the screen a RELATIVE ranking, not a verdict. Anything
that ranks well has to come through here before a decision follows from it, because live_engine
re-predicts every out-of-sample bar exactly as the dashboard would have done.

Results go to their own file, so a screening pass can never be mistaken for a verification.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import market_screener as ms

from src.runtime_paths import installed_terminal

TERMINAL = installed_terminal("mt5_strategies")

if __name__ == "__main__":
    symbols = sys.argv[1:] or ["NAS100", "AAPL", "SP500"]
    ms.RESULTS_PATH = Path(__file__).resolve().parents[1] / "data" / "market_verify_live.json"
    print(f"live-engine verification of {', '.join(symbols)}", flush=True)
    report = ms.screen(symbols, TERMINAL, 12000, 5, timeout=2400, mode="live_engine")
    print(f"done: {report.get('done')}", flush=True)
