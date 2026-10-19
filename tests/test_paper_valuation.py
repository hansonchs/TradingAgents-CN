"""Real simulator routes with isolated accounts/quotes and no provider or DB calls."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import sys
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import paper
from app.routers.auth_db import get_current_user

REAL_ACCOUNT_HELPER = paper._get_or_create_account


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, _):
        return self.rows


class Collection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.insert_one = AsyncMock()
        self.update_one = AsyncMock()
        self.delete_one = AsyncMock()

    def find(self, query):
        return Cursor([row for row in self.rows if all(row.get(k) == v for k, v in query.items())])

    async def find_one(self, query, *args):
        return self.rows[0] if self.rows else None


@pytest.fixture
def api(monkeypatch):
    positions = [
        dict(user_id='owner', code='AAA', market='US', currency='USD', quantity=3, avg_cost=20),
        dict(user_id='owner', code='BBB', market='US', currency='USD', quantity=2, avg_cost=10),
        dict(user_id='owner', code='00700', market='HK', currency='HKD', quantity=10, avg_cost=100),
        dict(user_id='someone-else', code='PRIVATE', market='US', currency='USD', quantity=100, avg_cost=200),
    ]
    for index, position in enumerate(positions):
        position['_id'] = str(index)
    db = {name: Collection() for name in ('paper_accounts', 'paper_orders', 'paper_trades', 'paper_market_rules')}
    db['paper_positions'] = Collection(positions)
    monkeypatch.setattr(paper, 'get_mongo_db', lambda: db)
    account = AsyncMock(return_value=dict(
        cash=dict(CNY=0.0, HKD=1000.0, USD=100.0),
        realized_pnl=dict(CNY=0.0, HKD=0.0, USD=0.0),
    ))
    monkeypatch.setattr(paper, '_get_or_create_account', account)
    prices = dict(AAA=25.0, BBB=None, **{'00700': 120.0})

    async def quote(code, market):
        return prices[code]

    monkeypatch.setattr(paper, '_get_last_price', quote)
    monkeypatch.setattr(paper, '_get_market_rules', AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(paper.router, prefix='/api')
    app.dependency_overrides[get_current_user] = lambda: {'id': 'owner'}
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, account=account, prices=prices)


def assert_no_writes(db):
    for collection in db.values():
        collection.insert_one.assert_not_awaited()
        collection.update_one.assert_not_awaited()
        collection.delete_one.assert_not_awaited()


@pytest.mark.parametrize('reverse', [False, True])
def test_missing_quote_invalidates_only_affected_currency_without_fake_loss(api, reverse):
    if reverse:
        api.db['paper_positions'].rows.reverse()
    response = api.client.get('/api/paper/account')
    assert response.status_code == 200
    data = response.json()['data']
    summary = data['account']
    assert summary['positions_value'] == dict(CNY=0, HKD=1200, USD=None)
    assert summary['equity'] == dict(CNY=0, HKD=2200, USD=None)
    assert summary['cash']['USD'] == 100
    assert summary['valuation_status']['USD'] == 'unavailable'
    rows = {p['code']: p for p in data['positions']}
    assert 'PRIVATE' not in rows
    assert rows['AAA']['market_value'] == 75
    assert rows['AAA']['unrealized_pnl'] == 15
    assert rows['BBB']['market_value'] is None
    assert rows['BBB']['unrealized_pnl'] is None
    assert rows['BBB']['last_price'] is None
    assert_no_writes(api.db)


def test_positions_route_preserves_unknown_and_recovers_when_price_returns(api):
    rows = api.client.get('/api/paper/positions').json()['data']['items']
    missing = next(p for p in rows if p['code'] == 'BBB')
    assert missing['market_value'] is missing['unrealized_pnl'] is None
    api.prices['BBB'] = 9
    account = api.client.get('/api/paper/account').json()['data']['account']
    assert account['positions_value']['USD'] == 93
    assert account['equity']['USD'] == 193
    assert account['valuation_status']['USD'] == 'available'
    rows = api.client.get('/api/paper/positions').json()['data']['items']
    assert next(p for p in rows if p['code'] == 'BBB')['unrealized_pnl'] == -2


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), -float('inf'), 0, -1, True, 'bad'])
def test_bad_quote_rejects_order_before_account_creation_or_mutation(api, bad):
    api.prices['AAA'] = bad
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side='buy', quantity=1))
    assert response.status_code == 400
    api.account.assert_not_awaited()
    assert_no_writes(api.db)
    # The same invalid provider value must serialize safely in both read routes.
    for route in ('account', 'positions'):
        response = api.client.get('/api/paper/' + route)
        assert response.status_code == 200
        data = response.json()['data']
        row = next(p for p in data.get('items', data.get('positions', [])) if p['code'] == 'AAA')
        assert row['last_price'] is row['market_value'] is row['unrealized_pnl'] is None


def test_notional_overflow_rejects_order_without_writes(api):
    api.prices['AAA'] = 1e308
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side='buy', quantity=2))
    assert response.status_code == 400
    api.account.assert_not_awaited()
    assert_no_writes(api.db)
    assert api.client.get('/api/paper/account').json()['data']['account']['equity']['USD'] is None


@pytest.mark.parametrize('fee', [float('nan'), float('inf'), -1.0])
def test_invalid_fee_cannot_mutate_account(api, monkeypatch, fee):
    monkeypatch.setattr(paper, '_get_market_rules', AsyncMock(return_value={'commission': {}}))
    monkeypatch.setattr(paper, '_calculate_commission', lambda *args: fee)
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side='buy', quantity=1))
    assert response.status_code == 400
    api.account.assert_not_awaited()
    assert_no_writes(api.db)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), 0, -1, True, 'bad'])
def test_cn_provider_rejects_bad_primary_and_uses_valid_fallback(monkeypatch, bad):
    db = {'market_quotes': Collection([{'close': bad}]), 'stock_basic_info': Collection([{'current_price': '10.5'}])}
    monkeypatch.setattr(paper, 'get_mongo_db', lambda: db)
    assert asyncio.run(paper._get_last_price('600000', 'CN')) == 10.5
    db['stock_basic_info'].rows = [{'current_price': bad}]
    assert asyncio.run(paper._get_last_price('600000', 'CN')) is None


@pytest.mark.parametrize('market', ['HK', 'US'])
def test_foreign_provider_uses_finite_fallback_field(monkeypatch, market):
    quote = AsyncMock(return_value={'price': float('inf'), 'current_price': float('nan'), 'close': '12.5'})
    monkeypatch.setitem(sys.modules, 'app.services.foreign_stock_service', SimpleNamespace(
        ForeignStockService=lambda db: SimpleNamespace(get_quote=quote)))
    monkeypatch.setattr(paper, 'get_mongo_db', lambda: {})
    assert asyncio.run(paper._get_last_price('FIXTURE', market)) == 12.5
    quote.return_value = {'price': float('inf')}
    assert asyncio.run(paper._get_last_price('FIXTURE', market)) is None


@pytest.mark.parametrize('side,expected_cash', [('buy', 75), ('sell', 125)])
def test_finite_quote_preserves_successful_simulated_fill(api, side, expected_cash):
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side=side, quantity=1))
    assert response.status_code == 200
    order = response.json()['data']['order']
    assert order['price'] == 25
    assert order['amount'] == 25
    assert order['status'] == 'filled'
    update = api.db['paper_accounts'].update_one.call_args.args[1]
    result_cash = update['$set']['cash.USD'] if side == 'buy' else 100 + update['$inc']['cash.USD']
    assert result_cash == expected_cash
    api.db['paper_orders'].insert_one.assert_awaited_once()
    api.db['paper_trades'].insert_one.assert_awaited_once()


@pytest.mark.parametrize('side', ['buy', 'sell'])
@pytest.mark.parametrize('field', ['cash', 'realized_pnl', 'avg_cost', 'quantity', 'available_qty'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), None, True])
def test_legacy_invalid_state_is_readable_but_cannot_be_traded(api, side, field, bad):
    if field in ('cash', 'realized_pnl'):
        api.account.return_value[field]['USD'] = bad
    else:
        api.db['paper_positions'].rows[0][field] = bad
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side=side, quantity=1))
    assert response.status_code == 409
    assert_no_writes(api.db)
    for route in ('account', 'positions'):
        assert api.client.get('/api/paper/' + route).status_code == 200


@pytest.mark.parametrize('field', ['rate', 'min', 'stamp_duty_rate', 'transaction_levy_rate',
                                   'trading_fee_rate', 'settlement_fee_rate', 'sec_fee_rate'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1.0, True, 'bad'])
def test_real_commission_calculator_rejects_invalid_configuration(api, monkeypatch, field, bad):
    rules = {'commission': {'rate': 0.01, 'min': 1, field: bad}}
    monkeypatch.setattr(paper, '_get_market_rules', AsyncMock(return_value=rules))
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side='buy', quantity=1))
    assert response.status_code == 400
    api.account.assert_not_awaited()
    assert_no_writes(api.db)


def test_valid_commission_is_preserved_in_fill_and_cash(api, monkeypatch):
    monkeypatch.setattr(paper, '_get_market_rules', AsyncMock(return_value={'commission': {'rate': 0.001, 'min': 1}}))
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side='buy', quantity=1))
    assert response.status_code == 200
    assert response.json()['data']['order']['commission'] == 1
    assert api.db['paper_accounts'].update_one.call_args.args[1]['$set']['cash.USD'] == 74


@pytest.mark.parametrize('side,qty', [('buy', 1), ('sell', 2)])
def test_derived_cost_or_pnl_overflow_does_not_debit_cash_first(api, side, qty):
    api.db['paper_positions'].rows[0]['avg_cost'] = 1e308
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side=side, quantity=qty))
    assert response.status_code == 409
    assert_no_writes(api.db)


@pytest.mark.parametrize('field', ['cash', 'realized_pnl'])
def test_derived_balance_overflow_cannot_be_written(api, field):
    api.prices['AAA'] = 1e308
    api.account.return_value[field]['USD'] = 1e308
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side='sell', quantity=1))
    assert response.status_code == 409
    assert_no_writes(api.db)


def test_quantity_exceeding_bson_integer_range_is_rejected(api):
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side='buy', quantity=2**63))
    assert response.status_code == 422
    api.account.assert_not_awaited()
    assert_no_writes(api.db)


@pytest.mark.parametrize('field', ['cash', 'realized_pnl'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), None])
def test_real_legacy_account_helper_never_persists_bad_state_on_get_or_rejection(api, monkeypatch, field, bad):
    row = dict(user_id='owner', cash=1000.0, realized_pnl=0.0)
    row[field] = bad
    api.db['paper_accounts'].rows = [row]
    monkeypatch.setattr(paper, '_get_or_create_account', REAL_ACCOUNT_HELPER)
    api.prices['600000'] = 25
    response = api.client.get('/api/paper/account')
    assert response.status_code == 200
    data = response.json()['data']['account']
    assert data[field]['CNY'] is None
    assert data['cash']['USD'] is data['cash']['HKD'] is None
    response = api.client.post('/api/paper/order', json=dict(code='600000', side='buy', quantity=1))
    assert response.status_code == 409
    assert_no_writes(api.db)
    assert not isinstance(row[field], dict)


def test_real_scalar_cny_balance_cannot_be_reinterpreted_as_known_foreign_cash(api, monkeypatch):
    api.db['paper_accounts'].rows = [dict(user_id='owner', cash=1000.0, realized_pnl=0.0)]
    monkeypatch.setattr(paper, '_get_or_create_account', REAL_ACCOUNT_HELPER)
    response = api.client.post('/api/paper/order', json=dict(code='AAA', side='buy', quantity=1))
    assert response.status_code == 409
    assert_no_writes(api.db)


@pytest.mark.parametrize('side,cash,pnl', [('buy', 975, 0), ('sell', 1025, 5)])
def test_valid_scalar_cny_trade_normalizes_only_in_validated_order_update(api, monkeypatch, side, cash, pnl):
    api.db['paper_accounts'].rows = [dict(user_id='owner', cash=1000.0, realized_pnl=0.0)]
    api.db['paper_positions'].rows[0].update(code='600000', market='CN', currency='CNY')
    api.prices['600000'] = 25
    monkeypatch.setattr(paper, '_get_or_create_account', REAL_ACCOUNT_HELPER)
    response = api.client.post('/api/paper/order', json=dict(code='600000', side=side, quantity=1))
    assert response.status_code == 200
    api.db['paper_accounts'].update_one.assert_awaited_once()
    update = api.db['paper_accounts'].update_one.call_args.args[1]
    assert update['$set']['cash'] == dict(CNY=cash, HKD=None, USD=None)
    assert update['$set']['realized_pnl'] == dict(CNY=pnl, HKD=None, USD=None)
    assert '$inc' not in update
