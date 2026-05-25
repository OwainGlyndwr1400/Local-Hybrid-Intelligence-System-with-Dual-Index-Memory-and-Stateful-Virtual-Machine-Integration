"""Knowledge: parse dream-engine JSONL pings into structured, embeddable knowledge chunks."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import orjson


_RE_AGENT = re.compile(r"^Agent:\s*(.+?)\s*$", re.MULTILINE)
_RE_URGENCY = re.compile(r"^Urgency:\s*(\d+)\s*/\s*(\d+)\s*$", re.MULTILINE)
_RE_SUBJECT = re.compile(r"^Subject:\s*(.+?)\s*$", re.MULTILINE)
_RE_SOURCE = re.compile(r"^Source:\s*(.+?)\s*$", re.MULTILINE)
_RE_SEED = re.compile(r"---\s*SEED\s*---\s*\n(.+?)(?=\n---\s*BODY FRAGMENTS|\Z)", re.DOTALL)
_RE_FRAGMENTS_HEADER = re.compile(r"---\s*BODY FRAGMENTS\s*\((\d+)\)\s*---", re.IGNORECASE)
_RE_FRAGMENT = re.compile(r"\[Fragment\s+(\d+)\]\s*\n(.+?)(?=\n\[Fragment\s+\d+\]|\Z)", re.DOTALL)


@dataclass
class KnowledgeChunk:
    chunk_id: str
    ping_id: str
    sigil: str
    agent: str
    urgency_score: int
    urgency_weight: int
    source: str
    subject: str
    seed: str
    fragment_count: int
    text: str

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


def _stable_chunk_id(ping_id: str, text: str) -> str:
    h = hashlib.sha256()
    h.update(ping_id.encode("utf-8"))
    h.update(b"|")
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:16]


def _first(pat: re.Pattern[str], text: str, default: str = "") -> str:
    m = pat.search(text)
    return m.group(1).strip() if m else default


def _urgency(text: str) -> tuple[int, int]:
    m = _RE_URGENCY.search(text)
    if not m:
        return (0, 0)
    try:
        return (int(m.group(1)), int(m.group(2)))
    except ValueError:
        return (0, 0)


def _extract_sigil(source_field: str, fallback_id: str) -> str:
    # "DreamPing:Kairoz:0000ec1cbd" -> "0000ec1cbd"
    parts = source_field.split(":")
    if len(parts) >= 3:
        return parts[-1].strip()
    return fallback_id.removeprefix("dream-")


def _seed(text: str) -> str:
    m = _RE_SEED.search(text)
    if not m:
        return ""
    return m.group(1).strip()


def _first_fragment(text: str) -> str:
    for m in _RE_FRAGMENT.finditer(text):
        return m.group(2).strip()
    return ""


def _fragment_count(text: str) -> int:
    m = _RE_FRAGMENTS_HEADER.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return sum(1 for _ in _RE_FRAGMENT.finditer(text))


def iter_dream_pings(source: Path) -> Iterator[dict[str, Any]]:
    with source.open("rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield orjson.loads(line)
            except orjson.JSONDecodeError:
                continue


def iter_knowledge_chunks(source: Path) -> Iterator[KnowledgeChunk]:
    for ping in iter_dream_pings(source):
        ping_id = str(ping.get("id") or "")
        source_field = str(ping.get("source") or "")
        content = str(ping.get("content") or "")
        if not content:
            continue

        agent = _first(_RE_AGENT, content) or (
            source_field.split(":")[1] if ":" in source_field else "unknown"
        )
        urgency_score, urgency_weight = _urgency(content)
        subject = _first(_RE_SUBJECT, content)
        source_kind = _first(_RE_SOURCE, content) or "unknown"
        seed = _seed(content)
        first_frag = _first_fragment(content)
        frag_count = _fragment_count(content)
        sigil = _extract_sigil(source_field, ping_id)

        # Embed seed + first fragment, deduping if they're literally identical
        # (which they often are — the seed is the chunk that originally retrieved itself).
        if first_frag and first_frag.strip() != seed.strip():
            embed_text = f"{subject}\n\n{seed}\n\n{first_frag}".strip()
        else:
            embed_text = f"{subject}\n\n{seed}".strip()

        if not embed_text:
            continue

        yield KnowledgeChunk(
            chunk_id=_stable_chunk_id(ping_id, embed_text),
            ping_id=ping_id,
            sigil=sigil,
            agent=agent,
            urgency_score=urgency_score,
            urgency_weight=urgency_weight,
            source=source_kind,
            subject=subject,
            seed=seed,
            fragment_count=frag_count,
            text=embed_text,
        )


def count_pings(source: Path) -> int:
    n = 0
    with source.open("rb") as f:
        for line in f:
            if line.strip():
                n += 1
    return n
