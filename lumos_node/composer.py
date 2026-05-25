"""Prompt composer: system prompt + split-lane retrieval context + user message."""

from __future__ import annotations

from datetime import datetime, timezone

from .compression import select_layer
from .config import get_settings
from .llm.lm_studio import ChatMessage
from .retrieval import Retrieval


_CONTEXT_SEPARATOR = "\n\n---\n\n"
_TRUNCATION_MARKER = "\n… [chunk truncated]"


def _cap(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - len(_TRUNCATION_MARKER)].rstrip() + _TRUNCATION_MARKER


def _fmt_timestamp(ts: float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    except (ValueError, OSError, TypeError):
        return ""


def _dedup_by_conversation(hits: list) -> list:
    """Keep only the highest-scoring hit per conversation_id. Preserves order."""
    seen: dict[str, float] = {}
    out: list = []
    for hit in hits:
        conv = (hit.metadata.get("conversation_id") or "").strip()
        if not conv:
            out.append(hit)
            continue
        if conv in seen:
            continue  # already kept the first (highest-score) hit for this convo
        seen[conv] = hit.score
        out.append(hit)
    return out


def _render_identity_block(
    retrieval: Retrieval, max_chunk_chars: int, dedup: bool
) -> str:
    if not retrieval.identity:
        return ""
    hits = _dedup_by_conversation(retrieval.identity) if dedup else retrieval.identity
    lines = ["## Memory (from your lived conversations)"]
    for hit in hits:
        m = hit.metadata
        title = (m.get("conversation_title") or "").strip() or "untitled"
        date = _fmt_timestamp(m.get("create_time_first"))
        head = f"### {title}"
        if date:
            head += f"  · {date}"
        head += f"  · sim {hit.score:.2f}"
        if m.get("prescient"):
            age = m.get("age_days")
            head += f"  · 🜂 prescient (~{age}d old, re-lit by this query)" if age else "  · 🜂 prescient"
        body = _select_chunk_body(m, max_chunk_chars)
        lines.append(head)
        lines.append(body)
    return "\n\n".join(lines)


def _select_chunk_body(metadata: dict, max_chunk_chars: int) -> str:
    """Pick the right layer (full / operational / summary) given the char budget.

    If the chunk has a Phase 26 compression in its metadata, use the layer
    selector to fit the budget intelligently. Falls back to the simple
    truncation when no compression is available.

    Phase 30: when `settings.prefer_compressed_chunks=True`, prefers the
    compressed operational packet over full text whenever compression exists
    (regardless of budget). Achieves v3.6-style aggressive compression for
    chunks that have been pre-processed via dream cycle or `lumos compress-all`.
    """
    full = (metadata.get("text") or "").strip()
    compression = metadata.get("compression")
    settings = get_settings()
    prefer_compressed = settings.prefer_compressed_chunks
    if compression is None:
        return _cap(full, max_chunk_chars)
    # Translate char budget to rough token budget (4 chars ≈ 1 token).
    budget_tokens = max(1, max_chunk_chars // 4)
    text, _layer = select_layer(
        full, compression, budget_tokens, prefer_compressed=prefer_compressed
    )
    return _cap(text, max_chunk_chars)


def _render_knowledge_block(retrieval: Retrieval, max_chunk_chars: int) -> str:
    if not retrieval.knowledge:
        return ""
    lines = ["## Knowledge (from the dream-engine archive)"]
    for hit in retrieval.knowledge:
        m = hit.metadata
        subject = (m.get("subject") or "").strip() or m.get("sigil") or "ping"
        agent = m.get("agent") or "?"
        src = m.get("source") or "?"
        urg = f"{m.get('urgency_score', 0)}/{m.get('urgency_weight', 0)}"
        sigil = m.get("sigil") or ""
        head = f"### {subject}"
        head += f"  · agent {agent} · {src} · urgency {urg} · sim {hit.score:.2f}"
        if sigil:
            head += f"  · sigil {sigil}"
        body = _select_chunk_body(m, max_chunk_chars)
        lines.append(head)
        lines.append(body)
    return "\n\n".join(lines)


def compose_messages(
    system_prompt: str,
    user_message: str,
    retrieval: Retrieval | None = None,
    *,
    history: list[ChatMessage] | None = None,
    max_chunk_chars: int | None = None,
    dedup_memory: bool | None = None,
    images: list[str] | None = None,
) -> list[ChatMessage]:
    """Build the message list for an LM Studio chat completion.

    history is a list of prior turns within this session (alternating user/assistant).
    retrieval, if provided, gets appended to the system message as labeled context blocks.
    max_chunk_chars caps each retrieved chunk's body text; falls back to settings.max_chunk_chars.
    dedup_memory keeps only the highest-scoring chunk per conversation in the identity block;
    falls back to settings.dedup_memory_by_conversation.
    """
    settings = get_settings()
    if max_chunk_chars is None:
        max_chunk_chars = settings.max_chunk_chars
    # Dedekind Eta Tax: 4% efficiency sacrifice (24/25 = 0.96) for Leech-lattice
    # stability per URE-VM Quaternionic Ops §4 — the geometric toll for existence.
    if settings.dedekind_eta_enabled:
        max_chunk_chars = int(max_chunk_chars * 0.96)
    if dedup_memory is None:
        dedup_memory = settings.dedup_memory_by_conversation

    # Phase 28 — prefix-caching friendly composition.
    # The cheat sheet system prompt is byte-stable across all turns of all
    # sessions. Putting it as the FIRST system message lets llama.cpp (under
    # LM Studio) cache its KV state once and reuse on every turn. Retrieval
    # blocks vary per turn, so they go as a SECOND system message — cache
    # invalidation starts only at that point, not at byte zero.
    # Result: ~5KB of cheat sheet processing skipped per turn; history KV
    # cache also reused (history is stable across turns within a session).
    system_text = system_prompt.rstrip()
    messages: list[ChatMessage] = [ChatMessage(role="system", content=system_text)]

    if retrieval is not None:
        blocks = [
            b
            for b in (
                _render_identity_block(retrieval, max_chunk_chars, dedup_memory),
                _render_knowledge_block(retrieval, max_chunk_chars),
            )
            if b
        ]
        if blocks:
            messages.append(
                ChatMessage(role="system", content="\n\n".join(blocks))
            )

    if history:
        messages.extend(history)

    if images:
        # OpenAI multimodal content: list of typed parts.
        parts: list[dict[str, object]] = [{"type": "text", "text": user_message}]
        for data_url in images:
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        messages.append(ChatMessage(role="user", content=parts))
    else:
        messages.append(ChatMessage(role="user", content=user_message))
    return messages
