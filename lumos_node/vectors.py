"""Shared vector-store layer: FAISS IndexFlatIP with sidecar JSONL metadata + manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import faiss
import numpy as np
import orjson


@dataclass
class Manifest:
    source_path: str
    source_size: int
    source_mtime: float
    chunk_count: int
    embedding_model: str
    embedding_dim: int
    built_at: str

    def to_json(self) -> bytes:
        return orjson.dumps(self.__dict__, option=orjson.OPT_INDENT_2)

    @classmethod
    def from_path(cls, path: Path) -> Manifest | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        except (json.JSONDecodeError, TypeError):
            return None


class VectorStore:
    """Wraps a FAISS IndexFlatIP plus row-aligned JSONL metadata."""

    def __init__(self, dim: int):
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim)
        self._metadata: list[dict[str, Any]] = []

    @staticmethod
    def _normalize(vectors: np.ndarray) -> np.ndarray:
        v = vectors.astype(np.float32, copy=False)
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return v / norms

    def add(self, vectors: list[list[float]], metadatas: list[dict[str, Any]]) -> None:
        if not vectors:
            return
        if len(vectors) != len(metadatas):
            raise ValueError(
                f"vectors ({len(vectors)}) and metadatas ({len(metadatas)}) must match"
            )
        arr = self._normalize(np.asarray(vectors, dtype=np.float32))
        self.index.add(arr)
        self._metadata.extend(metadatas)

    def search(
        self, query: list[float] | np.ndarray, top_k: int = 6
    ) -> list[tuple[float, dict[str, Any]]]:
        if self.index.ntotal == 0:
            return []
        q = np.asarray([query], dtype=np.float32)
        q = self._normalize(q)
        scores, indices = self.index.search(q, top_k)
        out: list[tuple[float, dict[str, Any]]] = []
        for score, idx in zip(scores[0], indices[0], strict=True):
            if idx < 0 or idx >= len(self._metadata):
                continue
            out.append((float(score), self._metadata[idx]))
        return out

    @property
    def size(self) -> int:
        return self.index.ntotal

    def save(self, index_path: Path, metadata_path: Path) -> None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(index_path))
        with metadata_path.open("wb") as f:
            for m in self._metadata:
                f.write(orjson.dumps(m))
                f.write(b"\n")

    @classmethod
    def load(cls, index_path: Path, metadata_path: Path) -> VectorStore:
        index = faiss.read_index(str(index_path))
        store = cls.__new__(cls)
        store.index = index
        store.dim = index.d
        store._metadata = []
        with metadata_path.open("rb") as f:
            for line in f:
                if line.strip():
                    store._metadata.append(orjson.loads(line))

        meta_count = len(store._metadata)
        faiss_count = int(index.ntotal)
        if meta_count != faiss_count:
            # Tolerate mismatch — log loudly but let the engine boot. Search
            # already guards `idx >= len(self._metadata)` (see search() below).
            # Mismatch typically arises from interrupted ingest/dream cycles.
            # Run `lumos repair` (or rebuild the index) when convenient.
            import logging
            logging.warning(
                "vectorstore.count_mismatch",
                extra={
                    "metadata_count": meta_count,
                    "faiss_count": faiss_count,
                    "delta": faiss_count - meta_count,
                    "metadata_path": str(metadata_path),
                    "index_path": str(index_path),
                },
            )
        return store
