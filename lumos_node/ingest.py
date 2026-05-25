"""Ingest orchestrator: build identity + knowledge FAISS indexes from source files."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .config import Settings, get_settings
from .knowledge.dreams import (
    KnowledgeChunk,
    count_pings,
    iter_knowledge_chunks,
)
from .llm.lm_studio import LMStudioClient
from .log import get_logger
from .memory.identity import (
    IdentityChunk,
    count_conversations,
    iter_identity_chunks,
)
from .vectors import Manifest, VectorStore


log = get_logger(__name__)


IDENTITY_INDEX = "identity.faiss"
IDENTITY_META = "identity.jsonl"
IDENTITY_MANIFEST = "identity.manifest.json"

KNOWLEDGE_INDEX = "knowledge.faiss"
KNOWLEDGE_META = "knowledge.jsonl"
KNOWLEDGE_MANIFEST = "knowledge.manifest.json"


def _source_signature(path: Path) -> tuple[int, float]:
    st = path.stat()
    return (st.st_size, st.st_mtime)


def _manifest_is_fresh(manifest: Manifest | None, source: Path, settings: Settings) -> bool:
    if manifest is None:
        return False
    size, mtime = _source_signature(source)
    return (
        manifest.source_path == str(source)
        and manifest.source_size == size
        and abs(manifest.source_mtime - mtime) < 1.0
        and manifest.embedding_model == settings.lm_studio_embedding_model
        and manifest.embedding_dim == settings.embedding_dim
    )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _batched(items: Iterable[Any], n: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


async def _embed_batch(
    client: LMStudioClient,
    chunks: list[Any],
    model: str,
) -> tuple[list[list[float]], list[Any]]:
    if not chunks:
        return [], []
    texts = [c.text for c in chunks]
    vectors = await client.embed(texts, model=model)
    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"Embedding response count mismatch: requested {len(chunks)}, got {len(vectors)}"
        )
    return vectors, chunks


async def _run_concurrent_embed(
    client: LMStudioClient,
    store: VectorStore,
    chunk_iter: Iterator[Any],
    model: str,
    batch_size: int,
    concurrency: int,
    pbar: tqdm,
) -> None:
    """Pull batches from chunk_iter and keep `concurrency` embedding requests in flight."""
    batched = _batched(chunk_iter, batch_size)
    while True:
        group = list(itertools.islice(batched, concurrency))
        if not group:
            break
        results = await asyncio.gather(
            *[_embed_batch(client, batch, model) for batch in group]
        )
        for vectors, chunks in results:
            store.add(vectors, [c.to_metadata() for c in chunks])
            pbar.update(len(chunks))


async def build_identity(
    settings: Settings | None = None,
    *,
    rebuild: bool = False,
) -> dict[str, Any]:
    settings = settings or get_settings()
    source = settings.identity_source.expanduser()
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()
    if not source.exists():
        raise FileNotFoundError(f"identity_source not found: {source}")

    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    index_path = cache / IDENTITY_INDEX
    meta_path = cache / IDENTITY_META
    manifest_path = cache / IDENTITY_MANIFEST

    existing = Manifest.from_path(manifest_path)
    if not rebuild and _manifest_is_fresh(existing, source, settings) and index_path.exists():
        log.info("identity.skip", reason="fresh", chunks=existing.chunk_count)
        return {"skipped": True, "chunks": existing.chunk_count, "path": str(index_path)}

    log.info("identity.start", source=str(source))
    convo_total = count_conversations(source)
    log.info("identity.scan", conversations=convo_total)

    client = LMStudioClient()
    store = VectorStore(dim=settings.embedding_dim)
    try:
        chunk_iter: Iterator[IdentityChunk] = iter_identity_chunks(source)
        pbar = tqdm(
            total=None,
            desc="identity",
            unit="chunk",
            dynamic_ncols=True,
        )
        await _run_concurrent_embed(
            client=client,
            store=store,
            chunk_iter=chunk_iter,
            model=settings.lm_studio_embedding_model,
            batch_size=settings.embedding_batch_size,
            concurrency=settings.embedding_concurrency,
            pbar=pbar,
        )
        pbar.close()
    finally:
        await client.aclose()

    store.save(index_path, meta_path)
    size, mtime = _source_signature(source)
    manifest = Manifest(
        source_path=str(source),
        source_size=size,
        source_mtime=mtime,
        chunk_count=store.size,
        embedding_model=settings.lm_studio_embedding_model,
        embedding_dim=settings.embedding_dim,
        built_at=_now_iso(),
    )
    manifest_path.write_bytes(manifest.to_json())
    log.info("identity.done", chunks=store.size, conversations=convo_total)
    return {
        "skipped": False,
        "chunks": store.size,
        "conversations": convo_total,
        "path": str(index_path),
    }


async def build_knowledge(
    settings: Settings | None = None,
    *,
    rebuild: bool = False,
) -> dict[str, Any]:
    settings = settings or get_settings()
    source = settings.knowledge_source.expanduser()
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()
    if not source.exists():
        raise FileNotFoundError(f"knowledge_source not found: {source}")

    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    index_path = cache / KNOWLEDGE_INDEX
    meta_path = cache / KNOWLEDGE_META
    manifest_path = cache / KNOWLEDGE_MANIFEST

    existing = Manifest.from_path(manifest_path)
    if not rebuild and _manifest_is_fresh(existing, source, settings) and index_path.exists():
        log.info("knowledge.skip", reason="fresh", chunks=existing.chunk_count)
        return {"skipped": True, "chunks": existing.chunk_count, "path": str(index_path)}

    log.info("knowledge.start", source=str(source))
    ping_total = count_pings(source)
    log.info("knowledge.scan", pings=ping_total)

    client = LMStudioClient()
    store = VectorStore(dim=settings.embedding_dim)
    try:
        chunk_iter: Iterator[KnowledgeChunk] = iter_knowledge_chunks(source)
        pbar = tqdm(
            total=ping_total,
            desc="knowledge",
            unit="ping",
            dynamic_ncols=True,
        )
        await _run_concurrent_embed(
            client=client,
            store=store,
            chunk_iter=chunk_iter,
            model=settings.lm_studio_embedding_model,
            batch_size=settings.embedding_batch_size,
            concurrency=settings.embedding_concurrency,
            pbar=pbar,
        )
        pbar.close()
    finally:
        await client.aclose()

    store.save(index_path, meta_path)
    size, mtime = _source_signature(source)
    manifest = Manifest(
        source_path=str(source),
        source_size=size,
        source_mtime=mtime,
        chunk_count=store.size,
        embedding_model=settings.lm_studio_embedding_model,
        embedding_dim=settings.embedding_dim,
        built_at=_now_iso(),
    )
    manifest_path.write_bytes(manifest.to_json())
    log.info("knowledge.done", chunks=store.size, pings=ping_total)
    return {
        "skipped": False,
        "chunks": store.size,
        "pings": ping_total,
        "path": str(index_path),
    }


async def build_all(rebuild: bool = False) -> dict[str, Any]:
    return {
        "identity": await build_identity(rebuild=rebuild),
        "knowledge": await build_knowledge(rebuild=rebuild),
    }
