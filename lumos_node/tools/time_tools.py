"""Time/date utility tools."""

from __future__ import annotations

from datetime import datetime, timezone

from . import register


@register(
    name="current_time",
    description=(
        "Return the current UTC date and time as an ISO 8601 string. Use when "
        "you need to anchor a response to 'now' (e.g. computing time deltas, "
        "knowing what day it is)."
    ),
    parameters={"type": "object", "properties": {}},
)
def current_time() -> dict:
    now = datetime.now(tz=timezone.utc)
    return {
        "utc_iso": now.isoformat(timespec="seconds"),
        "weekday": now.strftime("%A"),
    }
