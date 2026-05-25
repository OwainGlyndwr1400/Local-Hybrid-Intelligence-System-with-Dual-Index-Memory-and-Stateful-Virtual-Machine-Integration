"""In-process TTL cache + daily-quota counter for telemetry feeds.

Why this exists:
  - HUD widgets refresh on a timer.
  - Tools fire on Lumos's discretion.
  - The auto-trigger worker polls independently.
  All three can hit the same upstream within the same minute. Without a cache,
  that's wasted HTTP traffic and accelerated quota burn (NASA: 1000/hour with
  personal key; OpenSky: 4000/day authenticated). With per-source TTL caching,
  the upstream is hit at most once per (TTL × source) regardless of consumer count.

Design choices:
  - **In-memory only.** A restart clears the cache — acceptable because each
    upstream has its own cache-freshness behavior we can lean on.
  - **TTLs match upstream cadence, not consumer rate.** Kp updates every 1 min
    upstream → 2-min cache is "always fresh." NeoWs updates ~daily → 60-min
    cache is essentially always fresh.
  - **Daily counters separate from cache.** The counter increments on cache
    MISS (i.e., actual upstream hit), so operator-facing quota usage reflects
    real upstream traffic, not cache hits.
  - **UTC day boundary** for counter reset — matches NASA + OpenSky billing
    cycles (both are UTC midnight rollovers).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class _CacheEntry:
    """One cached payload + its absolute expiry timestamp (unix seconds)."""

    value: Any
    expires_at: float


@dataclass
class _CounterState:
    """Per-source per-day call counter. Resets when day_iso ticks over."""

    count: int = 0
    day_iso: str = ""


# Per-source TTL defaults (seconds). Tuned to upstream update cadence.
# Operator can override individual TTLs via env vars — see config.py.
# These defaults aim for "always reasonably fresh" without burning quota.
DEFAULT_TTL_SECONDS: dict[str, int] = {
    "kp": 120,            # NOAA Kp 1-min upstream; sample at 2 min
    "solar_wind": 60,     # 1-min upstream
    "xray": 60,           # 1-min upstream
    "donki": 1800,        # NASA DONKI events update hourly-ish
    "eonet": 1800,        # NASA EONET tracks daily events; 30 min ample
    "neos": 3600,         # NASA NeoWs is daily data; 1 hour cache
    "earthquakes": 60,    # USGS real-time but day-band granularity
    "opensky": 30,        # OpenSky aircraft positions move; 30s reasonable
}


_cache: dict[str, _CacheEntry] = {}
_counters: dict[str, _CounterState] = {}


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _tick_counter(source: str) -> None:
    """Increment the per-source daily call counter; reset on UTC day change."""
    today = _today_iso()
    state = _counters.get(source)
    if state is None or state.day_iso != today:
        _counters[source] = _CounterState(count=1, day_iso=today)
        return
    state.count += 1


def get(source: str) -> Any | None:
    """Return cached value for `source` if not expired, else None."""
    entry = _cache.get(source)
    if entry is None:
        return None
    if time.time() >= entry.expires_at:
        # Lazy purge — don't delete (next set() overwrites).
        return None
    return entry.value


def put(source: str, value: Any, ttl_seconds: int | None = None) -> None:
    """Store `value` under `source` with given TTL (falls back to default).
    Also increments the daily call counter for this source — i.e., we treat
    every `put` as evidence of an upstream call (cache miss path)."""
    if ttl_seconds is None:
        ttl_seconds = DEFAULT_TTL_SECONDS.get(source, 60)
    _cache[source] = _CacheEntry(value=value, expires_at=time.time() + ttl_seconds)
    _tick_counter(source)


def invalidate(source: str | None = None) -> None:
    """Drop one source's cache, or everything when source is None.
    Use sparingly — primarily for tests or operator-forced refresh."""
    if source is None:
        _cache.clear()
        return
    _cache.pop(source, None)


def quota_snapshot() -> dict[str, Any]:
    """Return today's call counts per source + cache state for each.
    Used by /api/telemetry/quota and the `get_telemetry_quota` tool."""
    today = _today_iso()
    out: dict[str, Any] = {"day_iso": today, "sources": {}}
    for source, state in _counters.items():
        if state.day_iso != today:
            continue
        entry = _cache.get(source)
        cached_for_seconds = (
            max(0, int(entry.expires_at - time.time())) if entry else 0
        )
        out["sources"][source] = {
            "calls_today": state.count,
            "ttl_seconds": DEFAULT_TTL_SECONDS.get(source, 60),
            "cache_remaining_seconds": cached_for_seconds,
            "cached": cached_for_seconds > 0,
        }
    return out


# ── Documented daily caps, exposed for the quota report ──────────────────────
# These are the operator-facing limits. NOAA + USGS list no daily cap; we
# expose those as "unlimited" so the quota tool can display all sources
# uniformly rather than partial info.
DAILY_CAPS: dict[str, int | None] = {
    "kp": None,                # NOAA SWPC: no documented cap
    "solar_wind": None,        # NOAA SWPC: no documented cap
    "xray": None,              # NOAA SWPC: no documented cap
    "donki": 24_000,           # NASA personal key: 1000/hour × 24h conservative
    "eonet": None,             # EONET: no documented cap, separate from api.nasa.gov
    "neos": 24_000,            # NASA personal key
    "earthquakes": None,       # USGS: no documented cap
    "opensky": 4000,           # OpenSky authenticated
}
