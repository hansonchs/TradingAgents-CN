"""Exercise the current K-line adapter contract with no DB or provider calls."""
from unittest.mock import Mock

import pandas as pd

from app.services.data_sources.manager import DataSourceManager
from app.services.data_sources.data_consistency_checker import DataConsistencyChecker


def adapter(name, items=None, error=None):
    value = Mock()
    value.name = name
    value.is_available.return_value = True
    if error:
        value.get_kline.side_effect = error
    else:
        value.get_kline.return_value = items
    return value


def manager(*adapters):
    # Constructor loads database settings; this test supplies already ordered adapters.
    value = DataSourceManager.__new__(DataSourceManager)
    value.adapters = list(adapters)
    return value


def test_primary_bars_keep_source_and_do_not_call_fallback():
    bars = [{'time': '2024-01-02', 'open': 1, 'high': 2, 'low': 0.5, 'close': 1.5, 'volume': 100}]
    primary, secondary = adapter('tushare', bars), adapter('akshare', [])
    result, source = manager(primary, secondary).get_kline_with_fallback('000001', limit=2, adj='qfq')
    assert result == bars and source == 'tushare'
    primary.get_kline.assert_called_once_with(code='000001', period='day', limit=2, adj='qfq')
    secondary.get_kline.assert_not_called()


def test_empty_and_failed_sources_fall_back_without_claiming_success():
    bars = [{'time': '2024-01-02', 'close': 1.5}]
    providers = [adapter('tushare', []), adapter('akshare', error=RuntimeError('offline')), adapter('baostock', bars)]
    assert manager(*providers).get_kline_with_fallback('000001') == (bars, 'baostock')
    assert manager(adapter('empty', [])).get_kline_with_fallback('000001') == (None, None)


def test_provider_conflicts_are_compared_instead_of_stubbed_as_certain():
    a = pd.DataFrame({'ts_code': ['000001.SZ'], 'pe': [10.0], 'pb': [1.0], 'total_mv': [100.0],
                      'close': [10.0], 'volume': [100.0], 'turnover_rate': [1.0]})
    b = a.copy()
    for column in ('pe', 'pb', 'total_mv', 'close', 'volume', 'turnover_rate'):
        b[column] *= 10
    result = DataConsistencyChecker().check_daily_basic_consistency(a, b, 'primary', 'secondary')
    assert result.is_consistent is False
    assert result.confidence_score < 1.0
