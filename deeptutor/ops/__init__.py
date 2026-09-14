"""Ops dashboard aggregation layer (RIC-720, D3).

Read-only aggregations over the *existing* DeepTutor data surfaces — order
ledger, session/turn log, and mastery-path progress — to drive the
tech-flow ops dashboard. Pure aggregation; no new engine, no writes.
"""

from .aggregates import OpsDashboard, convert_to_csv
from .ranges import resolve_range
from .source import build_dashboard

__all__ = [
    "build_dashboard",
    "convert_to_csv",
    "resolve_range",
    "OpsDashboard",
]