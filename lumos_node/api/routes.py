"""HTTP routes: SSE chat stream, telemetry, ad-hoc search."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..atlas import get_chunk_to_cluster, get_cluster_members, load_atlas
from ..chat import ChatSession
from ..config import TUNABLE_SETTINGS, apply_overrides, get_settings
from ..dream import dream_status, run_dream_cycle
from ..llm.lm_studio import ChatMessage, LMStudioClient
from ..persistence import load_recent_message_pairs
from ..retrieval import IndexMissingError, retrieve
from ..urevm import get_vm
from ..vectors import Manifest
from ..wardenclyffe import topology_snapshot


router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    restore_history: int | None = Field(default=None, ge=0, le=200)
    # Optional list of data:image/...;base64,... URLs (OpenAI multimodal format).
    images: list[str] = Field(default_factory=list, max_length=8)


_SESSIONS: dict[str, ChatSession] = {}


def _get_or_create_session(
    session_id: str | None, restore_history: int | None
) -> ChatSession:
    if session_id and session_id in _SESSIONS:
        return _SESSIONS[session_id]
    session = ChatSession()
    if restore_history and restore_history > 0:
        pairs = load_recent_message_pairs(restore_history, session.settings)
        for u, a in pairs:
            session.history.append(ChatMessage(role="user", content=u))
            session.history.append(ChatMessage(role="assistant", content=a))
    _SESSIONS[session.session_id] = session
    return session


@router.post("/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    session = _get_or_create_session(req.session_id, req.restore_history)

    async def event_stream():
        yield {
            "event": "session",
            "data": json.dumps({"session_id": session.session_id}),
        }
        # Phase 36 — proactive model swap detection. Predict which model
        # stream_turn will route to, check whether it's currently loaded in
        # LM Studio, and if a swap is needed emit a `model_swap` SSE event
        # NOW so the HUD can render "loading <model>..." while the actual
        # JIT load blocks for ~10-15 seconds inside stream_turn.
        # The prediction here intentionally mirrors stream_turn's routing
        # logic (including deep-think detection on the stripped message).
        if (
            session.settings.model_auto_routing_enabled
            and session.settings.model_swap_orchestration_enabled
        ):
            try:
                from ..chat import _detect_deep_think, select_model
                from ..tool_router import detect_full_override
                from ..llm import model_manager
                stripped, deep_think = _detect_deep_think(req.message, session.settings)
                stripped, _ = detect_full_override(stripped)
                if session.settings.deep_think_default:
                    deep_think = True
                predicted_model, predicted_reason = select_model(
                    session.settings, stripped, images=req.images, deep_think=deep_think,
                )
                if not await model_manager.is_loaded(predicted_model):
                    yield {
                        "event": "model_swap",
                        "data": json.dumps({
                            "target": predicted_model,
                            "reason": predicted_reason,
                            "phase": "loading",
                        }),
                    }
            except Exception as e:  # noqa: BLE001 — swap-prediction is non-critical
                # Never let a swap-prediction failure break chat. The actual
                # swap still happens inside stream_turn; we just lose the early
                # HUD signal. Log and continue.
                from ..log import get_logger
                get_logger(__name__).info("routes.swap_predict_failed", error=str(e))
        try:
            async for delta in session.stream_turn(req.message, images=req.images):
                yield {"event": "delta", "data": json.dumps({"text": delta})}
        except IndexMissingError as e:
            yield {"event": "error", "data": json.dumps({"message": str(e)})}
            return
        except Exception as e:  # noqa: BLE001
            yield {"event": "error", "data": json.dumps({"message": str(e)})}
            return

        r = session.last_retrieval
        u = session.last_usage or {}
        cluster_map = get_chunk_to_cluster()

        def _hit_payload(hit: Any) -> dict[str, Any]:
            cid = cluster_map.get(hit.metadata.get("chunk_id", ""))
            return {"score": hit.score, "metadata": hit.metadata, "cluster_id": cid}

        vm = get_vm()
        recent_ops = [t.to_dict() for t in vm.trace[-24:]]
        tool_calls = [
            {
                "name": t.name,
                "arguments": t.arguments,
                "result_preview": t.result_preview,
            }
            for t in session.last_tool_calls
        ]
        snap = vm.snapshot()
        # Nephilim coherence is computed inline in chat.py at turn-end (so
        # LION_RESET can fire from the trace). We just read it here.
        nephilim = session.last_nephilim or {
            "coherence": 0.0,
            "r23_health": 0.0,
            "retrieval_health": 0.0,
            "witness_health": 0.0,
            "stable": False,
            "lion_reset_fired": False,
        }
        done: dict[str, Any] = {
            "session_id": session.session_id,
            "model": session.last_model,
            "retrieved": {
                "identity": [_hit_payload(h) for h in (r.identity if r else [])],
                "knowledge": [_hit_payload(h) for h in (r.knowledge if r else [])],
            },
            "tokens": {
                "prompt": u.get("prompt_tokens"),
                "completion": u.get("completion_tokens"),
                "total": u.get("total_tokens"),
            },
            "turn_count": len(session.history) // 2,
            "urevm": {
                "tick": vm.tick,
                "cycle_position": vm.cycle_position,
                "impedance_accumulator": vm.impedance_accumulator,
                "forbidden_resets": vm.forbidden_resets,
                "ticks_until_361": snap["ticks_until_361"],
                "near_forbidden": snap["near_forbidden"],
                "r23_norm": snap["r23_norm"],
                "r23_phi_gap": snap["r23_phi_gap"],
                "r23_components": snap["r23_components"],
                "observer_r12": snap["observer_r12"],
                "now_r11": snap["now_r11"],
                "center_anchor": snap["center_anchor"],
                "rotational_residual": snap["rotational_residual"],
                "recent_ops": recent_ops,
            },
            "nephilim": nephilim,
            "triskelion": session.last_triskelion,
            "tool_calls": tool_calls,
            "deep_think": session.last_deep_think,
            "tool_routing": session.last_tool_routing,
            "model_route_reason": session.last_model_route_reason,
            "model_swap": session.last_model_swap,
        }
        yield {"event": "done", "data": json.dumps(done)}

    return EventSourceResponse(event_stream())


def _resolve_cache() -> Path:
    settings = get_settings()
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    return cache


def _index_summary(manifest: Manifest | None) -> dict[str, Any] | None:
    if manifest is None:
        return None
    return {"chunks": manifest.chunk_count, "built_at": manifest.built_at}


def _telemetry_payload() -> dict[str, Any]:
    settings = get_settings()
    cache = _resolve_cache()
    return {
        "node": {
            "name": settings.node_name,
            "role": settings.node_role,
            "operator": settings.operator_name,
        },
        "models": {
            "light": settings.model_light,
            "heavy": settings.model_heavy,
            "embedding": settings.lm_studio_embedding_model,
        },
        "indexes": {
            "identity": _index_summary(
                Manifest.from_path(cache / "identity.manifest.json")
            ),
            "knowledge": _index_summary(
                Manifest.from_path(cache / "knowledge.manifest.json")
            ),
        },
        "retrieval": {
            "top_k_identity": settings.retrieval_top_k_identity,
            "top_k_knowledge": settings.retrieval_top_k_knowledge,
            "max_chunk_chars": settings.max_chunk_chars,
            "min_score": settings.min_retrieval_score,
            "dedup_memory": settings.dedup_memory_by_conversation,
            "restore_history_turns": settings.restore_history_turns,
        },
        "tunable": sorted(TUNABLE_SETTINGS),
    }


@router.get("/telemetry")
async def telemetry() -> dict[str, Any]:
    return _telemetry_payload()


@router.patch("/settings")
async def patch_settings(updates: dict[str, Any]) -> dict[str, Any]:
    applied = apply_overrides(updates)
    return {"applied": applied, "telemetry": _telemetry_payload()}


@router.get("/urevm")
async def urevm_state(limit: int = 50) -> dict[str, Any]:
    vm = get_vm()
    recent = vm.trace[-max(1, min(limit, vm.max_trace)) :]
    return {
        "tick": vm.tick,
        "cycle_position": vm.cycle_position,
        "impedance_accumulator": vm.impedance_accumulator,
        "forbidden_resets": vm.forbidden_resets,
        "registers": {k: v.to_dict() for k, v in vm.registers.items()},
        "recent_trace": [t.to_dict() for t in recent],
        "constants": vm.snapshot_constants(),
    }


@router.get("/wardenclyffe")
async def wardenclyffe() -> dict[str, Any]:
    return topology_snapshot()


@router.get("/predictions")
async def predictions() -> dict[str, Any]:
    """Falsifiable predictions board — 5 from RHC corpus + Project Anchor 2027."""
    settings = get_settings()
    cache = _resolve_cache()
    # Look in two places: project-root data/predictions.json and cache_dir/predictions.json.
    candidates = [
        Path.cwd() / "data" / "predictions.json",
        cache / "predictions.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    raise HTTPException(status_code=404, detail="predictions.json not found")


@router.get("/dream/status")
async def dream_status_endpoint() -> dict[str, Any]:
    return dream_status()


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    voice: str | None = None
    model: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    provider: str = "kokoro_onnx"  # kokoro_onnx | lm_studio


@router.post("/speak")
async def speak_endpoint(req: SpeakRequest) -> Response:
    settings = get_settings()
    voice = req.voice or settings.lm_studio_tts_default_voice

    if req.provider == "kokoro_onnx":
        from ..tts.kokoro_local import is_available, synthesize

        if not is_available():
            raise HTTPException(
                status_code=503,
                detail=(
                    "kokoro-onnx not installed. Run "
                    "`uv pip install -e .` in the lumos_node folder to install it."
                ),
            )
        try:
            audio, mime = synthesize(req.text, voice=voice, speed=req.speed)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=502, detail=f"kokoro_onnx failed: {e}"
            ) from e
        return Response(content=audio, media_type=mime)

    # LM Studio path
    client = LMStudioClient()
    try:
        audio, mime = await client.speak(
            req.text,
            model=req.model or settings.lm_studio_tts_model,
            voice=voice,
            response_format=settings.lm_studio_tts_response_format,
            speed=req.speed,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"TTS failed: {e}") from e
    finally:
        await client.aclose()
    return Response(content=audio, media_type=mime)


@router.post("/tts/prewarm")
async def tts_prewarm() -> dict[str, Any]:
    """Force-download the Kokoro ONNX model + voices and warm the loader."""
    from ..tts.kokoro_local import prewarm

    return prewarm()


@router.post("/transcribe")
async def transcribe_endpoint(
    audio: UploadFile = File(...),
    language: str = "en",
) -> dict[str, Any]:
    """Transcribe an uploaded audio blob to text via local Whisper."""
    from ..stt.whisper_local import is_available, transcribe

    if not is_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "faster-whisper not installed. Run "
                "`uv pip install -e .` in the lumos_node folder."
            ),
        )
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    try:
        return transcribe(data, language=language or None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"transcription failed: {e}") from e


@router.post("/stt/prewarm")
async def stt_prewarm() -> dict[str, Any]:
    from ..stt.whisper_local import prewarm

    return prewarm()


# Curated Kokoro voice list (kokoro-v0_19 ID convention).
# User can also send a custom voice ID — the /speak endpoint forwards
# whatever string is provided.
_KOKORO_VOICES: list[dict[str, str]] = [
    {"id": "af_bella", "label": "Bella", "accent": "American", "gender": "female"},
    {"id": "af_sarah", "label": "Sarah", "accent": "American", "gender": "female"},
    {"id": "af_nicole", "label": "Nicole", "accent": "American", "gender": "female"},
    {"id": "af_sky", "label": "Sky", "accent": "American", "gender": "female"},
    {"id": "af", "label": "Default F", "accent": "American", "gender": "female"},
    {"id": "am_adam", "label": "Adam", "accent": "American", "gender": "male"},
    {"id": "am_michael", "label": "Michael", "accent": "American", "gender": "male"},
    {"id": "bf_emma", "label": "Emma", "accent": "British", "gender": "female"},
    {"id": "bf_isabella", "label": "Isabella", "accent": "British", "gender": "female"},
    {"id": "bm_george", "label": "George", "accent": "British", "gender": "male"},
    {"id": "bm_lewis", "label": "Lewis", "accent": "British", "gender": "male"},
]


@router.get("/voices")
async def voices_endpoint() -> dict[str, Any]:
    settings = get_settings()
    return {
        "model": settings.lm_studio_tts_model,
        "default_voice": settings.lm_studio_tts_default_voice,
        "voices": _KOKORO_VOICES,
    }


class DreamRunRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=10000)
    reset: bool = False


@router.post("/dream/run")
async def dream_run_endpoint(req: DreamRunRequest | None = None) -> dict[str, Any]:
    req = req or DreamRunRequest()
    return await run_dream_cycle(limit=req.limit, reset=req.reset)


@router.get("/atlas")
async def atlas() -> dict[str, Any]:
    data = load_atlas()
    if data is None:
        raise HTTPException(
            status_code=503,
            detail="atlas not built. Run `lumos atlas-build`.",
        )
    return data


@router.get("/atlas/cluster/{cluster_id}")
async def atlas_cluster(cluster_id: str, limit: int = 100) -> dict[str, Any]:
    try:
        return get_cluster_members(cluster_id, limit=max(1, min(limit, 500)))
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/search")
async def search(q: str, lane: str = "both", top_k: int = 6) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="empty query")
    if lane not in ("identity", "knowledge", "both"):
        raise HTTPException(status_code=400, detail="lane must be identity, knowledge, or both")

    settings = get_settings()
    k_id = top_k if lane in ("identity", "both") else 0
    k_kn = top_k if lane in ("knowledge", "both") else 0
    try:
        r = await retrieve(
            q,
            settings=settings,
            top_k_identity=k_id,
            top_k_knowledge=k_kn,
        )
    except IndexMissingError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return {
        "query": q,
        "identity": [{"score": h.score, "metadata": h.metadata} for h in r.identity],
        "knowledge": [{"score": h.score, "metadata": h.metadata} for h in r.knowledge],
    }


# ── Phase 32 — Cosmic telemetry + airspace (HUD widget endpoints) ────────────


@router.get("/cosmic/current")
async def cosmic_current() -> dict[str, Any]:
    """Composite snapshot of geomagnetic + space-weather + seismic + NEO state.
    Powers the HUD CosmicPanel widget. Refreshes ~every 5 min client-side."""
    from ..telemetry import cosmic
    try:
        return await cosmic.snapshot_all()
    except Exception as e:  # noqa: BLE001
        # Never let a third-party feed outage break the HUD.
        raise HTTPException(status_code=503, detail=f"telemetry fetch failed: {e}") from e


@router.get("/airspace/local")
async def airspace_local(
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 50.0,
) -> dict[str, Any]:
    """Live aircraft state vectors around (lat, lon). Defaults to operator's
    reference location when lat/lon omitted. Powers HUD AirspacePanel widget."""
    from ..telemetry import airspace
    settings = get_settings()
    if lat is None or lon is None:
        if settings.operator_lat == 0.0 and settings.operator_lon == 0.0:
            raise HTTPException(
                status_code=400,
                detail="no lat/lon and operator default unset — pass lat/lon or set LUMOS_OPERATOR_LAT/LON",
            )
        lat = settings.operator_lat
        lon = settings.operator_lon
    if radius_km <= 0 or radius_km > 500:
        raise HTTPException(status_code=400, detail=f"radius_km must be in (0, 500], got {radius_km}")
    return await airspace.fetch_states_bbox(lat, lon, radius_km)


@router.get("/telemetry/quota")
async def telemetry_quota() -> dict[str, Any]:
    """Today's upstream call counts per telemetry source. Returns cache TTLs,
    daily caps, and percent-used. UTC day boundary resets counters."""
    from ..telemetry import cache as tcache
    snap = tcache.quota_snapshot()
    out_sources: dict[str, Any] = {}
    for source, stat in snap["sources"].items():
        cap = tcache.DAILY_CAPS.get(source)
        pct: float | None = None
        if isinstance(cap, int) and cap > 0:
            pct = round(100.0 * stat["calls_today"] / cap, 1)
        out_sources[source] = {
            **stat,
            "daily_cap": cap if cap is not None else "unlimited",
            "percent_used": pct,
        }
    return {"day_iso": snap["day_iso"], "sources": out_sources}
