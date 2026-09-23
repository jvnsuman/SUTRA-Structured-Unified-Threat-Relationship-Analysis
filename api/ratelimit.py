"""
api/ratelimit.py

Brute-force protection for POST /auth/login.

Policy (all configurable in config.py / .env):
  * LOGIN_MAX_FAILURES failed attempts for one badge id within
    LOGIN_WINDOW_SECONDS  -> that badge is locked for LOGIN_LOCKOUT_SECONDS.
  * LOGIN_MAX_FAILURES_PER_IP (default 5x the per-badge limit) across all
    badges from one client address -> that address is locked too, which
    stops one host from spraying passwords across many badge ids.
  * A successful login clears that badge's failure count.
  * The 429 response is the same for a real and a non-existent badge id
    (no account enumeration), and includes Retry-After.

Storage is in-process memory, deliberately: sessions are in-process too
(api/auth.py), and this deployment runs a single uvicorn worker. With
several workers each would keep its own counters (an attacker's budget
multiplies by the worker count); at that point move both this and the
session store to Redis. Documented in SECURITY.md.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status

from config import get_settings


class LoginRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures_by_badge: dict = defaultdict(deque)
        self._failures_by_ip: dict = defaultdict(deque)
        self._locked_until: dict = {}

    # -- configuration is read lazily so tests can change settings --------
    @staticmethod
    def _cfg():
        s = get_settings()
        return (s.login_max_failures, s.login_window_seconds, s.login_lockout_seconds,
                s.login_max_failures * 5)

    @staticmethod
    def _prune(dq: deque, now: float, window: float) -> None:
        while dq and now - dq[0] > window:
            dq.popleft()

    def check(self, badge_id: str, ip: str) -> None:
        """Raise HTTP 429 if this badge or address is currently locked."""
        now = time.monotonic()
        with self._lock:
            for key in (("badge", (badge_id or "").lower()), ("ip", ip or "")):
                until = self._locked_until.get(key)
                if until is not None:
                    if now < until:
                        raise HTTPException(
                            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many failed login attempts. Try again later.",
                            headers={"Retry-After": str(int(until - now) + 1)},
                        )
                    del self._locked_until[key]

    def record_failure(self, badge_id: str, ip: str) -> None:
        max_fail, window, lockout, max_ip = self._cfg()
        now = time.monotonic()
        with self._lock:
            b_key = (badge_id or "").lower()
            b = self._failures_by_badge[b_key]
            self._prune(b, now, window)
            b.append(now)
            if len(b) >= max_fail:
                self._locked_until[("badge", b_key)] = now + lockout
                b.clear()
            i = self._failures_by_ip[ip or ""]
            self._prune(i, now, window)
            i.append(now)
            if len(i) >= max_ip:
                self._locked_until[("ip", ip or "")] = now + lockout
                i.clear()

    def record_success(self, badge_id: str, ip: str) -> None:
        with self._lock:
            self._failures_by_badge.pop((badge_id or "").lower(), None)

    def reset(self) -> None:
        with self._lock:
            self._failures_by_badge.clear()
            self._failures_by_ip.clear()
            self._locked_until.clear()


login_limiter = LoginRateLimiter()
