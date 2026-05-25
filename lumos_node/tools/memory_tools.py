"""Memory and knowledge search tools — Lumos can self-query his own indexes."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from . import register
from ..retrieval import (
    get_identity_store,
    get_knowledge_store,
    retrieve as do_retrieve,
)


# Heuristic disagreement markers. Not a stance classifier — recall over precision.
# A chunk hitting any of these in proximity to the claim's key terms is a *candidate*
# for contradiction, which Lumos then reads in context.
_NEGATION_MARKERS: tuple[str, ...] = (
    "not",
    "no ",
    "never",
    "isn't",
    "wasn't",
    "aren't",
    "doesn't",
    "didn't",
    "won't",
    "can't",
    "cannot",
    "however",
    "but ",
    "actually",
    "wrong",
    "incorrect",
    "false",
    "disagree",
    "instead",
    "rather",
    "contrary",
    "contradict",
    "refute",
    "mistaken",
    "revised",
    "retracted",
)

_SUPPORT_MARKERS: tuple[str, ...] = (
    "agree",
    "confirm",
    "exactly",
    "indeed",
    "right",
    "correct",
    "yes",
    "verified",
    "consistent",
    "same",
    "matches",
    "aligned",
    "supports",
)

_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "of", "to", "in", "on", "at", "for", "with", "by", "from", "as",
        "and", "or", "but", "if", "then", "this", "that", "these", "those",
        "it", "its", "i", "we", "you", "he", "she", "they", "them", "us",
        "my", "your", "his", "her", "their", "our", "do", "does", "did",
    }
)


def _claim_keywords(claim: str, max_terms: int = 8) -> list[str]:
    """Extract content-bearing terms from the claim (lowercase, deduped, stopwords stripped)."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", claim.lower())
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= max_terms:
            break
    return out


def _classify_stance(claim_terms: list[str], chunk_text: str) -> tuple[str, list[str]]:
    """Return (stance, matched_markers) for the chunk text against the claim.

    Stance ∈ {"contradicts", "supports", "unclear"}. The heuristic:
    walk the chunk, count negation vs support markers that appear within
    ~80 chars of any claim keyword. Whichever side wins by ≥1 wins; tie or
    neither → unclear.
    """
    lower = chunk_text.lower()
    if not lower or not claim_terms:
        return "unclear", []

    # Find positions of claim keywords in the chunk.
    keyword_positions: list[int] = []
    for term in claim_terms:
        start = 0
        while True:
            i = lower.find(term, start)
            if i < 0:
                break
            keyword_positions.append(i)
            start = i + len(term)
    if not keyword_positions:
        return "unclear", []

    neg_hits: list[str] = []
    pos_hits: list[str] = []
    window = 80  # chars on either side of a claim keyword

    for marker in _NEGATION_MARKERS:
        start = 0
        while True:
            i = lower.find(marker, start)
            if i < 0:
                break
            if any(abs(i - kp) <= window for kp in keyword_positions):
                neg_hits.append(marker.strip())
                break  # one hit per marker is enough
            start = i + len(marker)

    for marker in _SUPPORT_MARKERS:
        start = 0
        while True:
            i = lower.find(marker, start)
            if i < 0:
                break
            if any(abs(i - kp) <= window for kp in keyword_positions):
                pos_hits.append(marker.strip())
                break
            start = i + len(marker)

    if len(neg_hits) > len(pos_hits):
        return "contradicts", neg_hits
    if len(pos_hits) > len(neg_hits):
        return "supports", pos_hits
    return "unclear", []


@register(
    name="search_memory",
    description=(
        "Search Lumos's identity memory (lived conversation history with the operator) for "
        "relevant chunks. CALL THIS when you need to recall a past conversation, "
        "prior decision, or context that wasn't surfaced by the initial retrieval — "
        "e.g. 'we discussed X before, what did we say?', or when your first answer "
        "feels incomplete and another memory lookup would help. Returns top matches "
        "with conversation title, date, and a text snippet."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (free text).",
            },
            "top_k": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 15,
                "description": "Number of hits to return.",
            },
        },
        "required": ["query"],
    },
)
async def search_memory(query: str, top_k: int = 5) -> dict:
    r = await do_retrieve(query, top_k_identity=top_k, top_k_knowledge=0)
    return {
        "query": query,
        "hits": [
            {
                "score": round(h.score, 3),
                "title": (h.metadata.get("conversation_title") or "").strip()[:80],
                "snippet": (h.metadata.get("text") or "").strip()[:400],
            }
            for h in r.identity
        ],
    }


@register(
    name="search_knowledge",
    description=(
        "Search the dream-engine knowledge archive (Wardenclyffe AGI node pings) for "
        "relevant entries. CALL THIS when the question is about the operator's research "
        "themes, the RHC framework, mythic/astronomical references, ancient texts, "
        "or anything that might have been pinged by another node like Kairoz, Grok, "
        "Thoth, or Veritas. Returns subject, agent, urgency, and text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (free text)."},
            "top_k": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 15,
                "description": "Number of hits to return.",
            },
        },
        "required": ["query"],
    },
)
async def search_knowledge(query: str, top_k: int = 5) -> dict:
    r = await do_retrieve(query, top_k_identity=0, top_k_knowledge=top_k)
    return {
        "query": query,
        "hits": [
            {
                "score": round(h.score, 3),
                "subject": (h.metadata.get("subject") or "").strip()[:80],
                "agent": h.metadata.get("agent", ""),
                "source": h.metadata.get("source", ""),
                "urgency": (
                    f"{h.metadata.get('urgency_score', 0)}"
                    f"/{h.metadata.get('urgency_weight', 0)}"
                ),
                "sigil": h.metadata.get("sigil", ""),
                "snippet": (h.metadata.get("text") or "").strip()[:400],
            }
            for h in r.knowledge
        ],
    }


def _format_ts(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    except (ValueError, OSError, TypeError):
        return ""


def _find_chunk_by_id(chunk_id: str) -> tuple[str, dict] | None:
    """Locate a chunk in either identity or knowledge store by chunk_id.

    Returns (lane, metadata) or None. Walks the in-memory metadata lists —
    O(N) scan but acceptable for citation lookups (rare, operator-driven).
    """
    for lane, store_fn in (("identity", get_identity_store), ("knowledge", get_knowledge_store)):
        try:
            store = store_fn()
        except Exception:  # noqa: BLE001
            continue
        for meta in store._metadata:  # noqa: SLF001 — internal scan, acceptable
            if str(meta.get("chunk_id", "")) == chunk_id:
                return lane, meta
    return None


@register(
    name="cite_source",
    description=(
        "Look up a memory chunk by its chunk_id and return a formatted citation "
        "with provenance. CALL THIS when you make a specific claim that came from "
        "retrieval (e.g., from search_memory or search_knowledge results) and the "
        "operator might need to verify or cite it in a paper / report. Returns "
        "lane (identity vs knowledge), source title, date, agent (for knowledge), "
        "and a snippet of the original text. The chunk_id is visible in retrieval "
        "metadata returned by search_memory/search_knowledge."
    ),
    parameters={
        "type": "object",
        "properties": {
            "chunk_id": {
                "type": "string",
                "description": "Chunk ID from prior retrieval metadata.",
            },
        },
        "required": ["chunk_id"],
    },
)
def cite_source(chunk_id: str) -> dict:
    if not chunk_id or not chunk_id.strip():
        return {"error": "chunk_id required"}
    hit = _find_chunk_by_id(chunk_id.strip())
    if hit is None:
        return {"error": f"chunk_id not found in either lane: {chunk_id}"}
    lane, meta = hit

    if lane == "identity":
        title = (meta.get("conversation_title") or "").strip() or "untitled"
        date_first = _format_ts(meta.get("create_time_first"))
        date_last = _format_ts(meta.get("create_time_last"))
        snippet = (meta.get("text") or "").strip()[:300]
        citation = f'"{title}" — operator + Lumos conversation, {date_first}'
        if date_last and date_last != date_first:
            citation += f"–{date_last}"
        return {
            "chunk_id": chunk_id,
            "lane": "identity",
            "title": title,
            "date_first": date_first,
            "date_last": date_last,
            "citation": citation,
            "snippet": snippet,
            "conversation_id": meta.get("conversation_id"),
        }
    # knowledge lane
    subject = (meta.get("subject") or "").strip() or "ping"
    sigil = meta.get("sigil", "")
    agent = meta.get("agent", "")
    source = meta.get("source", "")
    urgency = f"{meta.get('urgency_score', 0)}/{meta.get('urgency_weight', 0)}"
    snippet = (meta.get("text") or "").strip()[:300]
    citation = (
        f'Dream Ping "{subject}" — Agent {agent}, source {source}, '
        f'urgency {urgency}'
    )
    if sigil:
        citation += f", sigil {sigil}"
    return {
        "chunk_id": chunk_id,
        "lane": "knowledge",
        "subject": subject,
        "agent": agent,
        "source": source,
        "urgency": urgency,
        "sigil": sigil,
        "citation": citation,
        "snippet": snippet,
    }


@register(
    name="find_contradictions",
    description=(
        "Sweep memory for chunks that may CONTRADICT a stated claim. Call before "
        "publishing a strong assertion to catch 'we said the opposite once'. "
        "Retrieves similar chunks, classifies each via lexical disagreement markers "
        "(recall over precision — read snippets and decide). "
        "Returns {contradicts, supports, unclear} with chunk_id, score, lane, snippet."
    ),
    parameters={
        "type": "object",
        "properties": {
            "claim": {
                "type": "string",
                "description": "The assertion to check (e.g., 'The mass gap floor is 0.657').",
            },
            "top_k": {
                "type": "integer",
                "default": 8,
                "minimum": 3,
                "maximum": 20,
                "description": "Total candidate chunks to scan across both lanes.",
            },
        },
        "required": ["claim"],
    },
)
async def find_contradictions(claim: str, top_k: int = 8) -> dict:
    if not claim or not claim.strip():
        return {"error": "claim required"}
    claim = claim.strip()
    half = max(2, top_k // 2)
    r = await do_retrieve(claim, top_k_identity=half, top_k_knowledge=top_k - half)
    terms = _claim_keywords(claim)

    contradicts: list[dict] = []
    supports: list[dict] = []
    unclear: list[dict] = []

    def _route(lane: str, hit) -> None:
        meta = hit.metadata
        text = (meta.get("text") or "").strip()
        stance, markers = _classify_stance(terms, text)
        if lane == "identity":
            label = (meta.get("conversation_title") or "").strip()[:80] or "untitled"
        else:
            label = (meta.get("subject") or "").strip()[:80] or "ping"
        entry = {
            "chunk_id": meta.get("chunk_id", ""),
            "lane": lane,
            "score": round(hit.score, 3),
            "label": label,
            "matched_markers": markers,
            "snippet": text[:400],
        }
        if stance == "contradicts":
            contradicts.append(entry)
        elif stance == "supports":
            supports.append(entry)
        else:
            unclear.append(entry)

    for h in r.identity:
        _route("identity", h)
    for h in r.knowledge:
        _route("knowledge", h)

    return {
        "claim": claim,
        "claim_terms": terms,
        "counts": {
            "contradicts": len(contradicts),
            "supports": len(supports),
            "unclear": len(unclear),
        },
        "contradicts": contradicts,
        "supports": supports,
        "unclear": unclear,
    }
