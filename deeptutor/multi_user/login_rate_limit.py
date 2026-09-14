"""In-process login brute-force protection (S-AUTH-02, RIC-754).

A dependency-free, per-process failure tracker keyed on ``(client_ip, username)``.
After a configurable number of consecutive failures the key enters an
exponentially growing lockout window and further attempts short-circuit with
HTTP 429 before any credential check runs. Successful logins reset the window.

Scope and limits:

- Single-process by design — DeepTutor's default ``backend_workers=1`` keeps
  this correct for the common deployment. With multiple workers each process
  tracks independently, which only makes the limiter *more* permissive, never
  less; a network-level limiter (reverse proxy / WAF) remains the hard ceiling
  for public deployments.
- Keys expire after ``WINDOW`` seconds of inactivity so a transient attacker
  cannot pin a legitimate user out forever.
- Only *failed* attempts count; successful auth clears the slate. This avoids
  locking out users who mistype occasionally while still throttling online
  guessing.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Tunables (module-level so tests can monkeypatch)
# ---------------------------------------------------------------------------

#: Consecutive failures before the lockout engages.
FAIL_THRESHOLD = 5
#: Base lockout seconds; each subsequent failure doubles the window up to MAX.
BASE_LOCKOUT_SECONDS = 15
#: Ceiling on a single lockout window.
MAX_LOCKOUT_SECONDS = 900  # 15 minutes
#: Idle seconds after which a key's record is forgotten.
WINDOW_SECONDS = 600  # 10 minutes


@dataclass
class _Record:
    failures: int = 0
    locked_until: float = 0.0  # monotonic deadline; <=0 means not locked
    last_ts: float = field(default_factory=time.monotonic)


class LoginRateLimiter:
    """Thread-safe in-process login failure tracker."""

    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(ip: str | None, username: str | None) -> str:
        # An absent IP (e.g. unix-socket / proxied without forwarding header)
        # collapses to a single bucket so we still throttle by username.
        return f"{(ip or '-')!s}|{(username or '-').lower()}"

    def _now(self) -> float:
        return time.monotonic()

    def is_locked(self, ip: str | None, username: str | None) -> tuple[bool, int]:
        """Return ``(locked, retry_after_seconds)``. Also prunes stale records."""
        key = self._key(ip, username)
        now = self._now()
        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                return False, 0
            # Prune stale record so a recovered user isn't punished forever.
            if now - rec.last_ts > WINDOW_SECONDS:
                self._records.pop(key, None)
                return False, 0
            if rec.locked_until and now < rec.locked_until:
                return True, int(rec.locked_until - now) + 1
            return False, 0

    def record_failure(self, ip: str | None, username: str | None) -> int:
        """Register a failed attempt; return the resulting lockout seconds (0=none)."""
        key = self._key(ip, username)
        now = self._now()
        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                rec = _Record()
                self._records[key] = rec
            # Reset a record whose prior lockout already expired.
            if rec.locked_until and now >= rec.locked_until:
                rec.failures = 0
                rec.locked_until = 0.0
            rec.failures += 1
            rec.last_ts = now
            if rec.failures >= FAIL_THRESHOLD:
                # Exponential backoff: 15, 30, 60, ... capped at MAX.
                exponent = rec.failures - FAIL_THRESHOLD
                lockout = min(
                    BASE_LOCKOUT_SECONDS * (2 ** max(exponent, 0)),
                    MAX_LOCKOUT_SECONDS,
                )
                rec.locked_until = now + lockout
                return int(lockout)
            return 0

    def record_success(self, ip: str | None, username: str | None) -> None:
        """Clear the slate for a key after a successful authentication."""
        key = self._key(ip, username)
        with self._lock:
            self._records.pop(key, None)


#: Process-wide singleton used by the auth router.
login_limiter = LoginRateLimiter()


def client_ip(request) -> str | None:
    """Best-effort client IP, honoring a single trusted proxy hop.

    We trust ``X-Forwarded-For`` only for its *last* entry (the hop nearest to
    us) because in a reverse-proxy setup that is the address our proxy saw.
    Deeper entries are attacker-controllable and must not be trusted for
    rate-limit keying. When no forwarding header is present we fall back to the
    raw transport peer.
    """
    peer = None
    if request.client is not None:
        peer = request.client.host
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # Rightmost non-empty token — the address our direct peer reported.
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return peer
