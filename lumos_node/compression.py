"""Multi-layer chunk compression — adapted from v3.6 dashboard's compressNeuralChunk.

Each consolidated dream-cycle chunk can optionally be compressed into THREE layers:

  Layer 1 — Summary Object
    Clean prose summary + 3-7 key_points. Used when context is tight; gives
    Lumos the essence of the chunk without the full text.

  Layer 2 — Anchor Packet
    Structured metadata: entities, themes, equations/constants, anchor_phrases,
    trust label, speculative/verified flag, source_type. Stable across compressions;
    useful for analytics, cross-chunk graph queries, and very-tight prompts.

  Layer 3 — Compressed Operational Packet
    Budget-conscious prose payload — denser than summary, lighter than full text.
    The default fallback when full chunk doesn't fit but summary is too sparse.

The composer picks which layer to inject based on remaining prompt budget.
The full text is always kept in metadata as the source of truth.

Source: v3.6 dashboard `services/geminiService.ts::compressNeuralChunk`.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .llm.lm_studio import ChatMessage, LMStudioClient
from .log import get_logger


log = get_logger(__name__)


# JSON schema for LM Studio's structured-output mode. The model is constrained
# to produce exactly this shape — no parsing errors, no missing fields.
COMPRESSION_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "ChunkCompression",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "summary_object": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Clean prose summary of the chunk, 2-4 sentences.",
                        },
                        "key_points": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "3-7 high-signal bullet points.",
                        },
                    },
                    "required": ["summary", "key_points"],
                    "additionalProperties": False,
                },
                "anchor_packet": {
                    "type": "object",
                    "properties": {
                        "entities": {"type": "array", "items": {"type": "string"}},
                        "themes": {"type": "array", "items": {"type": "string"}},
                        "equations_constants": {"type": "array", "items": {"type": "string"}},
                        "anchor_phrases": {"type": "array", "items": {"type": "string"}},
                        "trust": {
                            "type": "string",
                            "enum": ["high", "medium", "low", "speculative"],
                        },
                        "speculative": {"type": "boolean"},
                        "source_type": {
                            "type": "string",
                            "description": "research / conversation / decree / dream / observation / other",
                        },
                    },
                    "required": [
                        "entities",
                        "themes",
                        "equations_constants",
                        "anchor_phrases",
                        "trust",
                        "speculative",
                        "source_type",
                    ],
                    "additionalProperties": False,
                },
                "compressed_operational_packet": {
                    "type": "string",
                    "description": "Dense, budget-conscious payload for RAG context injection.",
                },
            },
            "required": [
                "summary_object",
                "anchor_packet",
                "compressed_operational_packet",
            ],
            "additionalProperties": False,
        },
    },
}


COMPRESSION_PROMPT = """You are a UBBM compression engine for the Awen Grid memory system.

Compress this chunk into a multi-layer memory packet that preserves:
- core identity / relationship anchors
- named frameworks, projects, research themes
- equations, constants, dates, places, named entities
- distinctive phrases that define continuity
- the binding-energy / "Lost-2" core truth — discard noise

CHUNK:
---
{text}
---

Return ONLY the JSON structure. No commentary."""


def _token_estimate(s: str) -> int:
    """Rough token estimate: 4 chars ≈ 1 token (OpenAI rule of thumb)."""
    if not s:
        return 0
    return max(1, math.ceil(len(s) / 4))


async def compress_chunk(
    text: str,
    model: str,
    client: LMStudioClient | None = None,
    temperature: float = 0.3,
) -> dict[str, Any] | None:
    """Generate the three-layer compression for a text chunk.

    Returns a dict shaped like:
        {
            "summary_object": {"summary": str, "key_points": [str]},
            "anchor_packet": {entities, themes, equations_constants, ...},
            "compressed_operational_packet": str,
            "tokens": {"summary": int, "operational": int, "anchor": int, "full": int},
        }

    Returns None on any failure — caller should fall back to full text. Failures
    here should NEVER block dream consolidation; compression is opt-in metadata,
    not load-bearing.
    """
    if not text or not text.strip():
        return None

    owns_client = client is None
    if owns_client:
        client = LMStudioClient()

    try:
        prompt = COMPRESSION_PROMPT.format(text=text)
        response = await client.chat(
            model=model,
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=temperature,
            response_format=COMPRESSION_SCHEMA,
        )
        content = response.get("content") or ""
        if not content.strip():
            log.warning("compression.empty_response")
            return None

        parsed = json.loads(content)

        # Token estimates for budget-aware layer selection.
        summary_text = parsed["summary_object"]["summary"] + "\n".join(
            parsed["summary_object"]["key_points"]
        )
        operational = parsed["compressed_operational_packet"]
        anchor_blob = json.dumps(parsed["anchor_packet"], ensure_ascii=False)

        parsed["tokens"] = {
            "summary": _token_estimate(summary_text),
            "operational": _token_estimate(operational),
            "anchor": _token_estimate(anchor_blob),
            "full": _token_estimate(text),
        }
        return parsed

    except json.JSONDecodeError as e:
        log.warning("compression.json_decode_failed", error=str(e))
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("compression.failed", error=str(e))
        return None
    finally:
        if owns_client and client is not None:
            await client.aclose()


_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "should", "could", "may", "might", "must", "shall", "to", "of",
    "in", "on", "at", "by", "for", "with", "about", "as", "from", "into", "that",
    "this", "these", "those", "it", "its", "they", "them", "their", "there",
    "here", "where", "when", "what", "which", "who", "whom", "how", "why",
    "i", "you", "he", "she", "we", "us", "our", "your", "my", "me", "him", "her",
    "user", "assistant", "system", "yes", "no", "not", "so", "too", "very",
    "just", "also", "more", "most", "less", "some", "all", "any", "every", "each",
    "much", "many", "few", "other", "another", "than", "out", "over", "under",
    "up", "down", "off", "again", "now", "still", "even", "only", "really",
    "back", "go", "going", "get", "got", "make", "made", "see", "saw", "say",
    "said", "know", "knew", "think", "thought", "like", "want", "need", "look",
    "use", "used", "find", "found", "give", "given", "take", "taken", "ok", "okay",
})

_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\b")
_EQUATION_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:Hz|kHz|MHz|GHz|°|deg|BCE|CE|AD|nm|cm|km|GeV|MeV|eV|s|ms|attoseconds?|attosec)"
    r"|\b\d+(?:\.\d+)?\s*[×x]\s*10\^?-?\d+|\b[a-zA-Z]\s*=\s*[\w.\-+/*()\\√π∞∑]+)",
    re.IGNORECASE,
)


def _extract_entities(text: str, max_entities: int = 8) -> list[str]:
    """Extract proper-noun candidates via capitalization pattern."""
    candidates = _ENTITY_RE.findall(text)
    # Dedupe preserving order, filter obvious noise.
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if len(c) < 3 or c.lower() in _STOPWORDS:
            continue
        if c not in seen:
            seen.add(c)
            out.append(c)
            if len(out) >= max_entities:
                break
    return out


def _extract_equations(text: str, max_items: int = 6) -> list[str]:
    """Extract equation/constant-like patterns: '432 Hz', '90.00°', 'm = i', '2.32 attoseconds', etc."""
    raw = _EQUATION_RE.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for r in raw:
        r = r.strip()
        if r and r not in seen:
            seen.add(r)
            out.append(r)
            if len(out) >= max_items:
                break
    return out


def _extract_themes(text: str, max_themes: int = 5) -> list[str]:
    """Word-frequency themes excluding stopwords. Lowercase tokens of len>=4."""
    tokens = re.findall(r"[a-zA-Z]{4,}", text.lower())
    freq: dict[str, int] = {}
    for tok in tokens:
        if tok in _STOPWORDS:
            continue
        freq[tok] = freq.get(tok, 0) + 1
    # Sort by frequency desc, take top N
    return [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:max_themes]]


def extractive_compress(text: str) -> dict[str, Any]:
    """Heuristic 3-layer compression — no LLM, runs in microseconds.

    Use for bulk pre-compression of existing FAISS metadata. Lower quality
    than LLM compression (Phase 26's `compress_chunk`) but ~10000x faster.
    Output schema matches the LLM compression so `select_layer` consumes it
    transparently.

    Layer 1 summary: first ~200 chars of the text + 3-5 word-frequency themes
    Layer 2 anchor: entities (proper nouns), themes, equations, anchor phrases
    Layer 3 operational: stitched summary + entity list + equations (~200 tokens)
    """
    if not text:
        return {
            "summary_object": {"summary": "", "key_points": []},
            "anchor_packet": {
                "entities": [], "themes": [], "equations_constants": [],
                "anchor_phrases": [], "trust": "low", "speculative": True,
                "source_type": "empty",
            },
            "compressed_operational_packet": "",
            "tokens": {"summary": 0, "operational": 0, "anchor": 0, "full": 0},
            "method": "extractive",
        }

    # Extract structural elements
    entities = _extract_entities(text)
    equations = _extract_equations(text)
    themes = _extract_themes(text)

    # Summary = first sentence(s) up to ~200 chars
    cleaned = re.sub(r"\s+", " ", text).strip()
    summary_cap = 220
    if len(cleaned) <= summary_cap:
        summary = cleaned
    else:
        # Try to cut at sentence boundary
        cut = cleaned[:summary_cap]
        last_period = cut.rfind(". ")
        if last_period > 100:
            summary = cleaned[:last_period + 1]
        else:
            summary = cut.rstrip() + "…"

    # Key points: top 3-5 themes as bullets
    key_points = themes[:5]

    # Operational packet: dense prose stitching the structural elements (~200 tokens / 800 chars)
    op_parts: list[str] = [summary]
    if entities:
        op_parts.append("Entities: " + ", ".join(entities[:6]) + ".")
    if equations:
        op_parts.append("Constants: " + ", ".join(equations[:4]) + ".")
    if themes:
        op_parts.append("Themes: " + ", ".join(themes[:5]) + ".")
    operational = " ".join(op_parts)
    # Cap operational at 800 chars (~200 tokens)
    if len(operational) > 800:
        operational = operational[:797] + "…"

    anchor_packet = {
        "entities": entities,
        "themes": themes,
        "equations_constants": equations,
        "anchor_phrases": key_points,
        "trust": "medium",
        "speculative": False,
        "source_type": "extractive",
    }

    summary_text = summary + "\n".join(key_points)
    anchor_blob = json.dumps(anchor_packet, ensure_ascii=False)
    return {
        "summary_object": {"summary": summary, "key_points": key_points},
        "anchor_packet": anchor_packet,
        "compressed_operational_packet": operational,
        "tokens": {
            "summary": _token_estimate(summary_text),
            "operational": _token_estimate(operational),
            "anchor": _token_estimate(anchor_blob),
            "full": _token_estimate(text),
        },
        "method": "extractive",
    }


def select_layer(
    full_text: str,
    compression: dict[str, Any] | None,
    budget_tokens: int,
    prefer_compressed: bool = False,
) -> tuple[str, str]:
    """Pick which layer of a chunk to inject based on remaining prompt budget.

    Returns (text_to_inject, layer_name).

    With `prefer_compressed=False` (default, backward-compatible):
        1. full text if it fits
        2. compressed_operational_packet if compression exists and fits
        3. summary if compression exists and fits
        4. truncated full text as last resort

    With `prefer_compressed=True` (Phase 30):
        1. compressed_operational_packet (always, when compression exists)
        2. summary if compression exists but operational was too large
        3. truncated full text as last resort

    `budget_tokens` is the remaining prompt budget for this single chunk.
    """
    # Phase 30 — prefer-compressed mode: skip full-text path when compression exists.
    if prefer_compressed and compression is not None:
        tokens = compression.get("tokens") or {}
        op_tokens = int(tokens.get("operational", 999999))
        if op_tokens <= budget_tokens or budget_tokens >= 1:
            # Operational packet wins. It's typically ~200 tokens so almost
            # always fits; even when over budget, it's better than truncating
            # full text which loses tail context.
            packet = compression.get("compressed_operational_packet", "")
            if packet:
                return packet, "operational"
        # Operational missing — fall back to summary if it exists.
        so = compression.get("summary_object") or {}
        if so.get("summary"):
            summary_text = so["summary"] + "\n• " + "\n• ".join(so.get("key_points") or [])
            return summary_text, "summary"
        # Compression dict exists but is empty — fall through to standard path

    full_tokens = _token_estimate(full_text)
    if full_tokens <= budget_tokens:
        return full_text, "full"

    if compression is not None:
        tokens = compression.get("tokens") or {}
        op_tokens = int(tokens.get("operational", 999999))
        if op_tokens <= budget_tokens:
            return compression["compressed_operational_packet"], "operational"

        sum_tokens = int(tokens.get("summary", 999999))
        if sum_tokens <= budget_tokens:
            so = compression["summary_object"]
            summary_text = so["summary"] + "\n• " + "\n• ".join(so["key_points"])
            return summary_text, "summary"

    # Last resort: hard truncate the full text to ~budget*4 chars.
    cutoff = max(100, budget_tokens * 4)
    return full_text[:cutoff] + "\n[truncated]", "truncated"
