"""OpenSky Network airspace client (post-March-2026 OAuth2 migration).

Auth flow (client credentials):
  POST https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token
    grant_type=client_credentials
    client_id=<from operator's OpenSky account>
    client_secret=<from operator's OpenSky account>
  → returns {"access_token": ..., "expires_in": 1800}

Token cached in-process for ≤25 min (TTL is 30 min; we refresh 5 min early).
Anonymous mode still supported when creds are blank — uses public /states/all
at the lower 400-credit/day rate.

Bounding-box credit cost (relevant — we usually want small boxes):
  ≤25 sq°  → 1 credit
  25-100   → 2 credits
  100-400  → 3 credits
  >400     → 4 credits

For a 50 km radius around the operator's location, the box is well under 1 sq° → 1 credit.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import httpx

from ..config import get_settings
from ..log import get_logger
from . import cache as tcache


log = get_logger(__name__)


_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/"
    "opensky-network/protocol/openid-connect/token"
)
_API_BASE = "https://opensky-network.org/api"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_TOKEN_REFRESH_MARGIN_SECONDS = 300  # refresh 5 min before expiry


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # unix ts


_token_cache: _CachedToken | None = None


async def _get_access_token(client: httpx.AsyncClient) -> str | None:
    """Fetch or reuse a cached access token. Returns None if creds unset
    or token endpoint fails — caller should fall back to anonymous mode."""
    global _token_cache
    settings = get_settings()
    cid = (settings.opensky_client_id or "").strip()
    csec = (settings.opensky_client_secret or "").strip()
    if not cid or not csec:
        return None

    if _token_cache and _token_cache.expires_at - time.time() > _TOKEN_REFRESH_MARGIN_SECONDS:
        return _token_cache.access_token

    try:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": cid,
                "client_secret": csec,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("opensky.auth_failed", error=str(e))
        return None

    access = payload.get("access_token")
    expires_in = int(payload.get("expires_in") or 1800)
    if not access:
        log.warning("opensky.auth_no_token", payload=payload)
        return None
    _token_cache = _CachedToken(
        access_token=access, expires_at=time.time() + expires_in
    )
    log.info("opensky.token_refreshed", expires_in=expires_in)
    return access


def _bbox_from_radius(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Build a (lamin, lomin, lamax, lomax) bounding box around (lat, lon).

    Latitude: 1° ≈ 111 km, constant.
    Longitude: 1° ≈ 111 km × cos(lat), so wider near equator, narrower near poles.
    Clamp longitude scale to avoid divide-by-zero at the poles.
    """
    dlat = radius_km / 111.0
    cos_lat = max(0.05, math.cos(math.radians(lat)))
    dlon = radius_km / (111.0 * cos_lat)
    return (
        max(-90.0, lat - dlat),
        max(-180.0, lon - dlon),
        min(90.0, lat + dlat),
        min(180.0, lon + dlon),
    )


def _decode_state_vector(s: list) -> dict:
    """OpenSky /states/all returns positional arrays — decode into a dict.

    Field reference (OpenSky API 1.4):
      0  icao24            (str)
      1  callsign          (str, may be padded with spaces)
      2  origin_country    (str)
      3  time_position     (unix ts, seconds; null if no position)
      4  last_contact      (unix ts, seconds)
      5  longitude         (deg)
      6  latitude          (deg)
      7  baro_altitude     (m)
      8  on_ground         (bool)
      9  velocity          (m/s)
      10 true_track        (deg, 0 = north, clockwise)
      11 vertical_rate     (m/s)
      12 sensors           (list[int] or null)
      13 geo_altitude      (m)
      14 squawk            (str)
      15 spi               (bool)
      16 position_source   (int enum)
    """
    g = lambda i: s[i] if i < len(s) else None  # noqa: E731

    callsign = g(1)
    if isinstance(callsign, str):
        callsign = callsign.strip() or None
    altitude_m = g(7) if g(7) is not None else g(13)
    velocity_ms = g(9)
    return {
        "icao24": g(0),
        "callsign": callsign,
        "origin_country": g(2),
        "lon": g(5),
        "lat": g(6),
        "altitude_m": altitude_m,
        "altitude_ft": round(altitude_m * 3.28084) if altitude_m is not None else None,
        "velocity_kts": round(velocity_ms * 1.94384) if velocity_ms is not None else None,
        "heading_deg": g(10),
        "vertical_rate_ms": g(11),
        "on_ground": bool(g(8)) if g(8) is not None else None,
        "squawk": g(14),
    }


async def fetch_states_bbox(
    lat: float,
    lon: float,
    radius_km: float = 50.0,
) -> dict:
    """Fetch live state vectors for aircraft within `radius_km` of (lat, lon).

    Falls back to anonymous mode (no Authorization header) when client creds
    are not configured. Anonymous quota is 400/day shared, which is plenty for
    on-demand tool calls but would burn out under continuous polling.

    Cached for `DEFAULT_TTL_SECONDS["opensky"]` (default 30s) per unique
    (lat, lon, radius) triple. Position data goes stale fast — short TTL is
    the right tradeoff. If operator wants longer cache for quota savings,
    they can raise LUMOS_TELEMETRY_TTL_OPENSKY in settings (future tunable).
    """
    cache_key = f"opensky_{lat:.4f}_{lon:.4f}_{radius_km:.0f}"
    cached = tcache.get(cache_key)
    if cached is not None:
        return cached
    lamin, lomin, lamax, lomax = _bbox_from_radius(lat, lon, radius_km)
    async with httpx.AsyncClient() as client:
        token = await _get_access_token(client)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            r = await client.get(
                f"{_API_BASE}/states/all",
                params={"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax},
                headers=headers,
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPStatusError as e:
            log.info("opensky.fetch_failed", status=e.response.status_code)
            err_result = {
                "ok": False,
                "error": f"OpenSky returned {e.response.status_code}",
                "authenticated": bool(token),
                "aircraft": [],
            }
            # Cache failures briefly so quota-exhausted (429) doesn't retry-spam.
            tcache.put(cache_key, err_result, ttl_seconds=tcache.DEFAULT_TTL_SECONDS["opensky"])
            return err_result
        except (httpx.HTTPError, ValueError) as e:
            log.info("opensky.fetch_error", error=str(e))
            return {"ok": False, "error": str(e), "authenticated": bool(token), "aircraft": []}

    states = payload.get("states") or []
    aircraft = [_decode_state_vector(s) for s in states]
    # Drop entries without position fix (can't render or describe usefully).
    aircraft = [a for a in aircraft if a["lat"] is not None and a["lon"] is not None]
    result = {
        "ok": True,
        "authenticated": bool(token),
        "bbox": {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax},
        "center": {"lat": lat, "lon": lon, "radius_km": radius_km},
        "count": len(aircraft),
        "aircraft": aircraft,
        "fetched_at_unix": int(time.time()),
    }
    tcache.put(cache_key, result, ttl_seconds=tcache.DEFAULT_TTL_SECONDS["opensky"])
    return result
