"""Private task routes must enforce the same owner policy as report routes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.routers import analysis
from app.routers.auth_db import get_current_user
from tests.test_report_ownership import Collection, record


@pytest.fixture
def api(monkeypatch):
    import app.core.database as database
    rows=[dict(task_id='task-b',user_id='user-b',status='completed',result={'summary':'private'})]
    reports=[dict(record('b','user-b'),task_id='task-b')]
    db=SimpleNamespace(analysis_tasks=Collection(rows),analysis_reports=Collection(reports))
    monkeypatch.setattr(database,'get_mongo_db',lambda:db)
    svc=SimpleNamespace(get_task_status=AsyncMock(return_value=None),memory_manager=SimpleNamespace(remove_task=AsyncMock(),update_task_status=AsyncMock()))
    monkeypatch.setattr(analysis,'get_simple_analysis_service',lambda:svc)
    identity={'id':'user-a','is_admin':False}
    app=FastAPI();app.include_router(analysis.router,prefix='/api/analysis')
    app.dependency_overrides[get_current_user]=lambda:identity
    with TestClient(app) as client:
        yield client, identity, svc, db


@pytest.mark.parametrize('storage',['memory','reports','tasks','legacy-analysis-id'])
@pytest.mark.parametrize('endpoint',['status','result'])
def test_owner_required_across_memory_and_database_fallbacks(api, storage, endpoint):
    client,user,svc,db=api
    if storage=='memory':
        svc.get_task_status.return_value={'user_id':'user-b','status':'completed','result_data':{'summary':'private'}}
    elif storage=='tasks':
        db.analysis_reports.rows=[]
    elif storage=='legacy-analysis-id':
        del db.analysis_reports.rows[0]['task_id']
        db.analysis_tasks.rows[0]['result']['analysis_id']='b'
    path='/api/analysis/tasks/task-b/'+endpoint
    assert client.get(path).status_code==404
    user['id']='user-b'
    assert client.get(path).status_code==200


@pytest.mark.parametrize('method,suffix',[('post','/mark-failed'),('delete','')])
def test_other_user_cannot_mutate_memory_or_database(api, method, suffix):
    client,user,svc,_=api
    svc.get_task_status.return_value={'user_id':'user-b','status':'completed'}
    assert getattr(client,method)('/api/analysis/tasks/task-b'+suffix).status_code==404
    svc.memory_manager.remove_task.assert_not_awaited()
    svc.memory_manager.update_task_status.assert_not_awaited()


def test_websocket_rejects_other_owner_before_subscribing(api):
    client,_,svc,_=api
    svc.get_task_status.return_value={'user_id':'user-b'}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/api/analysis/ws/task/task-b'):
            pytest.fail('Unauthorized stream accepted')


def test_all_tasks_is_scoped_for_users_and_global_only_for_admin(api):
    client,user,svc,_=api
    svc.list_all_tasks=AsyncMock(return_value=[{'task_id':'task-b'}])
    svc.list_user_tasks=AsyncMock(return_value=[])
    assert client.get('/api/analysis/tasks/all').json()['data']['tasks']==[]
    svc.list_all_tasks.assert_not_awaited()
    assert svc.list_user_tasks.call_args.kwargs['user_id']=='user-a'
    user['is_admin']=True
    assert client.get('/api/analysis/tasks/all').json()['data']['tasks']==[{'task_id':'task-b'}]


def test_auth_logging_does_not_echo_bearer_token(monkeypatch, caplog):
    from app.routers import auth_db
    from app.services.auth_service import AuthService
    monkeypatch.setattr(AuthService,'verify_token',lambda token:None)
    app=FastAPI()
    app.include_router(auth_db.router,prefix='/api/auth')
    token='regression-private-token-12345'
    with caplog.at_level('DEBUG'), TestClient(app) as client:
        assert client.get('/api/auth/me',headers={'Authorization':'Bearer '+token}).status_code==401
    assert token not in caplog.text and token[:20] not in caplog.text
