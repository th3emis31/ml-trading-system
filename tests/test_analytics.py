import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app


def test_analytics_page():
    client = app.test_client()
    response = client.get('/analytics')
    assert response.status_code == 200
    assert b'Analytics' in response.data


def test_knowledge_api():
    client = app.test_client()
    response = client.post('/api/knowledge', data={'text': 'BTCUSD breakout setup', 'source': 'notes'})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['count'] >= 1
