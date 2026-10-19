"""Compatibility import for the real shared data consistency checker.

The old local stub always certified data as consistent; keep a single actual
implementation so differing provider values are compared and reported.
"""
from app.services.data_consistency_checker import DataConsistencyChecker, DataConsistencyResult

__all__ = ['DataConsistencyChecker', 'DataConsistencyResult']
