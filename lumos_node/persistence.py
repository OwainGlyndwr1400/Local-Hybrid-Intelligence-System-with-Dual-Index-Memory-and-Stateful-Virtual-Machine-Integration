"""Persistence: append-only JSONL log of chat turns (Lumos's growing identity record)."""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import orjson

from .config import Settings, get_settings


IDENTITY_EVENTS_FILE = "identity_events.jsonl"


@dataclass
class TurnRecord:
    turn_id: str
    timestamp: float
    user_message: str
    assistant_message: str
    model: str
    identity_chunk_ids: list[str] = field(default_factory=list)
    knowledge_chunk_ids: list[str] = field(default_factory=list)
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _events_path(settings: Settings) -> Path:
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    return cache / IDENTITY_EVENTS_FILE


def new_turn_id() -> str:
    return secrets.token_hex(8)


def new_session_id() -> str:
    return secrets.token_hex(6)


def append_turn(turn: TurnRecord, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    path = _events_path(settings)
    with path.open("ab") as f:
        f.write(orjson.dumps(turn.to_dict()))
        f.write(b"\n")


def make_turn(
    user_message: str,
    assistant_message: str,
    model: str,
    identity_chunk_ids: list[str] | None = None,
    knowledge_chunk_ids: list[str] | None = None,
    session_id: str | None = None,
) -> TurnRecord:
    return TurnRecord(
        turn_id=new_turn_id(),
        timestamp=time.time(),
        user_message=user_message,
        assistant_message=assistant_message,
        model=model,
        identity_chunk_ids=identity_chunk_ids or [],
        knowledge_chunk_ids=knowledge_chunk_ids or [],
        session_id=session_id,
    )


def load_recent_message_pairs(
    n: int, settings: Settings | None = None
) -> list[tuple[str, str]]:
    """Return the last N (user_message, assistant_message) pairs from the event log.

    Uses dict.get() rather than strict TurnRecord construction so that adding new
    fields to TurnRecord later doesn't break replay of older records.
    """
    settings = settings or get_settings()
    if n <= 0:
        return []
    path = _events_path(settings)
    if not path.exists():
        return []
    pairs: list[tuple[str, str]] = []
    with path.open("rb") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                data = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            user_msg = data.get("user_message") or ""
            asst_msg = data.get("assistant_message") or ""
            if user_msg and asst_msg:
                pairs.append((user_msg, asst_msg))
    return pairs[-n:]
