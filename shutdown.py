"""Graceful shutdown that never interrupts deleting received files.

`docker stop` sends SIGTERM. ``request`` (installed as the SIGTERM handler in
main.py) normally turns it into SystemExit at once, so in-flight processing
stops and its ``finally`` blocks clean up. Inside a ``deferred()`` block (the
signal-cli delete sweeps) the SystemExit is postponed until the block ends, so
a sweep is never cut short.

After a shutdown request there is a fixed time budget (inside compose's
``stop_grace_period``) for the remaining deletes; ``remaining()`` reports it.
Signal handlers run only on the main thread, which is where all of this runs.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager

# Seconds after SIGTERM for the remaining deletes; keep below
# stop_grace_period (30 s in docker-compose.yml).
SHUTDOWN_BUDGET_SECONDS = 20.0

_requested_at: float | None = None
_depth = 0


def request(signum=None, frame=None) -> None:
    """SIGTERM handler: record the request; exit now unless inside deferred()."""
    global _requested_at
    if _requested_at is None:
        _requested_at = time.monotonic()
    if _depth == 0:
        raise SystemExit(0)


def requested() -> bool:
    return _requested_at is not None


def remaining() -> float:
    """Seconds left of the shutdown budget (infinite when not shutting down)."""
    if _requested_at is None:
        return float("inf")
    return SHUTDOWN_BUDGET_SECONDS - (time.monotonic() - _requested_at)


@contextmanager
def deferred() -> Iterator[None]:
    """Postpone a SIGTERM's SystemExit until the outermost block ends."""
    global _depth
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1
        if _depth == 0 and _requested_at is not None:
            raise SystemExit(0)


def reset() -> None:
    """For tests only."""
    global _requested_at, _depth
    _requested_at = None
    _depth = 0
