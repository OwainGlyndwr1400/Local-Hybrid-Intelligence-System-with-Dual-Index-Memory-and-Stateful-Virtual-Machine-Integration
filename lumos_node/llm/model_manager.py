"""LM Studio model load/unload manager (Phase 36).

Operator's hardware (20 GB total VRAM) can't fit `model_light`
(gpt-oss-20b ~20 GB Q4) AND `model_heavy` (huihui-gemma-4-26b-a4b-it-abliterated
~14-16 GB Q4) simultaneously. LM Studio's built-in JIT loading + Auto-Evict
handles the unload-before-load swap automatically when we request a different
model in `chat/completions`. This module adds two pieces of polish around that:

  1. **State polling** — query LM Studio's REST API to know which model is
     currently loaded BEFORE we send a chat request, so we can predict whether
     a swap is needed and emit an SSE `model_swap` event to the HUD before
     the ~15 second JIT load stalls the stream.

  2. **Eager pre-warm** — after a heavy-model turn finishes streaming, fire a
     tiny `model=light, max_tokens=1` ping completion in the background. LM
     Studio sees the request, JIT-loads the light model (auto-evicting heavy),
     so the operator's NEXT casual chat starts on an already-warm light model
     with zero load wait.

Endpoints used (LM Studio REST API, port 1234 by default):
  GET  /api/v0/models            list all available models with `state` field
                                  ("loaded" | "not-loaded")
  POST /api/v0/models/load       trigger explicit load (blocks until complete)

LM Studio version compatibility: v0.4.0+ exposes /api/v0/* and v0.5.0+ adds
/api/v1/*. We use /api/v0/* for broader compatibility — fallback gracefully
if the endpoint is missing (older LM Studio installs just skip the swap polish
and rely on JIT alone).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings
from ..log import get_logger


log = get_logger(__name__)


def _management_base_url() -> str:
    """Derive `/api/v0` base from the OpenAI-compatible base URL.

    Settings stores e.g. `http://localhost:1234/v1`. The management API lives
    at `http://localhost:1234/api/v0`. Strip the `/v1` suffix and append the
    management path.
    """
    base = get_settings().lm_studio_base_url.rstrip("/")
    # Strip trailing `/v1` (or `/v0`) if present, then append `/api/v0`.
    for v_suffix in ("/v1", "/v0"):
        if base.endswith(v_suffix):
            base = base[: -len(v_suffix)]
            break
    return f"{base}/api/v0"


async def list_models(timeout: float = 3.0) -> list[dict[str, Any]]:
    """GET /api/v0/models. Returns list of {id, state, ...} dicts.

    `state` is "loaded" for currently-resident models, "not-loaded" otherwise.
    Returns empty list on any error (caller treats unknown == "can't poll, fall
    back to JIT-only behavior").
    """
    url = f"{_management_base_url()}/models"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            r.raise_for_status()
            payload = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("model_manager.list_failed", error=str(e))
        return []
    # LM Studio returns either {"data": [...]} (OpenAI-shaped) or just a list.
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


async def currently_loaded(timeout: float = 3.0) -> set[str]:
    """Set of model identifiers currently in `state: loaded`.

    Note: LM Studio reports embedding models AND chat models with `state`.
    Callers can filter further if they only care about one category.
    """
    models = await list_models(timeout=timeout)
    return {
        m.get("id") or m.get("identifier")
        for m in models
        if m.get("state") == "loaded" and (m.get("id") or m.get("identifier"))
    }


async def is_loaded(model_id: str, timeout: float = 3.0) -> bool:
    """True iff `model_id` is currently `state: loaded` in LM Studio."""
    return model_id in await currently_loaded(timeout=timeout)


async def explicit_load(model_id: str, timeout: float = 120.0) -> bool:
    """POST /api/v0/models/load — block until model is loaded.

    Returns True on success, False on any failure. Operator-explicit alternative
    to relying on chat/completion JIT (which also works but has no progress
    signal). We use this for the eager pre-warm path after a heavy turn ends.

    Timeout default is 120s — large models on consumer hardware can take that
    long to load from disk + GPU upload. Callers that want faster failure can
    pass a shorter timeout.
    """
    url = f"{_management_base_url()}/models/load"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json={"identifier": model_id})
            r.raise_for_status()
        log.info("model_manager.loaded", model=model_id)
        return True
    except (httpx.HTTPError, ValueError) as e:
        log.warning("model_manager.load_failed", model=model_id, error=str(e))
        return False


async def preload_via_ping(model_id: str, timeout: float = 120.0) -> bool:
    """Alternative pre-warm: send a 1-token chat completion request.

    Works on older LM Studio versions where `/api/v0/models/load` may be
    missing. The downside vs explicit_load is a tiny token cost (~1 token in,
    1 token out). Both reach the same end state: model resident in VRAM.
    """
    from .lm_studio import ChatMessage, LMStudioClient

    client = LMStudioClient(timeout=timeout)
    try:
        await client.chat(
            model=model_id,
            messages=[ChatMessage(role="user", content="ping")],
            max_tokens=1,
            temperature=0.0,
        )
        log.info("model_manager.preloaded_via_ping", model=model_id)
        return True
    except Exception as e:  # noqa: BLE001 — best-effort warmup
        log.warning("model_manager.preload_failed", model=model_id, error=str(e))
        return False
    finally:
        await client.aclose()


async def ensure_loaded(target_id: str, timeout: float = 120.0) -> dict[str, Any]:
    """Make sure `target_id` is loaded; swap-in if not.

    Returns a status dict the caller can include in audit logs / SSE events:
      {
        "target": target_id,
        "was_loaded": bool,        # already loaded before this call
        "swap_performed": bool,    # we triggered a load
        "ok": bool,                # final state has target loaded
        "polled": bool,            # whether we could poll LM Studio at all
      }

    If polling fails (older LM Studio), we still attempt explicit_load — it
    just means we can't report `was_loaded` accurately. Callers should treat
    `polled: False, ok: True` as "swap probably worked, but we can't verify."
    """
    loaded = await currently_loaded()
    polled = bool(loaded)  # empty set might mean unloaded OR poll-failed; conservative
    was_loaded = target_id in loaded
    if was_loaded:
        return {
            "target": target_id,
            "was_loaded": True,
            "swap_performed": False,
            "ok": True,
            "polled": polled,
        }
    # Not loaded — trigger explicit load. Falls back to ping-style if explicit fails.
    ok = await explicit_load(target_id, timeout=timeout)
    if not ok:
        ok = await preload_via_ping(target_id, timeout=timeout)
    return {
        "target": target_id,
        "was_loaded": False,
        "swap_performed": True,
        "ok": ok,
        "polled": polled,
    }
