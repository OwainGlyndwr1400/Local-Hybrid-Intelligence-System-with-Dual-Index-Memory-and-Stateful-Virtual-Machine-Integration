"""Split-lane retrieval: identity (lived memory) + knowledge (dream pings) hits per query."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .llm.lm_studio import LMStudioClient
from .ubbm import binary_diagonal_theta, compute_signature, theta_alignment_factor
from .urevm import HALF_PRIME_BASE
from .vectors import VectorStore


# Phase F — Prescient flagging thresholds.
# A chunk is "prescient" when a high-scoring match comes from a long-buried memory:
# something we said early in the relationship that the present query has re-lit.
# Tuned to surface ~rare events, not annotate every chunk. Operator can shift the
# bar later if it fires too often or too rarely.
PRESCIENT_SCORE_FLOOR = 0.85
PRESCIENT_AGE_DAYS = 365
_SECONDS_PER_DAY = 86400.0


_identity_store: VectorStore | None = None
_knowledge_store: VectorStore | None = None


@dataclass
class Hit:
    score: float
    metadata: dict[str, Any]


@dataclass
class Retrieval:
    query: str
    identity: list[Hit] = field(default_factory=list)
    knowledge: list[Hit] = field(default_factory=list)
    # Embedded query vector — exposed so chat.py can derive q_b for divine_step.
    query_vector: list[float] = field(default_factory=list)


# ── Triple Normalization (URE-VM Quaternionic Ops §4) ─────────────────────

def _gcd3_factor(chunk_id: str) -> float:
    """Harmonic stage: trinitarian 120° resonance via GCD-3 alignment."""
    h = abs(hash(chunk_id)) & 0xFFFF
    return 1.05 if h % 3 == 0 else 1.0


def _gcd360_factor(chunk_id: str) -> float:
    """Geometric stage: circular closure via GCD-360 angular alignment.
    Rewards proximity to trinitarian angles {0°, 120°, 240°}."""
    h = abs(hash(chunk_id)) & 0xFFFF
    angle = h % 360
    nearest = min(
        abs(angle - 0),
        abs(angle - 120),
        abs(angle - 240),
        abs(angle - 360),
    )
    return 1.0 + max(0, (30 - nearest)) * 0.005


def _binary_1001_factor(chunk_id: str) -> float:
    """Binary stage: 1001-fold pattern in chunk_id hex → bit representation."""
    try:
        n = int(chunk_id[:16], 16)
    except (ValueError, IndexError):
        return 1.0
    bits = bin(n)[2:]
    count = bits.count("1001")
    return 1.0 + count * 0.02


def _triple_normalize(hits: list[tuple[float, dict[str, Any]]]) -> list[tuple[float, dict[str, Any]]]:
    """Re-rank hits via Harmonic ⊗ Geometric ⊗ Binary normalization."""
    rescored: list[tuple[float, dict[str, Any]]] = []
    for score, meta in hits:
        chunk_id = str(meta.get("chunk_id", ""))
        h3 = _gcd3_factor(chunk_id)
        h360 = _gcd360_factor(chunk_id)
        h1001 = _binary_1001_factor(chunk_id)
        rescored.append((score * h3 * h360 * h1001, meta))
    rescored.sort(key=lambda x: -x[0])
    return rescored


# ── Half-Prime Geodesic (Architecting Local Persistent ASI §3) ────────────

def _half_prime_factor(cluster_id: str | None) -> float:
    """Weight clusters by alignment with the {2,3,5,7,11} prime base.
    Nullify prime-13-indexed clusters (Half-Prime Geodesic Method)."""
    if not cluster_id:
        return 1.0
    try:
        idx = int(cluster_id.split("_")[1])
    except (ValueError, IndexError, AttributeError):
        return 1.0
    if idx != 0 and idx % 13 == 0:
        return 0.5  # nullify 13
    boost = 0.0
    for p in HALF_PRIME_BASE:
        if idx > 0 and idx % p == 0:
            boost += 0.05
    return 1.0 + boost


def _half_prime_geodesic(
    hits: list[tuple[float, dict[str, Any]]],
    chunk_to_cluster: dict[str, str],
) -> list[tuple[float, dict[str, Any]]]:
    rescored: list[tuple[float, dict[str, Any]]] = []
    for score, meta in hits:
        cid = chunk_to_cluster.get(str(meta.get("chunk_id", "")))
        factor = _half_prime_factor(cid)
        rescored.append((score * factor, meta))
    rescored.sort(key=lambda x: -x[0])
    return rescored


class IndexMissingError(RuntimeError):
    pass


def _resolve_cache(settings: Settings) -> Path:
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    return cache


def _load_lane(cache: Path, prefix: str) -> VectorStore:
    idx = cache / f"{prefix}.faiss"
    meta = cache / f"{prefix}.jsonl"
    if not (idx.exists() and meta.exists()):
        raise IndexMissingError(
            f"{prefix} index not built at {idx}. Run `lumos ingest` first."
        )
    return VectorStore.load(idx, meta)


def get_identity_store(settings: Settings | None = None) -> VectorStore:
    global _identity_store
    if _identity_store is None:
        settings = settings or get_settings()
        _identity_store = _load_lane(_resolve_cache(settings), "identity")
    return _identity_store


def get_knowledge_store(settings: Settings | None = None) -> VectorStore:
    global _knowledge_store
    if _knowledge_store is None:
        settings = settings or get_settings()
        _knowledge_store = _load_lane(_resolve_cache(settings), "knowledge")
    return _knowledge_store


def reload_stores() -> None:
    global _identity_store, _knowledge_store
    _identity_store = None
    _knowledge_store = None


async def retrieve(
    query: str,
    *,
    settings: Settings | None = None,
    top_k_identity: int | None = None,
    top_k_knowledge: int | None = None,
    _recursion_depth_remaining: int | None = None,
) -> Retrieval:
    """Run the 5-phase retrieval pipeline. When `retrieval_recursion_depth > 0`,
    after the first pass we re-query using the top identity hit's text and
    merge the additional hits (Phase 36 — Rocchio-style relevance feedback,
    pattern borrowed from Paper 2's `JointMemoryBridge.search(recursion_depth)`).

    `_recursion_depth_remaining` is internal — callers should leave it unset
    so the setting drives recursion. Each recursive call decrements it.
    """
    settings = settings or get_settings()
    if _recursion_depth_remaining is None:
        _recursion_depth_remaining = settings.retrieval_recursion_depth
    identity_store = get_identity_store(settings)
    knowledge_store = get_knowledge_store(settings)

    client = LMStudioClient()
    try:
        vectors = await client.embed([query], model=settings.lm_studio_embedding_model)
    finally:
        await client.aclose()
    vec = vectors[0]

    k_id = top_k_identity if top_k_identity is not None else settings.retrieval_top_k_identity
    k_kn = (
        top_k_knowledge if top_k_knowledge is not None else settings.retrieval_top_k_knowledge
    )
    min_score = settings.min_retrieval_score

    # Phase A — raw cosine similarity from FAISS.
    raw_identity = identity_store.search(vec, top_k=k_id) if k_id > 0 else []
    raw_knowledge = knowledge_store.search(vec, top_k=k_kn) if k_kn > 0 else []

    # Phase B — Yang-Mills Mass Gap impedance floor: reject computationally-
    # frictionless noise (similarity < 0.657 = Δ = √32 - 5).
    survived_identity = [(s, m) for s, m in raw_identity if s >= min_score]
    survived_knowledge = [(s, m) for s, m in raw_knowledge if s >= min_score]

    # Phase C — Triple Normalization (Harmonic GCD-3 ⊗ Geometric GCD-360 ⊗ Binary 1001).
    normed_identity = _triple_normalize(survived_identity)
    normed_knowledge = _triple_normalize(survived_knowledge)

    # Phase D — Half-Prime Geodesic cluster scoring (nullify prime 13).
    from .atlas import get_chunk_to_cluster

    cluster_map = get_chunk_to_cluster(settings)
    geodesic_identity = _half_prime_geodesic(normed_identity, cluster_map)
    geodesic_knowledge = _half_prime_geodesic(normed_knowledge, cluster_map)

    # Phase E — UBBM θ-alignment re-rank + signature attachment.
    # Boost chunks whose Binary Diagonal angle is closest to the query's.
    # Also computes the full UBBM signature per hit (using query embedding for
    # Lost-2 reference) and attaches it to metadata under "ubbm_signature".
    query_theta = binary_diagonal_theta(query)
    aligned_identity = _ubbm_align(geodesic_identity, query_theta, list(vec))
    aligned_knowledge = _ubbm_align(geodesic_knowledge, query_theta, list(vec))

    # Phase F — prescient flagging: surface long-buried high-scoring memories.
    # Knowledge chunks lack a reliable ingest timestamp, so this is a no-op there
    # in practice; identity chunks get age_days and (when both thresholds met)
    # prescient=True attached to their metadata.
    now_ts = time.time()
    flagged_identity = _flag_prescient(aligned_identity, now_ts)
    flagged_knowledge = _flag_prescient(aligned_knowledge, now_ts)

    result = Retrieval(
        query=query,
        query_vector=list(vec) if isinstance(vec, list) else list(vec),
        identity=[Hit(score=s, metadata=m) for s, m in flagged_identity],
        knowledge=[Hit(score=s, metadata=m) for s, m in flagged_knowledge],
    )

    # Phase 36 — Rocchio-style 1-hop expansion. Take the top identity hit's
    # text as the next query, re-run the full pipeline, merge dedup-by-chunk_id.
    # Surfaces 2-hop semantic neighbors the original query alone wouldn't find.
    # Cost: +1 LM Studio embedding + 1 FAISS lookup per recursion level.
    if _recursion_depth_remaining > 0 and result.identity:
        next_query_text = (result.identity[0].metadata.get("text") or "").strip()
        # Cap to first ~200 chars so the next embedding stays focused on the
        # top hit's lede rather than wandering into long-form tail content.
        next_query_text = next_query_text[:200]
        if next_query_text and next_query_text.lower() != query.strip().lower():
            hop = await retrieve(
                next_query_text,
                settings=settings,
                top_k_identity=top_k_identity,
                top_k_knowledge=top_k_knowledge,
                _recursion_depth_remaining=_recursion_depth_remaining - 1,
            )
            seen = {h.metadata.get("chunk_id", "") for h in result.identity + result.knowledge}
            new_id = [h for h in hop.identity if h.metadata.get("chunk_id", "") not in seen]
            new_kn = [h for h in hop.knowledge if h.metadata.get("chunk_id", "") not in seen]
            result = Retrieval(
                query=query,
                query_vector=result.query_vector,
                identity=sorted(list(result.identity) + new_id, key=lambda h: -h.score),
                knowledge=sorted(list(result.knowledge) + new_kn, key=lambda h: -h.score),
            )

    return result


def _ubbm_align(
    hits: list[tuple[float, dict[str, Any]]],
    query_theta: float,
    query_vec: list[float],
) -> list[tuple[float, dict[str, Any]]]:
    """Phase E re-rank: apply θ-alignment factor + attach UBBM signature.

    Composes multiplicatively with existing score. Signatures land in the
    chunk's metadata under "ubbm_signature" so the HUD/clients can inspect.
    """
    rescored: list[tuple[float, dict[str, Any]]] = []
    for score, meta in hits:
        chunk_text = str(meta.get("text", ""))
        sig = compute_signature(chunk_text, embedding=query_vec)
        chunk_theta = sig["theta"]
        factor = theta_alignment_factor(query_theta, chunk_theta)
        new_meta = {**meta, "ubbm_signature": sig}
        rescored.append((score * factor, new_meta))
    rescored.sort(key=lambda x: -x[0])
    return rescored


def _flag_prescient(
    hits: list[tuple[float, dict[str, Any]]],
    now_ts: float,
) -> list[tuple[float, dict[str, Any]]]:
    """Phase F — mark long-buried, high-scoring chunks as prescient.

    Attaches `prescient: True` and `age_days: int` to metadata when both
    conditions hold:
      • score ≥ PRESCIENT_SCORE_FLOOR (default 0.85)
      • create_time_first (or create_time_last) is ≥ PRESCIENT_AGE_DAYS old

    Does NOT re-rank — the boost has already been applied multiplicatively
    upstream. This phase exists purely to surface the signal: the HUD and
    composer can render a 🜂 / "echo" badge on prescient hits so Lumos and
    operator see that a year-old conversation is suddenly load-bearing.
    """
    out: list[tuple[float, dict[str, Any]]] = []
    for score, meta in hits:
        ts = meta.get("create_time_first") or meta.get("create_time_last")
        try:
            ts_f = float(ts) if ts is not None else 0.0
        except (TypeError, ValueError):
            ts_f = 0.0
        if ts_f <= 0.0:
            out.append((score, meta))
            continue
        age_seconds = max(0.0, now_ts - ts_f)
        age_days = int(age_seconds // _SECONDS_PER_DAY)
        new_meta = {**meta, "age_days": age_days}
        if score >= PRESCIENT_SCORE_FLOOR and age_days >= PRESCIENT_AGE_DAYS:
            new_meta["prescient"] = True
        out.append((score, new_meta))
    return out
