import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, build_live_series


def test_dashboard_page():
    client = app.test_client()
    response = client.get('/')
    assert response.status_code == 200
    assert b'Dashboard' in response.data or b'Live Trading' in response.data


def test_signals_page():
    client = app.test_client()
    response = client.get('/signals')
    assert response.status_code == 200
    assert b'Signals' in response.data


def test_api_market_summary():
    client = app.test_client()
    response = client.get('/api/market-summary')
    assert response.status_code == 200
    data = response.get_json()
    assert 'symbols' in data
    assert len(data['symbols']) >= 2


def test_signal_history_page():
    client = app.test_client()
    response = client.get('/history')
    assert response.status_code == 200
    assert b'Signal History' in response.data


def test_signal_api_endpoint():
    client = app.test_client()
    response = client.get('/api/signals')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) >= 2


def test_live_data_api():
    client = app.test_client()
    response = client.get('/api/live-data')
    assert response.status_code == 200
    data = response.get_json()
    assert 'symbols' in data
    assert len(data['symbols']) >= 2


def test_price_history_api():
    client = app.test_client()
    response = client.get('/api/price-history/XAUUSD')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) >= 10


def test_build_live_series_falls_back_when_source_unavailable(monkeypatch):
    monkeypatch.setattr('app.fetch_intraday_prices_from_yahoo', lambda symbol, interval='5m', period='1d': [])
    monkeypatch.setattr('app.fetch_daily_prices_from_yahoo', lambda symbol, periods=20: [])
    series = build_live_series('XAUUSD')
    assert isinstance(series, list)
    assert len(series) >= 20


def test_control_panel_page():
    client = app.test_client()
    response = client.get('/control')
    assert response.status_code == 200
    assert b'Control Panel' in response.data


def test_settings_api_persists():
    client = app.test_client()
    response = client.post('/api/settings', data={'risk_mode': 'Aggressive', 'max_positions': '3'})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['risk_mode'] == 'Aggressive'
    assert payload['max_positions'] == 3


def test_health_endpoint():
    client = app.test_client()
    response = client.get('/api/health')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['status'] == 'ok'


def test_learn_page():
    client = app.test_client()
    response = client.get('/learn')
    assert response.status_code == 200
    assert b'Daily Learn Engine' in response.data


def test_api_learn_endpoint():
    client = app.test_client()
    response = client.get('/api/learn')
    assert response.status_code == 200
    data = response.get_json()
    assert 'chart_insights' in data
    assert 'chart_learn' in data
    assert 'train_status' in data
    assert 'learn_status' in data


def test_api_learn_status_endpoint():
    client = app.test_client()
    response = client.get('/api/learn-status')
    assert response.status_code == 200
    data = response.get_json()
    assert 'symbols' in data
    assert isinstance(data['symbols'], list)
    assert len(data['symbols']) == 2
    assert all('history' in item for item in data['symbols'])
    assert all('run_count' in item for item in data['symbols'])
    assert all('strategy_quality' in item for item in data['symbols'])


def test_live_plan_endpoint():
    client = app.test_client()
    response = client.get('/api/live-plan/XAUUSD')
    assert response.status_code == 200
    data = response.get_json()
    assert data['symbol'] == 'XAUUSD'
    assert 'entry' in data
    assert 'stop_loss' in data
    assert 'take_profit_1' in data
    assert 'confidence' in data
    assert 'grade' in data


def test_train_daily_endpoint():
    client = app.test_client()
    response = client.get('/api/train-daily')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert all(item.get('status') in ('trained', 'no_data') for item in data)


def test_train_status_endpoint():
    client = app.test_client()
    response = client.get('/api/train-status')
    assert response.status_code == 200
    data = response.get_json()
    assert 'history' in data
    assert 'last_updated' in data
    assert 'model_metrics' in data


def test_model_status_endpoint():
    client = app.test_client()
    response = client.get('/api/model-status')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert all('symbol' in item for item in data)


def test_chart_insights_endpoint():
    client = app.test_client()
    response = client.get('/api/chart-insights/XAUUSD')
    assert response.status_code == 200
    data = response.get_json()
    assert data['symbol'] == 'XAUUSD'
    assert 'summary' in data
