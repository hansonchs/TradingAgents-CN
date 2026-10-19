"""Real report routes with an in-memory Mongo substitute and two authenticated users."""
import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import reports
from app.routers.auth_db import get_current_user


def matches(doc, query):
    for key, value in query.items():
        if key == '$and':
            if not all(matches(doc, part) for part in value): return False
        elif key == '$or':
            if not any(matches(doc, part) for part in value): return False
        elif isinstance(value, dict) and '$in' in value:
            if str(doc.get(key)) not in [str(item) for item in value['$in']]: return False
        elif doc.get(key) != value:
            return False
    return True


class Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, *args): return self
    def skip(self, count): self.rows = self.rows[count:]; return self
    def limit(self, count): self.rows = self.rows[:count]; return self
    async def __aiter__(self):
        for row in self.rows: yield row


class Collection:
    def __init__(self, rows): self.rows = rows
    async def find_one(self, query, *args): return next((r for r in self.rows if matches(r, query)), None)
    def find(self, query): return Cursor([r for r in self.rows if matches(r, query)])
    async def count_documents(self, query): return sum(matches(r, query) for r in self.rows)
    async def delete_one(self, query):
        row = await self.find_one(query)
        if row: self.rows.remove(row)
        return SimpleNamespace(deleted_count=int(row is not None))


def record(name, owner):
    return dict(_id=name, analysis_id=name, stock_symbol='AAA', stock_name='Fixture', user_id=owner,
                analysis_date='2026-10-19', created_at=dt.datetime(2026,10,19),
                reports={'market_report':'Private research'}, decision={'analysis_status':'unavailable'})


@pytest.fixture
def api(monkeypatch):
    db=SimpleNamespace(analysis_reports=Collection([record('a','user-a'),record('b','user-b'),record('legacy',None)]),
                       analysis_tasks=Collection([dict(task_id='task-b', user_id='user-b', result=record('task-b','user-b'))]))
    monkeypatch.setattr(reports, 'get_mongo_db', lambda: db)
    identity=SimpleNamespace(user={'id':'user-a','is_admin':False})
    app=FastAPI(); app.include_router(reports.router)
    app.dependency_overrides[get_current_user]=lambda: identity.user
    with TestClient(app) as client:
        yield client, identity, db


@pytest.mark.parametrize('method,path', [('get','/b/detail'),('get','/b/content/market_report'),
                                         ('get','/b/download?format=json'),('delete','/b'),('get','/task-b/detail')])
def test_other_users_cannot_read_download_delete_or_restore_a_report(api, method, path):
    client, identity, db=api
    assert getattr(client, method)('/api/reports'+path).status_code == 404
    assert any(r['_id']=='b' for r in db.analysis_reports.rows)
    identity.user={'id':'user-b','is_admin':False}
    assert getattr(client, method)('/api/reports'+path).status_code == 200


def test_listing_ownership_legacy_policy_and_research_status(api):
    client, identity, _=api
    listed=client.get('/api/reports/list').json()['data']
    assert listed['total']==1
    assert [r['id'] for r in listed['reports']]==['a']
    assert client.get('/api/reports/a/detail').json()['data']['analysis_status']=='unavailable'
    assert client.get('/api/reports/a/content/market_report').status_code==200
    assert client.get('/api/reports/a/download?format=json').status_code==200
    identity.user={'id':'admin','is_admin':True}
    assert client.get('/api/reports/legacy/detail').status_code==200
    assert client.get('/api/reports/list').json()['data']['total']==3
