"""Date-range resolution shared by every ops-dashboard endpoint.

The dashboard accepts a ``range`` label and converts it to a half-open
UTC interval ``[start, end)``. ``end`` defaults to *now* so a report always
has a deterministic upper bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

RangeKey = Literal["7d", "30d", "90d", "all"]

RANGES: tuple[RangeKey, ...] = ("7d", "30d", "90d", "all")

#: Refresh hints for the UI. ``all`` has no explicit refresh cadence.
DEFAULT_RANGE: RangeKey = "30d"

_RANGE_DAYS: dict[RangeKey, int] = {"7d": 7, "30d": 30, "90d": 90}

#: "all" is an unbounded lookback — anchor far enough in the past that every
#: persisted record (order / session / path) falls inside the window.
_ALL_ANCHOR = datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class TimeRange:
    key: RangeKey
    start: datetime
    end: datetime

    @property
    def start_ts(self) -> float:
        """Unix timestamp of the inclusive start."""
        return self.start.timestamp()

    @property
    def end_ts(self) -> float:
        """Unix timestamp of the exclusive end."""
        return self.end.timestamp()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def resolve_range(key: str | None, *, now: datetime | None = None) -> TimeRange:
    """Resolve a human range label into a half-open UTC interval.

    Unknown labels fall back to :data:`DEFAULT_RANGE` so a stale client or a
    typo never hard-fails the report — the UI always sends one of the known
    keys, but a defensive default is cheaper than a 400.
    """
    normalized = str(key or DEFAULT_RANGE)
    if normalized not in _RANGE_DAYS and normalized != "all":
        normalized = DEFAULT_RANGE
    anchor = now or now_utc()
    if normalized == "all":
        return TimeRange(key="all", start=_ALL_ANCHOR, end=anchor)
    days = _RANGE_DAYS[normalized]
    start = (anchor - timedelta(days=days)).replace(tzinfo=timezone.utc)
    end = anchor.replace(tzinfo=timezone.utc)
    return TimeRange(key=normalized, start=start, end=end)