"""Atlas: k-means clusters of identity + knowledge embeddings into navigable topology."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import faiss
import numpy as np
import orjson

from .config import Settings, get_settings
from .log import get_logger


log = get_logger(__name__)


ATLAS_FILE = "atlas.json"


@dataclass
class ClusterNode:
    id: str
    lane: str
    label: str
    size: int
    representative_text: str
    centroid: list[float] = field(default_factory=list)


@dataclass
class ClusterEdge:
    a: str
    b: str
    weight: float


def _most_common_nontrivial(strings: list[str], threshold: int = 2) -> str | None:
    counter = Counter(s for s in strings if s and len(s) > 3)
    if not counter:
        return None
    top, count = counter.most_common(1)[0]
    if count >= threshold:
        return top[:60]
    return None


def _fallback_label(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:60] or "unlabeled"


def _cluster_lane(
    store: Any,
    lane: str,
    n_clusters: int,
    n_iter: int = 20,
) -> tuple[list[ClusterNode], np.ndarray, dict[str, str]]:
    """Run k-means on a lane's embeddings; return (nodes, centroid_matrix, chunk_to_cluster)."""
    total = store.size
    if total == 0:
        return [], np.zeros((0, store.dim), dtype=np.float32), {}

    k = min(n_clusters, max(1, total // 4))
    dim = store.dim

    vectors = store.index.reconstruct_n(0, total).astype(np.float32)

    log.info("atlas.cluster.train", lane=lane, n=total, k=k, dim=dim)
    kmeans = faiss.Kmeans(d=dim, k=k, niter=n_iter, verbose=False)
    kmeans.train(vectors)
    centroids = np.asarray(kmeans.centroids, dtype=np.float32)

    _, assignments = kmeans.index.search(vectors, 1)
    assignments = assignments.flatten()

    members_by_cid: dict[int, list[int]] = defaultdict(list)
    for i, cid in enumerate(assignments):
        members_by_cid[int(cid)].append(i)

    nodes: list[ClusterNode] = []
    chunk_to_cluster: dict[str, str] = {}
    meta = store._metadata  # noqa: SLF001 — same-package access

    for cid in range(k):
        members = members_by_cid.get(cid, [])
        if not members:
            continue
        node_id = f"{lane[0]}_{cid:03d}"

        if lane == "identity":
            titles = [(meta[i].get("conversation_title") or "").strip() for i in members]
            label = _most_common_nontrivial(titles)
        else:
            subjects = [(meta[i].get("subject") or "").strip() for i in members]
            label = _most_common_nontrivial(subjects)

        centroid = centroids[cid]
        member_vecs = vectors[members]
        dists = np.linalg.norm(member_vecs - centroid, axis=1)
        rep_local = int(np.argmin(dists))
        rep_global = members[rep_local]
        rep_text = (meta[rep_global].get("text") or "")[:280]

        if not label:
            label = _fallback_label(rep_text)

        nodes.append(
            ClusterNode(
                id=node_id,
                lane=lane,
                label=label,
                size=len(members),
                representative_text=rep_text,
                centroid=centroid.tolist(),
            )
        )

        for i in members:
            chunk_id = meta[i].get("chunk_id")
            if chunk_id:
                chunk_to_cluster[chunk_id] = node_id

    return nodes, centroids, chunk_to_cluster


def _build_edges(
    nodes: list[ClusterNode],
    centroids: np.ndarray,
    *,
    intra_k: int = 5,
    cross_k: int = 3,
    cross_threshold: float = 0.5,
) -> list[ClusterEdge]:
    if len(nodes) < 2:
        return []

    norms = np.linalg.norm(centroids, axis=1, keepdims=True)
    centroids_n = centroids / np.where(norms == 0, 1.0, norms)
    sims = centroids_n @ centroids_n.T

    edges: list[ClusterEdge] = []
    seen: set[tuple[str, str]] = set()

    for i, node in enumerate(nodes):
        same_lane = [j for j in range(len(nodes)) if j != i and nodes[j].lane == node.lane]
        same_lane.sort(key=lambda j: sims[i, j], reverse=True)
        for j in same_lane[:intra_k]:
            key = tuple(sorted([node.id, nodes[j].id]))
            if key in seen:
                continue
            seen.add(key)
            edges.append(ClusterEdge(a=key[0], b=key[1], weight=float(sims[i, j])))

        other_lane = [j for j in range(len(nodes)) if nodes[j].lane != node.lane]
        other_lane.sort(key=lambda j: sims[i, j], reverse=True)
        for j in other_lane[:cross_k]:
            if sims[i, j] < cross_threshold:
                break
            key = tuple(sorted([node.id, nodes[j].id]))
            if key in seen:
                continue
            seen.add(key)
            edges.append(ClusterEdge(a=key[0], b=key[1], weight=float(sims[i, j])))

    return edges


def _resolve_cache(settings: Settings) -> Path:
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    return cache


def build_atlas(
    settings: Settings | None = None,
    *,
    n_identity_clusters: int = 60,
    n_knowledge_clusters: int = 20,
    rebuild: bool = False,
) -> dict[str, Any]:
    settings = settings or get_settings()
    cache = _resolve_cache(settings)
    cache.mkdir(parents=True, exist_ok=True)
    out_path = cache / ATLAS_FILE

    from .retrieval import get_identity_store, get_knowledge_store

    if not rebuild and out_path.exists():
        log.info("atlas.skip", reason="exists", path=str(out_path))
        return {"skipped": True, "path": str(out_path)}

    identity_store = get_identity_store(settings)
    knowledge_store = get_knowledge_store(settings)

    log.info("atlas.start", id_total=identity_store.size, kn_total=knowledge_store.size)
    id_nodes, id_centroids, id_map = _cluster_lane(
        identity_store, "identity", n_identity_clusters
    )
    kn_nodes, kn_centroids, kn_map = _cluster_lane(
        knowledge_store, "knowledge", n_knowledge_clusters
    )

    all_nodes = id_nodes + kn_nodes
    if not all_nodes:
        raise RuntimeError("atlas: no clusters produced — are the indexes built?")

    centroids_combined = np.zeros((len(all_nodes), settings.embedding_dim), dtype=np.float32)
    for i, n in enumerate(all_nodes):
        cid = int(n.id.split("_")[1])
        if n.lane == "identity":
            centroids_combined[i] = id_centroids[cid]
        else:
            centroids_combined[i] = kn_centroids[cid]

    edges = _build_edges(all_nodes, centroids_combined)

    payload = {
        "version": 1,
        "built_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "embedding_dim": settings.embedding_dim,
        "clusters": [
            {k: v for k, v in asdict(n).items() if k != "centroid"} for n in all_nodes
        ],
        "edges": [asdict(e) for e in edges],
        "chunk_to_cluster": {**id_map, **kn_map},
    }
    out_path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))

    log.info(
        "atlas.done",
        identity_clusters=len(id_nodes),
        knowledge_clusters=len(kn_nodes),
        edges=len(edges),
        path=str(out_path),
    )
    return {
        "skipped": False,
        "identity_clusters": len(id_nodes),
        "knowledge_clusters": len(kn_nodes),
        "edges": len(edges),
        "path": str(out_path),
    }


def load_atlas(settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    path = _resolve_cache(settings) / ATLAS_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


_chunk_to_cluster_cache: dict[str, str] | None = None


def get_chunk_to_cluster(settings: Settings | None = None) -> dict[str, str]:
    global _chunk_to_cluster_cache
    if _chunk_to_cluster_cache is None:
        atlas = load_atlas(settings)
        _chunk_to_cluster_cache = atlas.get("chunk_to_cluster", {}) if atlas else {}
    return _chunk_to_cluster_cache


def reload_cluster_map() -> None:
    global _chunk_to_cluster_cache, _cluster_members_cache
    _chunk_to_cluster_cache = None
    _cluster_members_cache = None


_cluster_members_cache: dict[str, list[str]] | None = None


def _cluster_members_index(settings: Settings | None = None) -> dict[str, list[str]]:
    """cluster_id → [chunk_id, …] inverted index. Cached; invalidated by reload_cluster_map."""
    global _cluster_members_cache
    if _cluster_members_cache is None:
        ctc = get_chunk_to_cluster(settings)
        inv: dict[str, list[str]] = defaultdict(list)
        for chunk_id, cluster_id in ctc.items():
            inv[cluster_id].append(chunk_id)
        _cluster_members_cache = dict(inv)
    return _cluster_members_cache


def get_cluster_members(
    cluster_id: str,
    *,
    limit: int = 100,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Return cluster metadata + up to `limit` member chunk records.

    Identity members sorted by create_time_first desc; knowledge members by
    urgency_score desc.
    """
    settings = settings or get_settings()
    atlas = load_atlas(settings)
    if atlas is None:
        raise FileNotFoundError("atlas not built")

    cluster_meta = next(
        (c for c in atlas.get("clusters", []) if c.get("id") == cluster_id), None
    )
    if cluster_meta is None:
        raise KeyError(f"cluster not found: {cluster_id}")

    lane = cluster_meta.get("lane", "identity")
    members_idx = _cluster_members_index(settings)
    chunk_ids = members_idx.get(cluster_id, [])

    from .retrieval import get_identity_store, get_knowledge_store

    store = (
        get_identity_store(settings) if lane == "identity"
        else get_knowledge_store(settings)
    )
    by_id = {str(m.get("chunk_id", "")): m for m in store._metadata}  # noqa: SLF001

    raw_members = [by_id[cid] for cid in chunk_ids if cid in by_id]

    if lane == "identity":
        raw_members.sort(
            key=lambda m: m.get("create_time_first") or 0.0,
            reverse=True,
        )
    else:
        raw_members.sort(
            key=lambda m: m.get("urgency_score") or 0,
            reverse=True,
        )

    members = raw_members[:limit]
    return {
        "cluster": cluster_meta,
        "total_members": len(chunk_ids),
        "shown": len(members),
        "members": members,
    }
