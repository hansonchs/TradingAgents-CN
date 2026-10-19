"""Storage initialization uses real configuration and never emits connection secrets."""
import logging
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.core.config_bridge import initialize_research_storage


@pytest.mark.parametrize('enabled,uri',[('false','mongodb://unused/'),('true',''),('true','  '),('true','未设置'),('true','https://invalid.test/')])
def test_disabled_or_missing_connection_never_connects(monkeypatch,enabled,uri):
    import tradingagents.config.mongodb_storage as module
    factory=Mock()
    monkeypatch.setattr(module,'MongoDBStorage',factory)
    monkeypatch.setenv('USE_MONGODB_STORAGE',enabled)
    monkeypatch.setenv('MONGODB_CONNECTION_STRING',uri)
    manager=SimpleNamespace(mongodb_storage='old')
    initialize_research_storage(manager)
    factory.assert_not_called()
    assert manager.mongodb_storage is None


@pytest.mark.parametrize('outcome',['connected','disconnected','exception'])
def test_real_uri_is_forwarded_and_credential_never_logged(monkeypatch,caplog,outcome):
    import tradingagents.config.mongodb_storage as module
    uri='mongodb://fixture-user:private-canary-password@example.test:27017/'
    storage=SimpleNamespace(is_connected=lambda:outcome=='connected')
    factory=Mock(return_value=storage)
    if outcome=='exception':factory.side_effect=RuntimeError(uri)
    monkeypatch.setattr(module,'MongoDBStorage',factory)
    monkeypatch.setenv('USE_MONGODB_STORAGE','true')
    monkeypatch.setenv('MONGODB_CONNECTION_STRING',uri)
    monkeypatch.setenv('MONGODB_DATABASE_NAME','test-research')
    manager=SimpleNamespace(mongodb_storage='old')
    with caplog.at_level(logging.INFO):initialize_research_storage(manager)
    factory.assert_called_once_with(connection_string=uri,database_name='test-research')
    assert manager.mongodb_storage is (storage if outcome=='connected' else None)
    assert uri not in caplog.text and 'private-canary-password' not in caplog.text


@pytest.mark.parametrize('error_type',[ValueError, __import__('pymongo').errors.ConnectionFailure])
def test_actual_storage_connection_errors_never_log_uri(monkeypatch, caplog, error_type):
    from tradingagents.config import mongodb_storage as module
    uri='mongodb://fixture:private-canary-password@example.test/'
    monkeypatch.setattr(module,'MongoClient',Mock(side_effect=error_type(uri)))
    with caplog.at_level(logging.INFO):
        storage=module.MongoDBStorage(connection_string=uri)
    assert storage.is_connected() is False
    assert uri not in caplog.text and 'private-canary-password' not in caplog.text
