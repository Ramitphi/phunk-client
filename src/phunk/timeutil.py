"""Farcaster time.

Message timestamps are seconds since the Farcaster epoch (2021-01-01 UTC),
not since 1970. On-chain events (key adds and removes) use plain Unix time.
"""
from __future__ import annotations

import datetime as dt

FARCASTER_EPOCH = 1609459200  # 2021-01-01T00:00:00Z as a Unix timestamp


def to_unix(fc_ts: int) -> int:
    """Farcaster timestamp -> Unix timestamp."""
    return int(fc_ts) + FARCASTER_EPOCH


def from_unix(unix_ts: float) -> int:
    """Unix timestamp -> Farcaster timestamp."""
    return int(unix_ts) - FARCASTER_EPOCH


def to_datetime(fc_ts: int) -> dt.datetime:
    """Farcaster timestamp -> timezone-aware UTC datetime."""
    return dt.datetime.fromtimestamp(to_unix(fc_ts), tz=dt.timezone.utc)


def from_datetime(when: dt.datetime) -> int:
    """Datetime -> Farcaster timestamp. A naive datetime is read as UTC."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return from_unix(when.timestamp())
