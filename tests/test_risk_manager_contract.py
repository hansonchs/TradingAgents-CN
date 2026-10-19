"""Offline regression tests; no provider or broker calls."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def manager(monkeypatch):
    # Load the node without importing optional analyst/provider integrations.
    spec = spec_from_file_location("risk_manager_contract", Path(__file__).resolve().parents[1] / "tradingagents/agents/managers/risk_manager.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return module


def state():
    return dict(company_of_interest="AAA", market_report="MARKET", news_report="NEWS",
                fundamentals_report="FUNDAMENTALS", sentiment_report="SENTIMENT", investment_plan="PLAN",
                risk_debate_state=dict(history="DEBATE", risky_history="", safe_history="", neutral_history="",
                                       current_risky_response="", current_safe_response="", current_neutral_response="", count=0))


def test_memory_uses_distinct_fundamentals(manager):
    memory = Mock()
    memory.get_memories.return_value = []
    llm = Mock()
    llm.invoke.return_value = SimpleNamespace(content="Completed research recommendation with supporting evidence.")
    result = manager.create_risk_manager(llm, memory)(state())
    context = memory.get_memories.call_args.args[0]
    assert context.count("NEWS") == 1
    assert context.count("FUNDAMENTALS") == 1
    assert result["risk_debate_state"]["analysis_status"] == "completed"


@pytest.mark.parametrize("failure", ["empty", "short", "exception"])
def test_failed_provider_is_marked_unavailable(manager, failure):
    llm = Mock()
    if failure == "exception":
        llm.invoke.side_effect = RuntimeError("offline provider failure")
    else:
        llm.invoke.return_value = SimpleNamespace(content="" if failure == "empty" else "hold")
    result = manager.create_risk_manager(llm, None)(state())
    assert llm.invoke.call_count == 3
    assert result["risk_debate_state"]["analysis_status"] == "unavailable"
    assert "技术原因" in result["final_trade_decision"]


def test_persisted_decision_carries_failure_without_hold_or_price():
    from tradingagents.utils.research_decision import with_research_status
    old = dict(action='持有', confidence=0.5, risk_score=0.3, target_price=100)
    result = with_research_status(old, {'risk_debate_state': {'analysis_status': 'unavailable'}})
    assert result['analysis_status'] == 'unavailable'
    assert result['action'] == '分析不可用' and result['confidence'] == 0
    assert result['target_price'] is None and result['risk_score'] is None
    assert old['action'] == '持有'
    assert with_research_status(old, {})['analysis_status'] == 'unknown'
    assert with_research_status(old, {'risk_debate_state': {'analysis_status': 'completed'}})['action'] == '持有'


def test_graph_log_keeps_availability(tmp_path, monkeypatch):
    import ast
    import json
    source = Path(__file__).resolve().parents[1] / 'tradingagents/graph/trading_graph.py'
    tree = ast.parse(source.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_log_state')
    namespace = {'Path': Path, 'json': json}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    value = state()
    value.update(trade_date='2026-10-19', trader_investment_plan='PLAN', final_trade_decision='unavailable',
                 investment_debate_state=dict(bull_history='', bear_history='', history='', current_response='', judge_decision=''))
    value['risk_debate_state'].update(analysis_status='unavailable', judge_decision='unavailable')
    monkeypatch.chdir(tmp_path)
    obj = SimpleNamespace(ticker='AAA', log_states_dict={})
    namespace['_log_state'](obj, '2026-10-19', value)
    saved = json.loads(next(tmp_path.rglob('full_states_log.json')).read_text())
    assert saved['2026-10-19']['risk_debate_state']['analysis_status'] == 'unavailable'


def test_disabled_startup_sync_does_not_schedule_provider_work():
    import ast
    source = Path(__file__).resolve().parents[1] / 'app/main.py'
    tree = ast.parse(source.read_text())
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == 'settings.SYNC_STOCK_BASICS_ENABLED'
                  and any(isinstance(call, ast.Call) and ast.unparse(call.func) == 'asyncio.create_task'
                          for call in ast.walk(n)))
    async_client = Mock()
    namespace = dict(settings=SimpleNamespace(SYNC_STOCK_BASICS_ENABLED=False),
                     asyncio=async_client, run_sync_with_sources=Mock(return_value=object()))
    compiled = compile(ast.Module(body=[branch], type_ignores=[]), str(source), 'exec')
    exec(compiled, namespace)
    async_client.create_task.assert_not_called()
    namespace['settings'].SYNC_STOCK_BASICS_ENABLED = True
    exec(compiled, namespace)
    async_client.create_task.assert_called_once()


def test_unavailable_report_export_preserves_unknown_risk(tmp_path, monkeypatch):
    import ast
    import asyncio
    import typing
    source = Path(__file__).resolve().parents[1] / 'app/services/simple_analysis_service.py'
    tree = ast.parse(source.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == '_save_modular_reports_to_data_dir')
    namespace = {'Dict': typing.Dict, 'Any': typing.Any, '__file__': str(source), 'logger': Mock()}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    monkeypatch.setenv('TRADINGAGENTS_RESULTS_DIR', str(tmp_path))
    from tradingagents.utils.research_decision import with_research_status
    decision = with_research_status({}, {'risk_debate_state': {'analysis_status': 'unavailable'}})
    result = dict(analysis_date='2026-10-19', state={}, decision=decision)
    saved = asyncio.run(namespace['_save_modular_reports_to_data_dir'](None, result, 'AAA'))
    text = Path(saved['final_trade_decision']).read_text()
    assert '**风险评分**: 未知' in text and '**行动**: 分析不可用' in text
    import json
    metadata = json.loads(next(tmp_path.rglob('analysis_metadata.json')).read_text())
    assert metadata['risk_analysis_status'] == 'unavailable'
