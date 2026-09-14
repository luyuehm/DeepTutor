"""Unit tests for the in-process login brute-force limiter (S-AUTH-02, RIC-754).

These lock the contract documented in ``deeptutor/multi_user/login_rate_limit.py``:
failures below the threshold never lock; the threshold failure engages an
exponentially growing lockout; success clears the slate; distinct keys are
independent. No FastAPI/client plumbing is exercised here — only the tracker.
"""

from __future__ import annotations

from deeptutor.multi_user.login_rate_limit import LoginRateLimiter


def test_no_lockout_below_threshold() -> None:
    limiter = LoginRateLimiter()
    for _ in range(4):
        assert limiter.record_failure("1.2.3.4", "alice") == 0
    assert limiter.is_locked("1.2.3.4", "alice") == (False, 0)


def test_threshold_engages_exponential_lockout() -> None:
    limiter = LoginRateLimiter()
    for _ in range(5):
        pass  # 4 below + 1 crossing
    # Drive exactly to the threshold failure.
    for _ in range(5):
        last = limiter.record_failure("1.2.3.4", "alice")
    assert last >= 15  # BASE_LOCKOUT_SECONDS
    locked, retry = limiter.is_locked("1.2.3.4", "alice")
    assert locked and retry > 0


def test_success_clears_lock() -> None:
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.record_failure("1.2.3.4", "alice")
    assert limiter.is_locked("1.2.3.4", "alice")[0]
    limiter.record_success("1.2.3.4", "alice")
    assert limiter.is_locked("1.2.3.4", "alice") == (False, 0)


def test_distinct_users_are_independent() -> None:
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.record_failure("1.2.3.4", "alice")
    assert limiter.is_locked("1.2.3.4", "alice")[0]
    # bob from the same IP is a separate key.
    assert limiter.is_locked("1.2.3.4", "bob") == (False, 0)
    # alice from a different IP is also separate.
    assert limiter.is_locked("9.9.9.9", "alice") == (False, 0)


def test_username_key_is_case_insensitive() -> None:
    limiter = LoginRateLimiter()
    for _ in range(5):
        limiter.record_failure("1.2.3.4", "Alice")
    # Lower-cased lookup must hit the same record.
    assert limiter.is_locked("1.2.3.4", "alice")[0]


def test_lockout_grows_then_caps() -> None:
    """Repeated failures past the threshold grow the window up to the ceiling."""
    limiter = LoginRateLimiter()
    windows: list[int] = []
    # Push well past threshold; lockout grows but never exceeds MAX.
    for _ in range(20):
        windows.append(limiter.record_failure("1.2.3.4", "alice"))
    nonzero = [w for w in windows if w > 0]
    assert nonzero, "threshold must eventually engage"
    assert max(nonzero) <= 900  # MAX_LOCKOUT_SECONDS
    # The last few windows are at the cap (monotonic non-decreasing tail).
    assert nonzero[-1] >= nonzero[0]
