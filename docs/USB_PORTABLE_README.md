# i40 Pilot — full system copy, 25 September 2026

Everything the trading system is, copied from `C:\Users\th_em\ml_trading_system` on 25 Sep 2026.
**3,797 files, 4,142 MB.** Nothing else on this drive was touched — your own 20 items in
`A. Trading system Themis` are exactly as they were.

---

## What is in here

| folder | what it holds | size |
|---|---|---|
| `ml_trading_system\src` | the code: signals, strategies, backtests, memory, governance, providers | 2.4 MB |
| `ml_trading_system\data` | every recorded result, learning decision, research run, journal | 3,007 MB |
| `ml_trading_system\models` | the live models and every archived version | 961 MB |
| `ml_trading_system\.claude\memory` | **BASELINE.md** — every measured result including the failures | 0.4 MB |
| `ml_trading_system\.claude\skills` | the 15 skills, each with its acceptance checks | 15 files |
| `ml_trading_system\docs` | the architecture and build map | |
| `ml_trading_system\scripts` | the scheduled jobs and tools | 57 files |
| `ml_trading_system\tests` | 294 test files — what stops it lying to you | 2.4 MB |
| `ml_trading_system\strategies` | strategy documents and the SmartEntry set files | 42 files |
| `claude-memory` | the durable lessons that survive between sessions | 36 files |
| `excel` | the Trading Business Dashboard and its MT5 data | 10.8 MB |

The two that matter most if everything else were lost: **`.claude\memory\BASELINE.md`**, which is every
result ever measured — including every rejected idea, which is what stops the same ground being
re-walked — and **`data\`**, which is the record of what the system actually did.

---

## Will it run from this drive?

**The system itself: yes.** It finds its own `models\` and `data\` relative to wherever it sits, so
copying the folder is enough. Nothing points back at the C: drive for its own files.

**But it needs three things from the machine it runs on**, and they are not on this drive because
they cannot be:

1. **Python 3.10** with the packages installed (`pandas`, `scikit-learn`, `flask`, `MetaTrader5`,
   `openpyxl`, `pywin32`). A `pip install` on a fresh machine.
2. **MetaTrader** - the terminals are installed software, so they cannot travel on a drive. WHERE
   they are, though, is now config rather than code: `config\machine.json` holds every
   machine-specific path, and on a new PC you edit that one file. Run `python -m src.system_doctor`
   first: it lists each configured path with found / NOT FOUND beside it, so you can see exactly what
   to re-point. `tests	est_machine_portability.py` now fails if a path naming this particular PC
   goes back into the source, so it cannot quietly creep back in.
3. **An AI provider.** Online it uses the Claude CLI. **Offline it needs a local model, and none is
   installed yet** — the system reports this honestly as `independent: false`. Installing Ollama and
   pulling `qwen2.5-coder:7b` (~4.7 GB) is what makes it work with no internet at all.

This copy is **a complete backup and a working system**. To run it FROM this drive, name the folder
as its home and everything - models, data, memory, config - comes from here:

```
set I40_HOME=D:\A. Trading system Themis\i40 Pilot full system 2026-09-25\ml_trading_system
python -m src.system_doctor
```

Verified on 25 September 2026 by doing exactly that: this copy resolved its home, found its 68 model
files and its data folder **on the drive**, and reported all four configured terminals present.
Leaving `I40_HOME` unset changes nothing - the system behaves as it always has on the C: drive.

**What is still not proven, so it is not claimed:** it has not yet been run on a second PC, and fully
offline still needs a local model, which is not installed. The system reports that itself as
`independent: false` rather than pretending otherwise; installing Ollama and pulling
`qwen2.5-coder:7b` (~4.7 GB) is what closes it.

---

## To restore it

Copy `ml_trading_system` back to a folder on the target machine, then from inside it:

```
python -m pip install -r requirements.txt      (if present, else install the packages above)
python app.py                                   serves on port 5000
python -m src.system_doctor                     checks the whole system and never trades
```

The doctor is the right first command anywhere: it reports what is working, what is missing and what
it cannot reach, and it places no orders.

---

## What the system knew on the day this was copied

- **101 closed trades** from its own strategies: **net +394.54**, profit factor **1.62**, win rate 46.5%
- Split by market: **XAUUSD +446.03** over 70 trades, **BTCUSD −51.49** over 31
- The gold machine-learning model measured **worse than chance** — 0.6th percentile over 254 trades
- SmartEntry V9 on gold: **7 of 8 years profitable**, and its stop is dated rather than wrong, because
  it is a fixed point count and gold tripled
- Bitcoin under that EA has a real edge of 0.0823 per trade against a 0.169 spread — **the rules work
  and the venue charges twice what they are worth**
- Build map: **8 of 10 steps done**

All of it, with the workings, is in `.claude\memory\BASELINE.md`.
