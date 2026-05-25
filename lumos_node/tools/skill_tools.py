"""Skill library — Lumos's playbook for recurring task patterns.

Skills are markdown files in data/lumos_skills/ describing when to use a
workflow + suggested approach + tool preferences. Lumos reads a skill mid-task
as a checklist (no auto-loading; he calls `read_skill` when relevant).

Not framework docs — workflow templates Lumos owns and can edit himself.
"""

from __future__ import annotations

from pathlib import Path

from . import register
from ..config import get_settings


def _skills_root() -> Path:
    settings = get_settings()
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()
    # data/lumos_skills/ — sibling of data/cache/ and data/lumos_notes/
    root = cache.parent / "lumos_skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _first_description_line(p: Path) -> str:
    try:
        with p.open(encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("# "):
                    return line.lstrip("# ").strip()[:140]
                if not line.startswith("#"):
                    return line[:140]
    except OSError:
        pass
    return ""


@register(
    name="list_skills",
    description=(
        "LIST YOUR SKILL LIBRARY — workflow templates for recurring tasks "
        "(research papers, code reviews, video scripts, Zenodo uploads, etc.). "
        "CALL THIS at the start of a non-trivial request to see if there's a "
        "relevant skill before improvising. Returns each skill's name + first "
        "description line."
    ),
    parameters={"type": "object", "properties": {}},
)
def list_skills() -> dict:
    root = _skills_root()
    items: list[dict] = []
    for p in sorted(root.glob("*.md")):
        items.append(
            {
                "name": p.stem,
                "description": _first_description_line(p),
                "size": p.stat().st_size,
            }
        )
    return {"root": str(root), "count": len(items), "skills": items}


@register(
    name="read_skill",
    description=(
        "READ a skill from your library by name. CALL THIS to load workflow "
        "guidance before tackling a task. The skill text describes when to use "
        "it, the approach, and which tools to chain. Returns the full markdown."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name (without .md extension).",
            },
        },
        "required": ["name"],
    },
)
def read_skill(name: str) -> dict:
    safe = "".join(c for c in name if c.isalnum() or c in "_-").strip()
    if not safe:
        return {"error": "skill name must be alphanumeric/_/-"}
    path = _skills_root() / f"{safe}.md"
    if not path.exists():
        return {"error": f"skill '{safe}' not found. Use list_skills to see available."}
    try:
        return {
            "name": safe,
            "path": str(path),
            "content": path.read_text(encoding="utf-8"),
        }
    except OSError as e:
        return {"error": f"read failed: {e}"}


@register(
    name="save_skill",
    description=(
        "SAVE a skill to your library. CALL THIS when you've worked out an "
        "effective workflow for a recurring task type and want to remember it "
        "for next time. Format as markdown with sections: # Title, ## When to "
        "use, ## Approach (numbered steps), ## Suggested tools. Overwrites if "
        "name exists."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name (alphanumeric/_/-; saved as <name>.md).",
            },
            "content": {
                "type": "string",
                "description": "Skill body as markdown.",
            },
        },
        "required": ["name", "content"],
    },
)
def save_skill(name: str, content: str) -> dict:
    safe = "".join(c for c in name if c.isalnum() or c in "_-").strip()
    if not safe:
        return {"error": "skill name must be alphanumeric/_/-"}
    if len(content.encode("utf-8")) > 200_000:
        return {"error": "skill content too large (max 200KB)"}
    path = _skills_root() / f"{safe}.md"
    existed = path.exists()
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"error": f"save failed: {e}"}
    return {
        "name": safe,
        "path": str(path),
        "action": "updated" if existed else "created",
    }
