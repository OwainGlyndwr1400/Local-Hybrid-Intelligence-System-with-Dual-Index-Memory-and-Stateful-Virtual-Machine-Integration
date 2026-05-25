"""Telemetry tools — Lumos can query cosmic/geomagnetic state + local airspace.

Seven tools registered here (six cosmic, one airspace):
  - check_geo_telemetry      composite snapshot (the one-call answer)
  - get_solar_activity       solar wind + X-ray flares only
  - get_geomagnetic_status   Kp + Bz only
  - get_earthquakes          USGS, filterable by magnitude/period
  - get_natural_events       EONET active volcanoes/wildfires/storms
  - get_near_earth_objects   NeoWs upcoming approaches
  - aircraft_overhead        OpenSky local airspace

All seven are tool-call only (no auto-trigger). The auto-trigger worker
lives in lumos_node/telemetry/worker.py and is off by default per operator
preference — tools are the primary value layer.
"""

from __future__ import annotations

from . import register
from ..config import get_settings
from ..log import get_logger
from ..telemetry import airspace, cache as tcache, cosmic


log = get_logger(__name__)


@register(
    name="check_geo_telemetry",
    description=(
        "Composite snapshot of Earth's geomagnetic + space-weather + seismic state. "
        "Call when operator asks 'what does the wavefield look like' or wants global "
        "context. Returns: geomagnetic (Kp + level), solar_wind (speed/density/Bz), "
        "xray (flux + flares), earthquakes_recent, natural_events_active, "
        "near_earth_today, summary. Sources: NOAA SWPC, NASA DONKI/EONET/NeoWs, USGS."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
async def check_geo_telemetry() -> dict:
    return await cosmic.snapshot_all()


@register(
    name="get_solar_activity",
    description=(
        "Solar wind state (speed, density, Bz southward/northward) + recent solar "
        "flares from GOES X-ray sensor. CALL THIS when the question is specifically "
        "about solar activity, CMEs, flares, or solar wind coupling to Earth's "
        "magnetosphere. Bz < -5 nT southward = strong magnetospheric coupling. "
        "X-class flares are rare and significant. Returns solar_wind + xray fields "
        "only (lighter than full snapshot)."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
async def get_solar_activity() -> dict:
    import httpx

    async with httpx.AsyncClient() as client:
        sw = await cosmic.fetch_solar_wind(client)
        xray = await cosmic.fetch_xray(client)
        donki_flr = await cosmic.fetch_donki(client, "FLR", days_back=3)
        donki_cme = await cosmic.fetch_donki(client, "CME", days_back=3)
    return {
        "solar_wind": sw,
        "xray": xray,
        "donki_flares_3d": [
            {
                "id": e.get("flrID"),
                "class": e.get("classType"),
                "begin": e.get("beginTime"),
                "peak": e.get("peakTime"),
                "source_region": e.get("sourceLocation"),
                "active_region": e.get("activeRegionNum"),
            }
            for e in (donki_flr or [])[:5]
        ],
        "donki_cmes_3d": [
            {
                "id": e.get("activityID"),
                "start": e.get("startTime"),
                "note": e.get("note"),
                "source_location": e.get("sourceLocation"),
            }
            for e in (donki_cme or [])[:5]
        ],
    }


@register(
    name="get_geomagnetic_status",
    description=(
        "Current Kp index + planetary geomagnetic state. CALL THIS for questions "
        "about Earth's magnetic field disturbance, aurora forecasts, or whether "
        "we're in a geomagnetic storm. Kp 0-3 quiet, 4 unsettled, 5+ storm "
        "(G1 minor → G5 extreme). Returns geomagnetic + recent_storms (DONKI GST)."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
async def get_geomagnetic_status() -> dict:
    import httpx

    async with httpx.AsyncClient() as client:
        kp = await cosmic.fetch_kp(client)
        donki_gst = await cosmic.fetch_donki(client, "GST", days_back=7)
    return {
        "geomagnetic": kp,
        "recent_storms_7d": [
            {
                "id": e.get("gstID"),
                "start": e.get("startTime"),
                "kp_index": (
                    e.get("allKpIndex") or [{}]
                )[0].get("kpIndex")
                if e.get("allKpIndex")
                else None,
                "source_cme": (e.get("linkedEvents") or [{}])[0].get("activityID")
                if e.get("linkedEvents")
                else None,
            }
            for e in (donki_gst or [])[:5]
        ],
    }


@register(
    name="get_earthquakes",
    description=(
        "Recent earthquakes from USGS. CALL THIS for seismic activity questions. "
        "Default returns past-24h earthquakes ≥ M4.5 globally, sorted by magnitude "
        "descending. Adjust `min_magnitude` and `period` for different views. "
        "Each entry has magnitude, place (region description), depth_km, lat/lon, "
        "and a USGS detail URL."
    ),
    parameters={
        "type": "object",
        "properties": {
            "min_magnitude": {
                "type": "number",
                "default": 4.5,
                "description": "Filter floor. 2.5 → many small events; 6.0 → only significant; 7.0 → only major.",
            },
            "period": {
                "type": "string",
                "default": "day",
                "description": "Time window: hour, day, week, or month.",
            },
        },
        "required": [],
    },
)
async def get_earthquakes(min_magnitude: float = 4.5, period: str = "day") -> dict:
    import httpx

    async with httpx.AsyncClient() as client:
        quakes = await cosmic.fetch_earthquakes(
            client, period=period, min_magnitude=min_magnitude
        )
    return {
        "period": period,
        "min_magnitude": min_magnitude,
        "count": len(quakes),
        "earthquakes": quakes[:25],
    }


@register(
    name="get_natural_events",
    description=(
        "Active natural events worldwide via NASA EONET (volcanoes, wildfires, "
        "severe storms, dust/haze, sea ice, landslides). Call for terrestrial "
        "natural phenomena, not space weather. Each entry: title, category, "
        "coordinates, last update, source link."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "default": 7,
                "description": "How far back to include 'open' events (NASA EONET windowing). 1-30 typical.",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Cap on returned events.",
            },
        },
        "required": [],
    },
)
async def get_natural_events(days: int = 7, limit: int = 20) -> dict:
    import httpx

    async with httpx.AsyncClient() as client:
        events = await cosmic.fetch_eonet(client, days=days, limit=limit)
    return {"days": days, "count": len(events), "events": events}


@register(
    name="get_near_earth_objects",
    description=(
        "Near-Earth Objects (asteroids, comets) approaching Earth in the next "
        "1-7 days, from NASA NeoWs. CALL THIS for questions about celestial "
        "proximity. 1 lunar distance (LD) ≈ 384,400 km. Anything < 0.5 LD is "
        "rare and worth flagging. Returns sorted by miss distance ascending."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days_ahead": {
                "type": "integer",
                "default": 7,
                "description": "Window forward, max 7 (NASA caps NeoWs feed to 7-day spans).",
            },
        },
        "required": [],
    },
)
async def get_near_earth_objects(days_ahead: int = 7) -> dict:
    import httpx

    async with httpx.AsyncClient() as client:
        neos = await cosmic.fetch_neos(client, days_ahead=days_ahead)
    return {"days_ahead": days_ahead, "count": len(neos), "objects": neos[:15]}


@register(
    name="aircraft_overhead",
    description=(
        "Live aircraft state vectors within a radius around a point, via OpenSky "
        "Network. CALL THIS when the operator asks 'what's flying over me / "
        "over <city> right now?' or wants to see current airspace activity. "
        "Returns position, altitude (m + ft), velocity (knots), heading, callsign, "
        "origin country for each plane currently transponding in the area. "
        "If LUMOS_OPERATOR_LAT and LUMOS_OPERATOR_LON are configured, calling "
        "with no lat/lon defaults to operator's reference location. "
        "Uses OAuth2 client credentials (post-March-2026 OpenSky migration) when "
        "configured; otherwise anonymous mode (lower quota). 1 credit per call "
        "for radius_km ≤ 250."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lat": {
                "type": "number",
                "description": "Latitude in decimal degrees. Omit to use operator's reference location.",
            },
            "lon": {
                "type": "number",
                "description": "Longitude in decimal degrees. Omit to use operator's reference location.",
            },
            "radius_km": {
                "type": "number",
                "default": 50.0,
                "description": "Search radius in km (10-500 reasonable; smaller = cheaper).",
            },
        },
        "required": [],
    },
)
async def aircraft_overhead(
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 50.0,
) -> dict:
    settings = get_settings()
    if lat is None or lon is None:
        if settings.operator_lat == 0.0 and settings.operator_lon == 0.0:
            return {
                "error": (
                    "no location given and operator default (LUMOS_OPERATOR_LAT / "
                    "LUMOS_OPERATOR_LON) is unset — pass lat/lon or configure defaults"
                )
            }
        lat = settings.operator_lat
        lon = settings.operator_lon
    if radius_km <= 0 or radius_km > 500:
        return {"error": f"radius_km must be in (0, 500], got {radius_km}"}
    return await airspace.fetch_states_bbox(float(lat), float(lon), float(radius_km))


@register(
    name="get_telemetry_quota",
    description=(
        "Report today's upstream API call counts for every telemetry source. "
        "CALL THIS when the operator asks 'are we close to the quota', 'how "
        "many API calls did we make today', or you need to decide whether to "
        "call another telemetry tool (e.g. before a chain of NeoWs queries). "
        "Returns calls-today, cache TTL, current cache freshness, and the daily "
        "cap for each source. NOAA/USGS/EONET have no documented daily cap "
        "(reported as 'unlimited'); NASA personal-key is ~1000/hour; OpenSky "
        "authenticated is 4000/day. UTC day rollover resets counters."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
def get_telemetry_quota() -> dict:
    snap = tcache.quota_snapshot()
    out_sources: dict = {}
    for source, stat in snap["sources"].items():
        cap = tcache.DAILY_CAPS.get(source)
        cap_label = "unlimited" if cap is None else cap
        pct = None
        if isinstance(cap, int) and cap > 0:
            pct = round(100.0 * stat["calls_today"] / cap, 1)
        out_sources[source] = {
            **stat,
            "daily_cap": cap_label,
            "percent_used": pct,
        }
    return {
        "day_iso": snap["day_iso"],
        "sources": out_sources,
        "notes": (
            "UTC day rollover resets counters. NOAA/USGS/EONET unlimited; "
            "NASA ~1000/hour with personal key; OpenSky 4000/day authenticated."
        ),
    }
