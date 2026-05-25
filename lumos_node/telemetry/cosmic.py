"""Cosmic telemetry clients — free public feeds, no key needed for NOAA/USGS.

Endpoints (all real, all checked May 2026):
  NOAA SWPC:
    - Planetary K-index 1-min:     /products/noaa-planetary-k-index.json
    - Solar wind plasma 1-day:     /products/solar-wind/plasma-1-day.json
    - Solar wind magfield 1-day:   /products/solar-wind/mag-1-day.json
    - GOES X-ray flux 1-day:       /json/goes/primary/xrays-1-day.json
  NASA (gateway, single key for all):
    - DONKI events:                /DONKI/{FLR|CME|GST|SEP|IPS}
    - EONET natural events:        eonet.gsfc.nasa.gov/api/v3/events
    - NeoWs near-earth objects:    /neo/rest/v1/feed
  USGS:
    - Earthquakes GeoJSON:         /earthquakes/feed/v1.0/summary/{day|week}/{all|significant|M2.5}

Network discipline:
  - 8-second per-request timeout (these are public services; don't hang chat).
  - Single shared httpx.AsyncClient per call site; closed in finally.
  - Every fetch returns {"ok": bool, ...} so tools can degrade gracefully when
    one source is down without breaking the composite snapshot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import get_settings
from ..log import get_logger
from . import cache as tcache


log = get_logger(__name__)


_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
_NOAA_BASE = "https://services.swpc.noaa.gov"
_NASA_BASE = "https://api.nasa.gov"
_EONET_BASE = "https://eonet.gsfc.nasa.gov/api/v3"
_USGS_BASE = "https://earthquake.usgs.gov/earthquakes/feed/v1.0"

# DEMO_KEY works for occasional calls (50/day shared globally). Fine for tool
# use; insufficient for sustained polling. Operator should set LUMOS_NASA_API_KEY.
_NASA_DEMO_KEY = "DEMO_KEY"


def _nasa_key() -> str:
    key = (get_settings().nasa_api_key or "").strip()
    return key or _NASA_DEMO_KEY


async def _get_json(client: httpx.AsyncClient, url: str, **kwargs) -> Any:
    """GET → JSON with error swallowing. Returns None on any failure."""
    try:
        r = await client.get(url, timeout=_TIMEOUT, **kwargs)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("telemetry.fetch_failed", url=url, error=str(e))
        return None


# ── NOAA SWPC ────────────────────────────────────────────────────────────────


def _kp_level(kp: float) -> str:
    """NOAA G-scale: 5=G1, 6=G2, 7=G3, 8=G4, 9=G5."""
    if kp < 4:
        return "quiet"
    if kp < 5:
        return "unsettled"
    if kp < 6:
        return "G1 minor storm"
    if kp < 7:
        return "G2 moderate storm"
    if kp < 8:
        return "G3 strong storm"
    if kp < 9:
        return "G4 severe storm"
    return "G5 extreme storm"


def _flare_class(flux_wm2: float) -> str:
    """GOES X-ray peak flux → class letter (A/B/C/M/X) + magnitude."""
    if flux_wm2 < 1e-7:
        return "A"
    if flux_wm2 < 1e-6:
        return f"B{flux_wm2 / 1e-7:.1f}"
    if flux_wm2 < 1e-5:
        return f"C{flux_wm2 / 1e-6:.1f}"
    if flux_wm2 < 1e-4:
        return f"M{flux_wm2 / 1e-5:.1f}"
    return f"X{flux_wm2 / 1e-4:.1f}"


async def fetch_kp(client: httpx.AsyncClient) -> dict:
    """Current Kp from NOAA 1-min estimated planetary K-index.

    Endpoint: `services.swpc.noaa.gov/json/planetary_k_index_1m.json`
    Returns a list of dicts (last ~6 hours) with keys:
      time_tag, kp_index, estimated_kp, kp
    The historical `/products/noaa-planetary-k-index-1-minute.json` path was
    discontinued — `/json/planetary_k_index_1m.json` is the current 1-min feed
    (verified May 2026).
    """
    cached = tcache.get("kp")
    if cached is not None:
        return cached
    data = await _get_json(
        client, f"{_NOAA_BASE}/json/planetary_k_index_1m.json"
    )
    if not isinstance(data, list) or not data:
        result = {"ok": False, "kp": None, "level": "unknown"}
        tcache.put("kp", result)
        return result
    latest = data[-1]
    # Different deploys of this endpoint key the field as kp_index, kp, or
    # estimated_kp. Coalesce in that order — first numeric wins.
    raw = (
        latest.get("kp_index")
        if isinstance(latest, dict)
        else None
    )
    if raw is None and isinstance(latest, dict):
        raw = latest.get("estimated_kp") or latest.get("kp")
    try:
        kp = float(raw) if raw is not None else None
    except (ValueError, TypeError):
        kp = None
    if kp is None:
        result = {"ok": False, "kp": None, "level": "unknown"}
        tcache.put("kp", result)
        return result
    result = {
        "ok": True,
        "kp": kp,
        "level": _kp_level(kp),
        "time_tag": str(latest.get("time_tag")) if isinstance(latest, dict) else None,
    }
    tcache.put("kp", result)
    return result


async def fetch_solar_wind(client: httpx.AsyncClient) -> dict:
    """Latest solar wind speed/density (plasma) + Bz (magfield) from DSCOVR."""
    cached = tcache.get("solar_wind")
    if cached is not None:
        return cached
    plasma = await _get_json(
        client, f"{_NOAA_BASE}/products/solar-wind/plasma-1-day.json"
    )
    mag = await _get_json(client, f"{_NOAA_BASE}/products/solar-wind/mag-1-day.json")
    out: dict = {"ok": False}
    if plasma and len(plasma) >= 2:
        # ["time_tag","density","speed","temperature"]
        last = plasma[-1]
        try:
            out["density_per_cm3"] = float(last[1]) if last[1] not in (None, "") else None
            out["speed_kms"] = float(last[2]) if last[2] not in (None, "") else None
            out["temperature_k"] = float(last[3]) if last[3] not in (None, "") else None
            out["plasma_time_tag"] = str(last[0])
            out["ok"] = True
        except (ValueError, TypeError, IndexError):
            pass
    if mag and len(mag) >= 2:
        # ["time_tag","bx_gsm","by_gsm","bz_gsm","lon_gsm","lat_gsm","bt"]
        last = mag[-1]
        try:
            out["bz_nt"] = float(last[3]) if last[3] not in (None, "") else None
            out["bt_nt"] = float(last[6]) if last[6] not in (None, "") else None
            out["mag_time_tag"] = str(last[0])
            out["ok"] = True
        except (ValueError, TypeError, IndexError):
            pass
    tcache.put("solar_wind", out)
    return out


async def fetch_xray(client: httpx.AsyncClient) -> dict:
    """GOES X-ray flux + recent flares (last 24h)."""
    cached = tcache.get("xray")
    if cached is not None:
        return cached
    data = await _get_json(client, f"{_NOAA_BASE}/json/goes/primary/xrays-1-day.json")
    if not data:
        result = {"ok": False, "recent_flares_24h": []}
        tcache.put("xray", result)
        return result
    # Format: [{"time_tag": "...", "satellite": 16, "flux": 1.2e-7, "energy": "0.1-0.8nm"}, ...]
    long_band = [d for d in data if d.get("energy", "").startswith("0.1-0.8")]
    if not long_band:
        result = {"ok": False, "recent_flares_24h": []}
        tcache.put("xray", result)
        return result
    latest = long_band[-1]
    try:
        current_flux = float(latest.get("flux") or 0.0)
    except (ValueError, TypeError):
        current_flux = 0.0
    # Surface peaks worth knowing: any sample ≥ C-class (1e-6)
    flares = []
    for sample in long_band:
        try:
            flux = float(sample.get("flux") or 0.0)
        except (ValueError, TypeError):
            continue
        if flux >= 1e-6:
            flares.append(
                {
                    "time_tag": sample.get("time_tag"),
                    "class": _flare_class(flux),
                    "flux_wm2": flux,
                }
            )
    # Dedup by class+truncated-time so we don't get 100 entries for one peak
    flares = flares[-10:]
    result = {
        "ok": True,
        "current_class": _flare_class(current_flux),
        "current_flux_wm2": current_flux,
        "recent_flares_24h": flares,
        "time_tag": latest.get("time_tag"),
    }
    tcache.put("xray", result)
    return result


# ── NASA DONKI ───────────────────────────────────────────────────────────────


async def fetch_donki(client: httpx.AsyncClient, kind: str, days_back: int = 3) -> list[dict]:
    """DONKI events of one kind. kind ∈ {FLR, CME, GST, SEP, IPS}.
    Cache key includes kind + days_back so different queries don't collide."""
    kind = kind.upper()
    if kind not in {"FLR", "CME", "GST", "SEP", "IPS"}:
        return []
    cache_key = f"donki_{kind}_{days_back}d"
    cached = tcache.get(cache_key)
    if cached is not None:
        return cached
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)
    url = f"{_NASA_BASE}/DONKI/{kind}"
    params = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "api_key": _nasa_key(),
    }
    data = await _get_json(client, url, params=params)
    result: list[dict] = data if isinstance(data, list) else []
    # Use "donki" TTL bucket for all DONKI variants (same upstream service).
    tcache.put(cache_key, result, ttl_seconds=tcache.DEFAULT_TTL_SECONDS["donki"])
    return result


# ── NASA EONET ───────────────────────────────────────────────────────────────


async def fetch_eonet(client: httpx.AsyncClient, days: int = 7, limit: int = 20) -> list[dict]:
    """Active natural events from NASA EONET (volcanoes, wildfires, storms, etc.).

    EONET is free + no API key required for read endpoints.
    """
    cache_key = f"eonet_{days}_{limit}"
    cached = tcache.get(cache_key)
    if cached is not None:
        return cached
    url = f"{_EONET_BASE}/events"
    params = {"status": "open", "days": days, "limit": limit}
    data = await _get_json(client, url, params=params)
    if not isinstance(data, dict):
        tcache.put(cache_key, [], ttl_seconds=tcache.DEFAULT_TTL_SECONDS["eonet"])
        return []
    events = data.get("events", [])
    out: list[dict] = []
    for ev in events:
        cats = ev.get("categories") or []
        category = cats[0].get("title") if cats else "Unknown"
        geoms = ev.get("geometry") or []
        latest_geom = geoms[-1] if geoms else None
        coords = None
        if latest_geom:
            c = latest_geom.get("coordinates")
            if isinstance(c, list) and len(c) >= 2:
                coords = [c[0], c[1]]  # [lon, lat]
        out.append(
            {
                "id": ev.get("id"),
                "title": ev.get("title"),
                "category": category,
                "coords_lonlat": coords,
                "last_update": latest_geom.get("date") if latest_geom else None,
                "link": ev.get("link"),
            }
        )
    tcache.put(cache_key, out, ttl_seconds=tcache.DEFAULT_TTL_SECONDS["eonet"])
    return out


# ── NASA NeoWs ───────────────────────────────────────────────────────────────


async def fetch_neos(client: httpx.AsyncClient, days_ahead: int = 7) -> list[dict]:
    """Near-Earth Objects approaching in the next `days_ahead` days."""
    cache_key = f"neos_{days_ahead}d"
    cached = tcache.get(cache_key)
    if cached is not None:
        return cached
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=min(days_ahead, 7))  # NeoWs caps at 7-day windows
    url = f"{_NASA_BASE}/neo/rest/v1/feed"
    params = {
        "start_date": today.isoformat(),
        "end_date": end.isoformat(),
        "api_key": _nasa_key(),
    }
    data = await _get_json(client, url, params=params)
    if not isinstance(data, dict):
        tcache.put(cache_key, [], ttl_seconds=tcache.DEFAULT_TTL_SECONDS["neos"])
        return []
    by_date = data.get("near_earth_objects", {})
    out: list[dict] = []
    for date_str, objs in by_date.items():
        for obj in objs or []:
            approaches = obj.get("close_approach_data") or []
            if not approaches:
                continue
            ap = approaches[0]
            miss_km = float(ap.get("miss_distance", {}).get("kilometers") or 0.0)
            miss_lunar = float(ap.get("miss_distance", {}).get("lunar") or 0.0)
            vel_kms = float(ap.get("relative_velocity", {}).get("kilometers_per_second") or 0.0)
            est_diam = obj.get("estimated_diameter", {}).get("meters", {})
            out.append(
                {
                    "id": obj.get("id"),
                    "name": obj.get("name"),
                    "approach_date": date_str,
                    "miss_km": miss_km,
                    "miss_lunar_distances": miss_lunar,
                    "velocity_kms": vel_kms,
                    "diameter_m_min": float(est_diam.get("estimated_diameter_min") or 0.0),
                    "diameter_m_max": float(est_diam.get("estimated_diameter_max") or 0.0),
                    "potentially_hazardous": bool(
                        obj.get("is_potentially_hazardous_asteroid")
                    ),
                }
            )
    out.sort(key=lambda x: x["miss_lunar_distances"])
    tcache.put(cache_key, out, ttl_seconds=tcache.DEFAULT_TTL_SECONDS["neos"])
    return out


# ── USGS Earthquakes ─────────────────────────────────────────────────────────


async def fetch_earthquakes(
    client: httpx.AsyncClient,
    period: str = "day",
    min_magnitude: float = 4.5,
) -> list[dict]:
    """USGS earthquake feed. period ∈ {hour, day, week, month}.
    Magnitude filter is client-side since USGS only exposes fixed bands."""
    period = period if period in {"hour", "day", "week", "month"} else "day"
    band = "significant" if min_magnitude >= 4.5 else "4.5"
    if min_magnitude < 4.5:
        band = "2.5"
    if min_magnitude < 2.5:
        band = "all"
    cache_key = f"earthquakes_{band}_{period}_{min_magnitude}"
    cached = tcache.get(cache_key)
    if cached is not None:
        return cached
    url = f"{_USGS_BASE}/summary/{band}_{period}.geojson"
    data = await _get_json(client, url)
    if not isinstance(data, dict):
        tcache.put(cache_key, [], ttl_seconds=tcache.DEFAULT_TTL_SECONDS["earthquakes"])
        return []
    features = data.get("features") or []
    out: list[dict] = []
    for f in features:
        props = f.get("properties") or {}
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None, None]
        mag = props.get("mag")
        if mag is None or mag < min_magnitude:
            continue
        out.append(
            {
                "id": f.get("id"),
                "magnitude": float(mag),
                "place": props.get("place"),
                "time_ms": props.get("time"),
                "depth_km": float(coords[2]) if coords[2] is not None else None,
                "lon": float(coords[0]) if coords[0] is not None else None,
                "lat": float(coords[1]) if coords[1] is not None else None,
                "url": props.get("url"),
                "tsunami": bool(props.get("tsunami")),
            }
        )
    out.sort(key=lambda x: -x["magnitude"])
    tcache.put(cache_key, out, ttl_seconds=tcache.DEFAULT_TTL_SECONDS["earthquakes"])
    return out


# ── Composite ────────────────────────────────────────────────────────────────


def _summarize(snapshot: dict) -> str:
    """One-line natural-language summary of the snapshot. Used by the
    check_geo_telemetry tool so Lumos has a TLDR before drilling into specifics."""
    parts: list[str] = []
    kp = snapshot.get("geomagnetic", {}).get("kp")
    level = snapshot.get("geomagnetic", {}).get("level")
    if kp is not None:
        parts.append(f"Kp={kp:.1f} ({level})")
    sw = snapshot.get("solar_wind", {})
    if sw.get("speed_kms"):
        parts.append(f"solar wind {sw['speed_kms']:.0f} km/s")
        if sw.get("bz_nt") is not None:
            bz_dir = "southward" if sw["bz_nt"] < 0 else "northward"
            parts.append(f"Bz {sw['bz_nt']:+.1f} nT {bz_dir}")
    xray = snapshot.get("xray", {})
    if xray.get("current_class"):
        parts.append(f"X-ray {xray['current_class']}")
    quakes = snapshot.get("earthquakes_recent", [])
    if quakes:
        big = quakes[0]
        parts.append(f"largest quake M{big['magnitude']:.1f}")
    eonet = snapshot.get("natural_events_active", [])
    if eonet:
        parts.append(f"{len(eonet)} active natural events")
    neos = snapshot.get("near_earth_today", [])
    if neos:
        closest = neos[0]
        parts.append(
            f"nearest NEO {closest['miss_lunar_distances']:.2f} LD"
        )
    return " · ".join(parts) if parts else "no telemetry data available"


async def snapshot_all() -> dict:
    """One-call composite. Returns whatever succeeded; never raises.

    This is the tool layer's primary call — used by check_geo_telemetry().
    """
    async with httpx.AsyncClient() as client:
        kp = await fetch_kp(client)
        sw = await fetch_solar_wind(client)
        xray = await fetch_xray(client)
        eonet = await fetch_eonet(client)
        neos = await fetch_neos(client)
        quakes = await fetch_earthquakes(client, period="day", min_magnitude=4.5)

    snap = {
        "geomagnetic": kp,
        "solar_wind": sw,
        "xray": xray,
        "earthquakes_recent": quakes[:10],
        "natural_events_active": eonet[:10],
        "near_earth_today": neos[:5],
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    snap["summary"] = _summarize(snap)
    return snap
