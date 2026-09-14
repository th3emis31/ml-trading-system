# ML Trading System for XAUUSD and BTCUSD

This project provides a starter machine-learning trading system for two markets:
- XAUUSD (gold)
- BTCUSD (bitcoin)

## Structure
- src/data.py: synthetic market data generation
- src/features.py: feature engineering and target creation
- src/train.py: model training and evaluation
- src/backtest.py: simple backtesting loop
- src/predict.py: inference entrypoint

## Quickstart
1. Install dependencies: pip install -r requirements.txt
2. Run tests: pytest -q
3. Train a model: python -m src.train
4. Run backtest: python -m src.backtest
5. Train on real market history: python -m src.train
6. Start the dashboard: python run.py
7. Smart launcher entrypoint: python launcher.py

## SmartEntryProAI Phase 1 Structure (In Existing Project)
- launcher.py
- requirements.txt
- README.md
- ai/
- dashboard/
- trading/
- voice/
- memory/
- config/
- assets/
- logs/
- tests/

## New Learning and Chart Access
- Access chart insights: `GET /api/chart-insights/XAUUSD`
- Trigger learning cycle + chart summary: `GET /api/chart-learn/XAUUSD`
- Run daily retrain cycle: `GET /api/train-daily`
- See available learning endpoints: `GET /learn`

## Phase 2 (Implemented Slice)
- AI chat endpoint: `POST /api/ai/chat` with JSON `{ "message": "..." }`
- AI chat history: `GET /api/ai/history`
- Dashboard now includes a SmartEntryProAI chat panel backed by persistent memory (`data/ai_chat_memory.json`)
- Voice wakeword baseline:
	- `GET /api/voice/status`
	- `POST /api/voice/control` with `{ "enabled": true, "wakeword": "hey jarvis" }`
	- `POST /api/voice/process` with `{ "transcript": "hey jarvis analyze xauusd" }`
- MT5 service layer with resilient fallback:
	- `GET /api/mt5/status`
	- `GET /api/mt5/account`
	- `GET /api/mt5/market?symbols=XAUUSD,BTCUSD`
	- If MetaTrader5 is unavailable, market endpoint falls back to Yahoo quote/live-series data.
- Coding assistant APIs (MQL5/Pine templates):
	- `GET /api/code/supported`
	- `POST /api/code/generate` with `{ "language": "mql5|pine", "template": "signal_strategy|risk_manager|session_filter", "symbol": "XAUUSD", "timeframe": "H1" }`
	- `POST /api/code/explain` with `{ "code": "..." }`

## Safe ML/LSTM reliability upgrades
- RF probability calibration is now tracked with reliability metrics (`brier_score`, `ece_10`).
- Time-series CV supports a leakage-reduction gap (`cv_gap`) in metrics.
- Signal payload includes advisory-only `risk_guardrails` (no hard blocking of learning/signals).
- Monitoring endpoints now expose richer model health:
  - `GET /api/model-status`
  - `GET /api/learn-status`
  - `GET /api/train-status`

## Voice conversation Phase 1 (additive, safe fallback)
- Existing endpoints remain unchanged and supported:
  - `GET /api/voice/status`
  - `POST /api/voice/process`
- New realtime session endpoints (fallback to existing voice processing on failure):
  - `GET /api/voice/realtime/status`
  - `POST /api/voice/realtime/start`
  - `POST /api/voice/realtime/chunk`
  - `POST /api/voice/realtime/stop`

## Dashboard Launch
- Default URL: http://127.0.0.1:5001
- Optional environment variables: `FLASK_HOST`, `FLASK_PORT`, `FLASK_DEBUG`
- If `5000` is already busy locally, `run.py` will automatically try the next free port and print it in the terminal.
- If you just want a clean local session right away, you can also start with `FLASK_PORT=5001 python run.py`.

## Render Deployment
- Render service type: Web Service
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn run:app`
- Render will inject `PORT`; the app now listens on that port automatically.
