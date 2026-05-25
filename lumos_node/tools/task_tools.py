"""Task continuity tools — research projects that span sessions.

Each task is a markdown file in `data/lumos_tasks/` with YAML frontmatter:

    ---
    id: <slug>
    name: <human title>
    status: active | paused | done | blocked
    created: 2026-05-14T10:30:00Z
    updated: 2026-05-14T18:45:00Z
    next_action: <one-line operator-facing hint>
    ---

    # Notes
    Free-form markdown body. Lumos appends progress notes here as work
    accumulates. Operator can edit directly with any text editor.

This decouples research-task state from chat-session state. A paper draft,
a Voynich decode, a Sphinx alignment validation — each gets its own task file
that persists across days/weeks of conversations. Lumos can read the file at
the start of a related conversation to "remember where we left off."
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from . import register
from ..log import get_logger


log = get_logger(__name__)


_TASKS_SUBDIR = "lumos_tasks"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_VALID_STATUS = frozenset({"active", "paused", "done", "blocked"})


def _tasks_dir() -> Path:
    cwd = Path.cwd().resolve()
    p = (cwd / "data" / _TASKS_SUBDIR).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slugify(name: str, max_len: int = 48) -> str:
    """Make a filesystem-safe slug from a human-friendly task name."""
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower())
    s = s.strip("-")
    if not s:
        s = "task"
    return s[:max_len]


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a markdown file into (frontmatter_dict, body). Frontmatter is YAML-like
    but we use a simple key: value parser to avoid a YAML dependency."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_block = m.group(1)
    body = m.group(2)
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body


def _serialize_frontmatter(fm: dict[str, str]) -> str:
    """Round-trip a dict back to the YAML-ish frontmatter block."""
    ordered_keys = ["id", "name", "status", "created", "updated", "next_action"]
    extra = [k for k in fm.keys() if k not in ordered_keys]
    out_lines = ["---"]
    for k in ordered_keys + extra:
        if k in fm:
            out_lines.append(f"{k}: {fm[k]}")
    out_lines.append("---")
    return "\n".join(out_lines)


def _task_path(task_id: str) -> Path:
    return _tasks_dir() / f"{task_id}.md"


@register(
    name="create_task",
    description=(
        "Create a new persistent research task. Use this when starting a multi-day "
        "or multi-session project (e.g., 'draft paper on Sphinx-Regulus alignment', "
        "'decode the Voynich Rosette folio', 'compile RHC theorem cross-references'). "
        "Returns a task_id that can be used with update_task / get_task / list_tasks. "
        "Lives in data/lumos_tasks/<id>.md as plain markdown — operator can edit by hand."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Human-readable task title.",
            },
            "description": {
                "type": "string",
                "description": "Initial body / goal. Markdown OK.",
                "default": "",
            },
            "next_action": {
                "type": "string",
                "description": "One-line hint of the immediate next step.",
                "default": "",
            },
        },
        "required": ["name"],
    },
)
def create_task(name: str, description: str = "", next_action: str = "") -> dict:
    if not name or not name.strip():
        return {"error": "name is required"}
    task_id = _slugify(name)
    path = _task_path(task_id)
    if path.exists():
        # Append timestamp suffix to dedupe
        task_id = f"{task_id}-{int(datetime.now(timezone.utc).timestamp())}"
        path = _task_path(task_id)
    now = _now_iso()
    fm = {
        "id": task_id,
        "name": name.strip(),
        "status": "active",
        "created": now,
        "updated": now,
        "next_action": next_action.strip() or "(unset)",
    }
    body_parts = ["# Notes\n"]
    if description and description.strip():
        body_parts.append(description.strip())
        body_parts.append("")
    body_parts.append(f"## {now}")
    body_parts.append("Task created.")
    body = "\n".join(body_parts)
    content = _serialize_frontmatter(fm) + "\n\n" + body + "\n"
    path.write_text(content, encoding="utf-8")
    log.info("task.created", task_id=task_id, name=name)
    return {
        "task_id": task_id,
        "path": str(path),
        "status": "active",
        "created": now,
    }


@register(
    name="update_task",
    description=(
        "Append a progress note to an existing task. Use this when work happens "
        "on a long-running project — every meaningful step (draft section completed, "
        "blocker identified, new finding) should land as a timestamped note. "
        "Optionally update status (active/paused/done/blocked) and next_action."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "ID returned by create_task."},
            "note": {
                "type": "string",
                "description": "Progress note (markdown OK). Appended with current timestamp.",
            },
            "status": {
                "type": "string",
                "description": "Optional status change: active, paused, done, blocked.",
                "default": "",
            },
            "next_action": {
                "type": "string",
                "description": "Optional update to the next-action hint.",
                "default": "",
            },
        },
        "required": ["task_id", "note"],
    },
)
def update_task(
    task_id: str, note: str, status: str = "", next_action: str = ""
) -> dict:
    if not task_id or not task_id.strip():
        return {"error": "task_id required"}
    if not note or not note.strip():
        return {"error": "note required"}
    path = _task_path(task_id.strip())
    if not path.exists():
        return {"error": f"task not found: {task_id}"}
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    now = _now_iso()
    fm["updated"] = now
    if status and status.strip().lower() in _VALID_STATUS:
        fm["status"] = status.strip().lower()
    if next_action and next_action.strip():
        fm["next_action"] = next_action.strip()
    new_body = body.rstrip() + f"\n\n## {now}\n{note.strip()}\n"
    new_content = _serialize_frontmatter(fm) + "\n\n" + new_body
    path.write_text(new_content, encoding="utf-8")
    log.info("task.updated", task_id=task_id, status=fm.get("status"))
    return {
        "task_id": task_id,
        "status": fm.get("status"),
        "updated": now,
        "next_action": fm.get("next_action"),
    }


@register(
    name="get_task",
    description=(
        "Read the full content of a task file (frontmatter + all progress notes). "
        "Use this at the START of a conversation that's continuing a known multi-day "
        "project — gives you the full context of where the work left off."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "ID returned by create_task."},
        },
        "required": ["task_id"],
    },
)
def get_task(task_id: str) -> dict:
    if not task_id or not task_id.strip():
        return {"error": "task_id required"}
    path = _task_path(task_id.strip())
    if not path.exists():
        return {"error": f"task not found: {task_id}"}
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    return {
        "task_id": task_id,
        "frontmatter": fm,
        "body": body[:8000],  # cap to keep tool result manageable
        "body_truncated": len(body) > 8000,
        "path": str(path),
    }


@register(
    name="list_tasks",
    description=(
        "List all persistent tasks with their status and next_action. Use this to "
        "remind yourself what projects are open at the start of a session, or when "
        "the operator asks 'what was I working on?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status_filter": {
                "type": "string",
                "description": "Optional: only return tasks with this status (active/paused/done/blocked). Empty = all.",
                "default": "",
            },
        },
        "required": [],
    },
)
def list_tasks(status_filter: str = "") -> dict:
    tasks_dir = _tasks_dir()
    out: list[dict] = []
    filt = status_filter.strip().lower() if status_filter else ""
    for p in sorted(tasks_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _parse_frontmatter(text)
        if not fm.get("id"):
            continue
        if filt and fm.get("status") != filt:
            continue
        out.append(
            {
                "task_id": fm.get("id"),
                "name": fm.get("name"),
                "status": fm.get("status"),
                "updated": fm.get("updated"),
                "next_action": fm.get("next_action"),
            }
        )
    return {"count": len(out), "tasks": out}
