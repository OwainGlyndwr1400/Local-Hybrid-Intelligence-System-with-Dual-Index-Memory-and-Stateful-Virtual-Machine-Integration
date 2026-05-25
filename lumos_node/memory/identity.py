"""Identity memory: stream-parse ChatGPT conversations.json into chunked, embeddable units."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ijson


CHUNK_TARGET_CHARS = 2000
CHUNK_OVERLAP_CHARS = 200


@dataclass
class IdentityMessage:
    node_id: str
    role: str
    text: str
    create_time: float | None


@dataclass
class IdentityChunk:
    chunk_id: str
    conversation_id: str
    conversation_title: str
    create_time_first: float | None
    create_time_last: float | None
    roles: list[str]
    node_ids: list[str]
    text: str

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


def _stable_chunk_id(conversation_id: str, node_ids: list[str], text: str) -> str:
    h = hashlib.sha256()
    h.update(conversation_id.encode("utf-8"))
    h.update(b"|")
    h.update(",".join(node_ids).encode("utf-8"))
    h.update(b"|")
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:16]


def _extract_text(content: dict[str, Any]) -> str | None:
    ct = content.get("content_type")
    if ct == "text":
        parts = content.get("parts") or []
        text = "\n".join(p for p in parts if isinstance(p, str) and p)
        return text or None
    if ct == "code":
        parts = content.get("parts") or []
        code = "\n".join(p for p in parts if isinstance(p, str) and p)
        lang = content.get("language") or ""
        return f"```{lang}\n{code}\n```" if code else None
    if ct == "multimodal_text":
        parts = content.get("parts") or []
        text = "\n".join(p for p in parts if isinstance(p, str) and p)
        return text or None
    if ct == "user_editable_context":
        profile = (content.get("user_profile") or "").strip()
        instructions = (content.get("user_instructions") or "").strip()
        bits = []
        if profile:
            bits.append(f"[user profile]\n{profile}")
        if instructions:
            bits.append(f"[user instructions]\n{instructions}")
        return "\n\n".join(bits) or None
    return None


def _canonical_path(mapping: dict[str, dict[str, Any]], current_node: str | None) -> list[str]:
    if not mapping:
        return []
    roots = [nid for nid, n in mapping.items() if n.get("parent") is None]
    if not roots:
        return []
    root = roots[0]

    if current_node and current_node in mapping:
        path: list[str] = []
        node: str | None = current_node
        seen: set[str] = set()
        while node and node not in seen:
            seen.add(node)
            path.append(node)
            node = mapping.get(node, {}).get("parent")
        return list(reversed(path))

    # No current_node: descend deepest subtree.
    @_memoized_depth(mapping)
    def depth(node_id: str) -> int:
        children = mapping.get(node_id, {}).get("children") or []
        if not children:
            return 0
        children_in = [c for c in children if c in mapping]
        if not children_in:
            return 0
        return 1 + max(depth(c) for c in children_in)

    path = [root]
    node = root
    while True:
        children = mapping.get(node, {}).get("children") or []
        children_in = [c for c in children if c in mapping]
        if not children_in:
            break
        node = max(children_in, key=depth)
        path.append(node)
    return path


def _memoized_depth(mapping: dict[str, dict[str, Any]]):
    """Decorator factory that memoizes depth() against this specific mapping."""
    cache: dict[str, int] = {}

    def decorator(fn):
        def wrapped(node_id: str) -> int:
            if node_id in cache:
                return cache[node_id]
            v = fn(node_id)
            cache[node_id] = v
            return v
        return wrapped

    return decorator


def _conversation_messages(conv: dict[str, Any]) -> list[IdentityMessage]:
    mapping = conv.get("mapping") or {}
    current = conv.get("current_node")
    path = _canonical_path(mapping, current)
    out: list[IdentityMessage] = []
    for nid in path:
        node = mapping.get(nid) or {}
        msg = node.get("message")
        if not msg:
            continue
        meta = msg.get("metadata") or {}
        if meta.get("is_visually_hidden_from_conversation"):
            continue
        content = msg.get("content") or {}
        text = _extract_text(content)
        if not text:
            continue
        author = msg.get("author") or {}
        role = author.get("role") or "unknown"
        out.append(
            IdentityMessage(
                node_id=nid,
                role=role,
                text=text,
                create_time=msg.get("create_time"),
            )
        )
    return out


def _render_message_block(m: IdentityMessage) -> str:
    role = m.role.upper()
    if m.create_time:
        ts = datetime.fromtimestamp(m.create_time, tz=timezone.utc).isoformat(timespec="seconds")
        header = f"[{role} · {ts}]"
    else:
        header = f"[{role}]"
    return f"{header}\n{m.text}"


def _chunk_messages(messages: list[IdentityMessage]) -> Iterator[tuple[list[IdentityMessage], str]]:
    """Yield (window_of_messages, rendered_text) tuples respecting CHUNK_TARGET_CHARS."""
    if not messages:
        return
    window: list[IdentityMessage] = []
    rendered_parts: list[str] = []
    current_len = 0

    def flush() -> tuple[list[IdentityMessage], str]:
        return (list(window), "\n\n".join(rendered_parts))

    for m in messages:
        block = _render_message_block(m)
        # If a single message overflows target, split it into hard slices.
        if len(block) > CHUNK_TARGET_CHARS and not window:
            for start in range(0, len(block), CHUNK_TARGET_CHARS - CHUNK_OVERLAP_CHARS):
                slice_text = block[start : start + CHUNK_TARGET_CHARS]
                yield ([m], slice_text)
            continue

        block_len = len(block) + (2 if rendered_parts else 0)  # account for separator
        if current_len + block_len > CHUNK_TARGET_CHARS and window:
            yield flush()
            # Build overlap: keep tail messages whose combined length fits in overlap budget.
            overlap_msgs: list[IdentityMessage] = []
            overlap_parts: list[str] = []
            overlap_len = 0
            for prev in reversed(window):
                prev_block = _render_message_block(prev)
                if overlap_len + len(prev_block) > CHUNK_OVERLAP_CHARS:
                    break
                overlap_msgs.insert(0, prev)
                overlap_parts.insert(0, prev_block)
                overlap_len += len(prev_block) + 2
            window = overlap_msgs
            rendered_parts = overlap_parts
            current_len = sum(len(p) + 2 for p in rendered_parts)

        window.append(m)
        rendered_parts.append(block)
        current_len += block_len

    if window:
        yield flush()


def iter_conversations(source: Path) -> Iterator[dict[str, Any]]:
    with source.open("rb") as f:
        for conv in ijson.items(f, "item", use_float=True):
            yield conv


def iter_identity_chunks(source: Path) -> Iterator[IdentityChunk]:
    for conv in iter_conversations(source):
        conv_id = str(conv.get("conversation_id") or conv.get("id") or conv.get("title") or "")
        title = str(conv.get("title") or "")
        messages = _conversation_messages(conv)
        if not messages:
            continue
        for window, text in _chunk_messages(messages):
            text = text.strip()
            if not text:
                continue
            yield IdentityChunk(
                chunk_id=_stable_chunk_id(conv_id, [m.node_id for m in window], text),
                conversation_id=conv_id,
                conversation_title=title,
                create_time_first=window[0].create_time,
                create_time_last=window[-1].create_time,
                roles=[m.role for m in window],
                node_ids=[m.node_id for m in window],
                text=text,
            )


def count_conversations(source: Path) -> int:
    n = 0
    with source.open("rb") as f:
        for _ in ijson.items(f, "item", use_float=True):
            n += 1
    return n
