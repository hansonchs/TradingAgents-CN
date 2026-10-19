import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch

# Build a minimal app that mounts only the stocks router to avoid triggering app.main lifespan
from app.routers import stocks as stocks_router
from app.routers.auth_db import get_current_user


def create_test_app():
    app = FastAPI()
    app.include_router(stocks_router.router, prefix="/api")
    # Override auth dependency to bypass Bearer token in tests
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "test",
        "username": "test",
        "is_admin": True,
        "roles": ["admin"],
    }
    return app


@pytest.fixture()
def client():
    app = create_test_app()
    with TestClient(app) as c:
        yield c


def test_kline_ok_source_and_adj(client):
    # Mock DataSourceManager fallback to return 2 bars
    items = [
        {"time": "2024-09-01", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 100000.0, "amount": 2.3e6},
        {"time": "2024-09-02", "open": 10.2, "high": 10.8, "low": 10.0, "close": 10.6, "volume": 120000.0, "amount": 2.8e6},
    ]
    with patch("app.services.data_sources.manager.DataSourceManager.get_kline_with_fallback", return_value=(items, "tushare")):
        resp = client.get("/api/stocks/000001/kline", params={"period": "day", "limit": 2, "adj": "qfq"})
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("success") is True
        data = body.get("data")
        assert data["code"] == "000001"
        assert data["period"] == "day"
        assert data["limit"] == 2
        assert data["adj"] == "qfq"
        assert data["source"] == "tushare"
        assert isinstance(data["items"], list) and len(data["items"]) == 2


def test_kline_invalid_period_returns_400(client):
    resp = client.get("/api/stocks/000001/kline", params={"period": "2m", "limit": 10})
    assert resp.status_code == 400
    j = resp.json()
    # FastAPI default error format
    assert j["detail"].startswith("不支持的period")


def news_services(monkeypatch, rows, unavailable=False):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import app.services.news_data_service as news
    import app.worker.akshare_sync_service as worker
    query = AsyncMock(return_value=rows)
    service = SimpleNamespace(query_news=query)
    monkeypatch.setattr(news, 'get_news_data_service', AsyncMock(return_value=service))
    sync = AsyncMock()
    if unavailable:
        sync.side_effect = RuntimeError('offline news service')
    monkeypatch.setattr(worker, 'get_akshare_sync_service', sync)
    return query, sync


def test_news_preserves_source_type_and_requested_publication_window(client, monkeypatch):
    rows = [dict(title='Announcement', source='fixture', publish_time='2024-09-02', url='https://example.test/a', type='announcement'),
            dict(title='News', source='fixture', publish_time='2024-09-02', url='https://example.test/b')]
    query, sync = news_services(monkeypatch, rows)
    response = client.get('/api/stocks/000001/news', params=dict(days=2, limit=2, include_announcements=True))
    assert response.status_code == 200
    data = response.json()['data']
    assert data['status'] == 'ready' and data['source'] == 'database'
    assert data['items'][0]['type'] == 'announcement'
    assert data['items'][0]['source'] == 'fixture'
    params = query.call_args.args[0]
    assert params.end_time - params.start_time == __import__('datetime').timedelta(days=2)
    sync.assert_not_awaited()
    response = client.get('/api/stocks/000001/news', params=dict(include_announcements=False))
    assert [item['type'] for item in response.json()['data']['items']] == ['news']


def test_failed_news_and_unsupported_market_are_not_healthy_empty_results(client, monkeypatch):
    news_services(monkeypatch, [], unavailable=True)
    data = client.get('/api/stocks/000001/news').json()['data']
    assert data['status'] == 'unavailable' and data['source'] is None
    hk = client.get('/api/stocks/00700.HK/news').json()['data']
    assert hk['status'] == 'unsupported'


@pytest.mark.parametrize('stats', [None, {}, {'errors':['provider unavailable']},
    {'success_count':1,'error_count':1}, {'success_count':0}, {'success_count':1,'error_count':0,'errors':[]}])
def test_nonthrowing_failed_or_ambiguous_empty_sync_stays_unavailable(client, monkeypatch, stats):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import app.worker.akshare_sync_service as worker
    news_services(monkeypatch, [])
    sync=AsyncMock(return_value=stats)
    monkeypatch.setattr(worker, 'get_akshare_sync_service', AsyncMock(return_value=SimpleNamespace(sync_news_data=sync)))
    data=client.get('/api/stocks/000001/news').json()['data']
    assert data['status']=='unavailable' and data['source'] is None
    sync.assert_awaited_once()
