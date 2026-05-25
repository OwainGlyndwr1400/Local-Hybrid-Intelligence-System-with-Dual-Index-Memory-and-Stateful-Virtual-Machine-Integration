"""Cosmic auto-trigger worker (OFF by default; tools are the primary path).

Polls cosmic.snapshot_all() every `cosmic_poll_interval_minutes`. When a
threshold is breached AND we're outside the cooldown AND we haven't hit the
daily cap AND chat isn't active right now, fires run_dream_cycle (only if
there are pending turns worth consolidating).

Memory hygiene:
  • Cosmic events are logged to `data/cosmic_events.jsonl` (small, append-only,
    audit-only). NOT written into identity FAISS — that grows only via the
    standard dream cycle on pre-existing pending turns. Cosmic events don't
    bloat the identity index.
  • The cosmic log is capped (`_LOG_CAP`); oldest entries pruned when exceeded.
  • Worker is OFF by default (LUMOS_COSMIC_TRIGGER_ENABLED defaults False).
    Operator turns it on after they've used tools enough to know the cadence.

The worker writes to telemetry/_state.json so HUD can show "last fire, next
candidate window" without re-running checks itself.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson

from ..config import Settings, get_settings
from ..log import get_logger
from ..persistence import _events_path
from . import cosmic


log = get_logger(__name__)


_LOG_FILE = "cosmic_events.jsonl"
_STATE_FILE = "cosmic_state.json"
_LOG_CAP = 500  # cap audit log; pruned on each write past threshold

# Flare letter ordering for threshold comparison: X > M > C > B > A.
_FLARE_RANK = {"A": 0, "B": 1, "C": 2, "M": 3, "X": 4}


def _data_dir(settings: Settings) -> Path:
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _log_path(settings: Settings) -> Path:
    return _data_dir(settings) / _LOG_FILE


def _state_path(settings: Settings) -> Path:
    return _data_dir(settings) / _STATE_FILE


def _flare_threshold_met(current_class: str | None, threshold: str) -> bool:
    """Compare flare classes like 'M5.2' against threshold like 'X' or 'M5'."""
    if not current_class:
        return False
    cur_letter = current_class[:1].upper()
    thresh_letter = (threshold or "X")[:1].upper()
    cur_rank = _FLARE_RANK.get(cur_letter, -1)
    th_rank = _FLARE_RANK.get(thresh_letter, 4)
    if cur_rank > th_rank:
        return True
    if cur_rank < th_rank:
        return False
    # Same letter — compare magnitude suffix when present.
    try:
        cur_mag = float(current_class[1:])
    except ValueError:
        cur_mag = 1.0
    try:
        th_mag = float(threshold[1:]) if len(threshold) > 1 else 1.0
    except ValueError:
        th_mag = 1.0
    return cur_mag >= th_mag


def _evaluate_thresholds(snap: dict, settings: Settings) -> list[dict]:
    """Return list of triggered events (may be empty) detected in this snapshot.

    Each event is a dict with keys: kind, description, magnitude_text.
    """
    events: list[dict] = []

    geom = snap.get("geomagnetic") or {}
    kp = geom.get("kp")
    if isinstance(kp, (int, float)) and kp >= settings.cosmic_trigger_min_kp:
        events.append(
            {
                "kind": "geomagnetic_storm",
                "description": f"Kp={kp:.1f} ({geom.get('level')})",
                "magnitude_text": f"Kp{kp:.1f}",
            }
        )

    xray = snap.get("xray") or {}
    cls = xray.get("current_class")
    if _flare_threshold_met(cls, settings.cosmic_trigger_min_flare_class):
        events.append(
            {
                "kind": "solar_flare",
                "description": f"Solar flare {cls} in progress",
                "magnitude_text": str(cls),
            }
        )

    quakes = snap.get("earthquakes_recent") or []
    if quakes:
        top = quakes[0]
        mag = top.get("magnitude") or 0.0
        if mag >= settings.cosmic_trigger_min_eq_magnitude:
            events.append(
                {
                    "kind": "major_earthquake",
                    "description": f"M{mag:.1f} earthquake — {top.get('place', 'unknown')}",
                    "magnitude_text": f"M{mag:.1f}",
                }
            )

    neos = snap.get("near_earth_today") or []
    if neos:
        closest = neos[0]
        ld = closest.get("miss_lunar_distances") or 999.0
        if ld <= settings.cosmic_trigger_min_neo_lunar_distances:
            events.append(
                {
                    "kind": "near_earth_pass",
                    "description": (
                        f"NEO {closest.get('name')} approaching at {ld:.2f} LD"
                    ),
                    "magnitude_text": f"{ld:.2f}LD",
                }
            )

    return events


def _read_state(settings: Settings) -> dict[str, Any]:
    p = _state_path(settings)
    if not p.exists():
        return {"last_fire_unix": 0.0, "fires_today": 0, "day_iso": ""}
    try:
        return orjson.loads(p.read_bytes())
    except (orjson.JSONDecodeError, OSError):
        return {"last_fire_unix": 0.0, "fires_today": 0, "day_iso": ""}


def _write_state(settings: Settings, state: dict[str, Any]) -> None:
    p = _state_path(settings)
    try:
        p.write_bytes(orjson.dumps(state, option=orjson.OPT_INDENT_2))
    except OSError as e:
        log.warning("cosmic.state_write_failed", error=str(e))


def _append_log(settings: Settings, entry: dict[str, Any]) -> None:
    """Append to cosmic_events.jsonl, pruning if past the cap."""
    p = _log_path(settings)
    try:
        with p.open("ab") as f:
            f.write(orjson.dumps(entry))
            f.write(b"\n")
    except OSError as e:
        log.warning("cosmic.log_write_failed", error=str(e))
        return
    # Cheap prune: read line count occasionally, rewrite last _LOG_CAP if over.
    try:
        with p.open("rb") as f:
            lines = f.readlines()
        if len(lines) > _LOG_CAP * 2:  # avoid pruning every write
            with p.open("wb") as f:
                f.writelines(lines[-_LOG_CAP:])
    except OSError:
        pass


def _chat_idle_seconds(settings: Settings) -> float:
    """How many seconds since the last chat turn was persisted.
    Returns +inf when there's no events file yet (treat as idle).
    """
    p = _events_path(settings)
    try:
        return time.time() - p.stat().st_mtime
    except OSError:
        return float("inf")


def _pending_turn_count(settings: Settings) -> int:
    """Best-effort count of pending turns (unconsolidated). Cheap proxy: line
    count in identity_events.jsonl. Real watermark check happens inside
    run_dream_cycle when actually called."""
    p = _events_path(settings)
    try:
        with p.open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _eligible(settings: Settings, state: dict[str, Any]) -> tuple[bool, str]:
    """Composite eligibility check. Returns (eligible, reason_if_not)."""
    now = time.time()
    cooldown = settings.cosmic_trigger_cooldown_hours * 3600
    if now - float(state.get("last_fire_unix") or 0.0) < cooldown:
        return False, "cooldown"
    today = _today_iso()
    if state.get("day_iso") == today and int(state.get("fires_today") or 0) >= settings.cosmic_trigger_daily_cap:
        return False, "daily_cap"
    skip_window = settings.cosmic_trigger_skip_if_chat_active_minutes * 60
    if skip_window > 0 and _chat_idle_seconds(settings) < skip_window:
        return False, "chat_active"
    return True, ""


async def _maybe_fire(settings: Settings, snap: dict) -> dict | None:
    """Run threshold check + eligibility gates. If eligible AND triggered AND
    there are pending turns to consolidate, fire run_dream_cycle. Always log
    the audit event regardless of whether dream actually ran."""
    events = _evaluate_thresholds(snap, settings)
    if not events:
        return None

    state = _read_state(settings)
    eligible, reason = _eligible(settings, state)
    pending = _pending_turn_count(settings)

    audit = {
        "ts_unix": time.time(),
        "ts_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events": events,
        "summary": snap.get("summary", ""),
        "eligible": eligible,
        "skipped_reason": "" if eligible else reason,
        "pending_turns": pending,
        "dream_fired": False,
    }

    if not eligible:
        _append_log(settings, audit)
        log.info("cosmic.skipped", reason=reason, events=[e["kind"] for e in events])
        return audit

    # Fire condition: also need enough pending turns to be worth consolidating.
    # Avoids dream firing on top of zero pending turns (no actual work to do).
    if pending < settings.auto_dream_min_pending:
        audit["skipped_reason"] = "no_pending_turns"
        _append_log(settings, audit)
        log.info(
            "cosmic.skipped",
            reason="no_pending_turns",
            pending=pending,
            events=[e["kind"] for e in events],
        )
        return audit

    # All gates passed: fire dream_cycle. Late-import to avoid circular import.
    from ..dream import run_dream_cycle
    try:
        result = await run_dream_cycle(settings=settings)
        audit["dream_fired"] = True
        audit["dream_consolidated"] = int(result.get("consolidated") or 0)
    except Exception as e:  # noqa: BLE001
        audit["dream_error"] = str(e)
        log.warning("cosmic.dream_failed", error=str(e))

    today = _today_iso()
    new_state = {
        "last_fire_unix": time.time(),
        "fires_today": (state.get("fires_today", 0) + 1) if state.get("day_iso") == today else 1,
        "day_iso": today,
        "last_events": [e["kind"] for e in events],
    }
    _write_state(settings, new_state)
    _append_log(settings, audit)
    log.info(
        "cosmic.fired",
        events=[e["kind"] for e in events],
        consolidated=audit.get("dream_consolidated"),
    )
    return audit


async def cosmic_worker_loop() -> None:
    """Background coroutine. Polls snapshot_all every interval, evaluates
    thresholds, fires dream when conditions align. Self-cancellable.

    Disabled when settings.cosmic_trigger_enabled is False — returns immediately.
    """
    settings = get_settings()
    if not settings.cosmic_trigger_enabled:
        log.info("cosmic.worker.disabled")
        return

    interval = max(5, settings.cosmic_poll_interval_minutes) * 60
    log.info("cosmic.worker.started", interval_minutes=interval // 60)

    while True:
        try:
            await asyncio.sleep(interval)
            snap = await cosmic.snapshot_all()
            await _maybe_fire(settings, snap)
        except asyncio.CancelledError:
            log.info("cosmic.worker.cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("cosmic.worker.iter_failed", error=str(e))
