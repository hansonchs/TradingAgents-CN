"""Preserve research availability without fabricating a trading recommendation."""
import math


def format_research_percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        return '未知'
    return f'{value:.1%}'


def with_research_status(decision, state):
    result = dict(decision)
    risk = state.get('risk_debate_state', {}) if isinstance(state, dict) else {}
    status = risk.get('analysis_status', 'unknown') if isinstance(risk, dict) else 'unknown'
    result['analysis_status'] = status if status in {'completed', 'unavailable'} else 'unknown'
    if result['analysis_status'] == 'unavailable':
        result.update(action='分析不可用', confidence=0.0, risk_score=None,
                      target_price=None, reasoning='风险分析未完成，需要人工覆核。')
    return result
