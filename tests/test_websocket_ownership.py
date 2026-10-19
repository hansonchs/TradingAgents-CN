"""Exercise the mounted notification sockets with two distinct authenticated users."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import threading
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.routers import websocket_notifications as ws
from tests.test_report_ownership import Collection


@pytest.fixture
def client(monkeypatch):
    async def identity(authorization):
        if authorization not in ['Bearer a', 'Bearer b']:
            raise HTTPException(status_code=401)
        return {'id':'user-'+authorization[-1], 'is_admin':False}
    monkeypatch.setattr(ws,'get_current_user',identity)
    manager=ws.ConnectionManager()
    manager.disconnected={user:threading.Event() for user in ['user-a','user-b']}
    disconnect=manager.disconnect
    async def observed_disconnect(socket, user_id):
        await disconnect(socket,user_id)
        manager.disconnected[user_id].set()
    manager.disconnect=observed_disconnect
    monkeypatch.setattr(ws,'manager',manager)
    import app.core.database as database
    db=SimpleNamespace(analysis_tasks=Collection([{'task_id':'private-b','user_id':'user-b'}]),analysis_reports=Collection([]))
    monkeypatch.setattr(database,'get_mongo_db',lambda:db)
    import app.services.simple_analysis_service as service
    monkeypatch.setattr(service,'get_simple_analysis_service',lambda:SimpleNamespace(get_task_status=AsyncMock(return_value={'user_id':'user-b'})))
    app=FastAPI();app.include_router(ws.router,prefix='/api')
    with TestClient(app) as client:
        yield client


def test_notifications_and_task_progress_are_personal(client):
    with client.websocket_connect('/api/ws/notifications?token=a') as a, client.websocket_connect('/api/ws/notifications?token=b') as b:
        assert a.receive_json()['data']['user_id']=='user-a'
        assert b.receive_json()['data']['user_id']=='user-b'
        client.portal.call(ws.send_task_progress_via_websocket,'private-b',{'task_id':'private-b','summary':'private'})
        client.portal.call(ws.send_task_progress_via_websocket,'unknown',{'summary':'legacy unknown owner'})
        client.portal.call(ws.send_notification_via_websocket,'user-a',{'title':'A only'})
        assert a.receive_json()=={'type':'notification','data':{'title':'A only'}}
        assert b.receive_json()['data']['task_id']=='private-b'
    assert all(event.wait(timeout=2) for event in ws.manager.disconnected.values())
    assert ws.manager.get_stats()['total_connections']==0


@pytest.mark.parametrize('path',['/api/ws/notifications?token=invalid','/api/ws/tasks/private-b?token=a'])
def test_invalid_identity_and_other_owners_task_are_rejected(client,path):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path):
            pytest.fail('Unauthorized socket accepted')


def test_task_socket_owner_can_connect(client):
    with client.websocket_connect('/api/ws/tasks/private-b?token=b') as b:
        assert b.receive_json()['data']['task_id']=='private-b'
