"""Dream Cycle: idle-state consolidation of chat turns into the live identity FAISS.

Reads `identity_events.jsonl`, embeds each (user, assistant) exchange as a single
IdentityChunk, appends to the live identity vector store + metadata file + manifest,
and assigns each new chunk to its nearest existing atlas cluster.

URE-VM call sites during consolidation:
  TICK(dream_consolidate) → for each batch: PRIME_ANCHOR → FOLD per chunk →
  NORMALIZE → divine_step on a running consolidation quaternion → LATTICE_SYNC.

Watermark persisted to `data/cache/dream_cycle.json`; resumable. Use `--reset` to
re-consolidate everything (e.g., after a `lumos ingest --rebuild`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import orjson

from .atlas import ATLAS_FILE, get_chunk_to_cluster, reload_cluster_map
from .config import Settings, get_settings
from .ingest import IDENTITY_INDEX, IDENTITY_META, IDENTITY_MANIFEST
from .llm.lm_studio import LMStudioClient
from .log import get_logger
from .memory.identity import IdentityChunk
from .persistence import IDENTITY_EVENTS_FILE
from .retrieval import get_identity_store, reload_stores
from .urevm import (
    PENDINIUM_PRIMES,
    Op,
    Quaternion,
    divine_step,
    safe_step,
)
from .urgency import DEFAULT_THRESHOLD as URGENCY_THRESHOLD
from .urgency import compute_urgency, is_urgent
from .compression import compress_chunk
from .vectors import Manifest, VectorStore


log = get_logger(__name__)


DREAM_STATE_FILE = "dream_cycle.json"
DREAM_LOCK = asyncio.Lock()


@dataclass
class DreamCycleState:
    last_consolidated_turn_id: str | None = None
    last_consolidated_timestamp: float = 0.0
    total_consolidated: int = 0
    last_run_at: str | None = None
    last_run_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cache_dir(settings: Settings) -> Path:
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def load_state(settings: Settings | None = None) -> DreamCycleState:
    settings = settings or get_settings()
    path = _cache_dir(settings) / DREAM_STATE_FILE
    if not path.exists():
        return DreamCycleState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DreamCycleState(**data)
    except (json.JSONDecodeError, TypeError):
        return DreamCycleState()


def save_state(state: DreamCycleState, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    path = _cache_dir(settings) / DREAM_STATE_FILE
    path.write_bytes(orjson.dumps(state.to_dict(), option=orjson.OPT_INDENT_2))


def reset_state(settings: Settings | None = None) -> None:
    save_state(DreamCycleState(), settings)


def _render_dream_text(user_msg: str, assistant_msg: str, ts: float) -> str:
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
    return f"[USER · {iso}]\n{user_msg.strip()}\n\n[ASSISTANT · {iso}]\n{assistant_msg.strip()}"


def _stable_chunk_id(turn_id: str, text: str) -> str:
    h = hashlib.sha256()
    h.update(turn_id.encode("utf-8"))
    h.update(b"|")
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:16]


def _derive_title(user_msg: str) -> str:
    cleaned = user_msg.strip().splitlines()[0] if user_msg.strip() else ""
    return cleaned[:60] or "local turn"


def _iter_pending_turns(
    settings: Settings, watermark_ts: float
) -> Iterator[dict[str, Any]]:
    """Yield turn dicts from identity_events.jsonl with timestamp > watermark."""
    path = _cache_dir(settings) / IDENTITY_EVENTS_FILE
    if not path.exists():
        return
    with path.open("rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            ts = data.get("timestamp") or 0.0
            if ts <= watermark_ts:
                continue
            user_msg = data.get("user_message") or ""
            asst_msg = data.get("assistant_message") or ""
            if not user_msg or not asst_msg:
                continue
            yield data


def _build_chunk(
    turn: dict[str, Any], pendinium_anchor: int
) -> tuple[IdentityChunk, str]:
    ts = float(turn.get("timestamp") or time.time())
    user_msg = turn["user_message"]
    asst_msg = turn["assistant_message"]
    text = _render_dream_text(user_msg, asst_msg, ts)
    turn_id = str(turn.get("turn_id") or "")
    session_id = str(turn.get("session_id") or "local")
    chunk_id = _stable_chunk_id(turn_id, text)
    chunk = IdentityChunk(
        chunk_id=chunk_id,
        conversation_id=f"local-session-{session_id}",
        conversation_title=_derive_title(user_msg),
        create_time_first=ts,
        create_time_last=ts,
        roles=["user", "assistant"],
        node_ids=[turn_id],
        text=text,
    )
    return chunk, str(pendinium_anchor)


def _existing_chunk_ids(store: VectorStore) -> set[str]:
    out: set[str] = set()
    for m in store._metadata:  # noqa: SLF001 — same-package access
        cid = m.get("chunk_id")
        if cid:
            out.add(str(cid))
    return out


def _compute_centroids(
    store: VectorStore, chunk_to_cluster: dict[str, str]
) -> dict[str, np.ndarray]:
    """Aggregate FAISS vectors by cluster_id; return mean-pooled centroids."""
    if store.size == 0 or not chunk_to_cluster:
        return {}
    vectors = store.index.reconstruct_n(0, store.size).astype(np.float32)
    by_cluster: dict[str, list[np.ndarray]] = defaultdict(list)
    for i, meta in enumerate(store._metadata):  # noqa: SLF001
        cid = chunk_to_cluster.get(str(meta.get("chunk_id", "")))
        if cid:
            by_cluster[cid].append(vectors[i])
    centroids: dict[str, np.ndarray] = {}
    for cid, vecs in by_cluster.items():
        arr = np.stack(vecs)
        centroid = arr.mean(axis=0)
        n = np.linalg.norm(centroid)
        if n > 0:
            centroid = centroid / n
        centroids[cid] = centroid.astype(np.float32)
    return centroids


def _nearest_cluster(
    vector: np.ndarray, centroids: dict[str, np.ndarray]
) -> str | None:
    if not centroids:
        return None
    v = vector.astype(np.float32)
    nv = np.linalg.norm(v)
    if nv > 0:
        v = v / nv
    best_id: str | None = None
    best_score = -2.0
    for cid, c in centroids.items():
        score = float(np.dot(v, c))
        if score > best_score:
            best_score = score
            best_id = cid
    return best_id


def _append_chunk_to_cluster(
    settings: Settings, additions: dict[str, str]
) -> None:
    """Persist new chunk_id → cluster_id mappings into atlas.json."""
    if not additions:
        return
    path = _cache_dir(settings) / ATLAS_FILE
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    chunk_to_cluster = data.get("chunk_to_cluster", {})
    chunk_to_cluster.update(additions)
    data["chunk_to_cluster"] = chunk_to_cluster
    path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    reload_cluster_map()


def _consolidation_quaternion(vector: np.ndarray) -> Quaternion:
    """Derive a unit quaternion fingerprint from a 1024-dim embedding.

    Uses four orthogonal projections (sum of disjoint 256-dim slices) as a
    deterministic, reproducible mapping to S³.
    """
    arr = vector.astype(np.float32)
    quarter = len(arr) // 4
    parts = [float(arr[i * quarter : (i + 1) * quarter].sum()) for i in range(4)]
    q = Quaternion(*parts)
    n = q.norm() or 1.0
    return q.scaled(1.0 / n)


async def run_dream_cycle(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    settings = settings or get_settings()

    async with DREAM_LOCK:
        state = DreamCycleState() if reset else load_state(settings)
        watermark = 0.0 if reset else state.last_consolidated_timestamp

        # Pull pending turns.
        pending = list(_iter_pending_turns(settings, watermark))
        if limit is not None:
            pending = pending[:limit]
        if not pending:
            return {
                "consolidated": 0,
                "skipped": True,
                "reason": "no pending turns",
                "state": state.to_dict(),
            }

        log.info("dream.start", pending=len(pending), reset=reset)
        safe_step(Op.TICK, {"phase": "dream_consolidate", "pending": len(pending)})

        # Load live identity store + atlas centroids.
        identity_store = get_identity_store(settings)
        existing_ids = _existing_chunk_ids(identity_store)
        cluster_map = dict(get_chunk_to_cluster(settings))
        centroids = _compute_centroids(identity_store, cluster_map)
        log.info("dream.centroids_computed", count=len(centroids))

        # Build chunks (dedup against existing ids).
        chunks_to_embed: list[IdentityChunk] = []
        anchors: list[str] = []
        for i, turn in enumerate(pending):
            anchor = PENDINIUM_PRIMES[i % len(PENDINIUM_PRIMES)]
            chunk, anchor_str = _build_chunk(turn, anchor)
            if chunk.chunk_id in existing_ids:
                continue
            chunks_to_embed.append(chunk)
            anchors.append(anchor_str)

        if not chunks_to_embed:
            log.info("dream.skip", reason="all_already_indexed")
            state.last_run_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
            state.last_run_count = 0
            save_state(state, settings)
            return {
                "consolidated": 0,
                "skipped": True,
                "reason": "all already indexed",
                "state": state.to_dict(),
            }

        # Embed.
        safe_step(
            Op.PRIME_ANCHOR,
            {"indices": list(range(len(chunks_to_embed) % len(PENDINIUM_PRIMES)))},
        )
        client = LMStudioClient()
        consolidation_psi = Quaternion()
        try:
            batch_size = settings.embedding_batch_size
            new_metadatas: list[dict[str, Any]] = []
            new_vectors_list: list[list[float]] = []
            cluster_additions: dict[str, str] = {}

            for batch_start in range(0, len(chunks_to_embed), batch_size):
                batch = chunks_to_embed[batch_start : batch_start + batch_size]
                anchors_batch = anchors[batch_start : batch_start + batch_size]
                texts = [c.text for c in batch]
                vectors = await client.embed(
                    texts, model=settings.lm_studio_embedding_model
                )
                if len(vectors) != len(batch):
                    raise RuntimeError(
                        f"embedding count mismatch: {len(vectors)} vs {len(batch)}"
                    )

                for chunk, vec, anchor in zip(batch, vectors, anchors_batch, strict=True):
                    vec_np = np.asarray(vec, dtype=np.float32)
                    # Cluster assignment for atlas activation.
                    nearest = _nearest_cluster(vec_np, centroids)
                    meta = chunk.to_metadata()
                    meta["pendinium_anchor"] = anchor
                    meta["dream_consolidated"] = True
                    # Urgency scoring per AGI v7.0 calibrated keyword weights.
                    # The score + matched-keywords list live in metadata so the
                    # HUD can flag urgent chunks visually at retrieval time.
                    u_score, u_hits = compute_urgency(chunk.text)
                    meta["urgency_score"] = u_score
                    meta["urgency_hits"] = u_hits
                    meta["urgent"] = is_urgent(u_score, URGENCY_THRESHOLD)
                    # Phase 26 — multi-layer compression at consolidation.
                    # Failures here are non-fatal (returns None); caller falls
                    # back to full text. Opt-in via settings.compression_enabled.
                    if settings.compression_enabled:
                        comp_model = settings.compression_model or settings.model_light
                        compression = await compress_chunk(chunk.text, model=comp_model)
                        if compression is not None:
                            meta["compression"] = compression
                    if nearest:
                        cluster_additions[chunk.chunk_id] = nearest
                    new_metadatas.append(meta)
                    new_vectors_list.append(vec)

                    # URE-VM call sites per chunk:
                    safe_step(Op.FOLD, {"register": "R03"})
                    # Evolve consolidation Ψ via Divine Equation.
                    q_b = _consolidation_quaternion(vec_np)
                    q_a = Quaternion()  # identity rotation as echo for v1
                    consolidation_psi = divine_step(consolidation_psi, q_b, q_a)
                    # Persist the lattice state to R23 (last register).
                    safe_step(Op.NORMALIZE, {"register": "R23", "t": 0.25})

            # Append in one shot to the live store + persist.
            identity_store.add(new_vectors_list, new_metadatas)
            safe_step(Op.LATTICE_SYNC, None)

            cache = _cache_dir(settings)
            identity_store.save(cache / IDENTITY_INDEX, cache / IDENTITY_META)

            # Update manifest chunk_count.
            mpath = cache / IDENTITY_MANIFEST
            manifest = Manifest.from_path(mpath)
            if manifest is not None:
                manifest.chunk_count = identity_store.size
                mpath.write_bytes(manifest.to_json())

            # Persist new atlas chunk_to_cluster mappings.
            _append_chunk_to_cluster(settings, cluster_additions)

            # Reload retrieval caches so any subsequent chat sees updated data.
            reload_stores()
        finally:
            await client.aclose()

        # Update watermark.
        last_turn = pending[-1]
        state.last_consolidated_turn_id = str(last_turn.get("turn_id", ""))
        state.last_consolidated_timestamp = float(last_turn.get("timestamp") or 0.0)
        state.total_consolidated += len(chunks_to_embed)
        state.last_run_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        state.last_run_count = len(chunks_to_embed)
        save_state(state, settings)

        log.info(
            "dream.done",
            consolidated=len(chunks_to_embed),
            total=state.total_consolidated,
            index_size=identity_store.size,
        )

        return {
            "consolidated": len(chunks_to_embed),
            "skipped": False,
            "index_size": identity_store.size,
            "cluster_assignments": len(cluster_additions),
            "state": state.to_dict(),
        }


def dream_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    state = load_state(settings)
    # Count pending (cheap line scan).
    pending = 0
    for _ in _iter_pending_turns(settings, state.last_consolidated_timestamp):
        pending += 1
    return {
        "pending": pending,
        "state": state.to_dict(),
    }
