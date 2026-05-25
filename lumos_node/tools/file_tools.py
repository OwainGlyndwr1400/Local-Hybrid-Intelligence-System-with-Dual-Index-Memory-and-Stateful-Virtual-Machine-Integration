"""Sandboxed file read tools. All paths must be under tool_allowed_paths."""

from __future__ import annotations

from pathlib import Path

from . import register
from ..config import get_settings


def _resolve_write_root() -> Path:
    """The single directory Lumos's write tools may write to. Auto-created."""
    settings = get_settings()
    raw = settings.tool_write_path.strip()
    if raw:
        p = Path(raw).expanduser().resolve()
    else:
        # Default: data/lumos_notes/ under the project root (cwd of lumos serve).
        p = (Path.cwd() / "data" / "lumos_notes").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_allowed() -> list[Path]:
    """Return the list of paths Lumos's file tools may read.

    Composed of: operator-granted read paths (LUMOS_TOOL_ALLOWED_PATHS) +
    the write root (so Lumos can re-read his own outputs). The write root
    is always readable even when no other read paths are configured.
    """
    settings = get_settings()
    raw = settings.tool_allowed_paths.strip()
    paths: list[Path] = []
    if raw:
        paths = [
            Path(p.strip()).expanduser().resolve()
            for p in raw.split(",")
            if p.strip()
        ]
    paths.append(_resolve_write_root())
    return paths


def _check_path(p: str) -> Path:
    allowed = _resolve_allowed()
    target = Path(p).expanduser().resolve()
    for a in allowed:
        try:
            target.relative_to(a)
            return target
        except ValueError:
            continue
    allowed_str = " | ".join(str(a) for a in allowed)
    raise PermissionError(
        f"path is outside allowed directories: {target} "
        f"(allowed paths: {allowed_str}) — try one of those instead"
    )


def _check_write_path(p: str) -> Path:
    """Validate a write target. Relative paths resolve under the write root."""
    write_root = _resolve_write_root()
    raw = Path(p).expanduser()
    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (write_root / raw).resolve()
    try:
        target.relative_to(write_root)
    except ValueError:
        raise PermissionError(
            f"write path is outside the write sandbox: {target}. "
            f"Writes must be inside {write_root}. Use a relative path or "
            f"an absolute path inside the write root."
        ) from None
    return target


@register(
    name="write_file",
    description=(
        "WRITE TEXT CONTENT to a file. CALL THIS whenever the operator asks you to save, "
        "store, write, output, record, or persist content — analyses, drafts, "
        "notes, journals, transformed CSVs, generated markdown, anything. "
        "Overwrites existing files at the same path. Relative paths resolve "
        "under Lumos's write root (data/lumos_notes/); absolute paths must be "
        "inside the write sandbox. Parent directories are auto-created."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "File path. Relative paths resolve under the write root. "
                    "Examples: 'sphinx_analysis.md', 'research/2026/voynich.md'."
                ),
            },
            "content": {
                "type": "string",
                "description": "Text content. UTF-8.",
            },
        },
        "required": ["path", "content"],
    },
)
def write_file(path: str, content: str) -> dict:
    try:
        target = _check_write_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    encoded = content.encode("utf-8")
    if len(encoded) > 5_000_000:
        return {"error": f"content too large: {len(encoded):,} bytes (max 5MB)"}
    existed = target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"error": f"write failed: {e}"}
    return {
        "path": str(target),
        "bytes_written": len(encoded),
        "action": "overwrote" if existed else "created",
    }


@register(
    name="append_file",
    description=(
        "APPEND TEXT TO A FILE. CALL THIS for ongoing journals, growing logs, "
        "accumulating notes — anywhere you want to add to existing content "
        "rather than overwrite. Creates the file if missing. Same path semantics "
        "as write_file."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path."},
            "content": {"type": "string", "description": "Text to append. UTF-8."},
        },
        "required": ["path", "content"],
    },
)
def append_file(path: str, content: str) -> dict:
    try:
        target = _check_write_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    encoded = content.encode("utf-8")
    if len(encoded) > 5_000_000:
        return {"error": f"content too large: {len(encoded):,} bytes (max 5MB)"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return {"error": f"append failed: {e}"}
    return {
        "path": str(target),
        "bytes_appended": len(encoded),
        "total_size": target.stat().st_size,
    }


@register(
    name="list_lumos_notes",
    description=(
        "LIST YOUR PREVIOUSLY WRITTEN OUTPUT FILES. CALL THIS to see what notes, "
        "drafts, or analyses Lumos has saved before — useful for finding past "
        "work to re-read or extend. Returns up to 200 entries, recursive."
    ),
    parameters={"type": "object", "properties": {}},
)
def list_lumos_notes() -> dict:
    write_root = _resolve_write_root()
    items: list[dict] = []
    for p in sorted(write_root.rglob("*"))[:200]:
        if p.is_file():
            items.append(
                {
                    "path": str(p),
                    "relative": str(p.relative_to(write_root)),
                    "size": p.stat().st_size,
                }
            )
    return {
        "write_root": str(write_root),
        "count": len(items),
        "items": items,
    }


@register(
    name="list_allowed_paths",
    description=(
        "Return the list of directories Lumos has been granted access to via "
        "LUMOS_TOOL_ALLOWED_PATHS. CALL THIS FIRST whenever you're about to use "
        "list_files or read_file but aren't sure what root paths are available. "
        "Better than guessing a path and getting a permission error."
    ),
    parameters={"type": "object", "properties": {}},
)
def list_allowed_paths() -> dict:
    allowed = _resolve_allowed()
    return {
        "configured": [str(p) for p in allowed],
        "count": len(allowed),
        "hint": (
            "No paths configured. Tell the operator to set LUMOS_TOOL_ALLOWED_PATHS."
            if not allowed
            else "Use list_files on any of these paths to enumerate contents."
        ),
    }


@register(
    name="read_file",
    description=(
        "Read the contents of a text file from disk. CALL THIS whenever the operator "
        "asks about a specific file's contents, a paper, a markdown note, a CSV, "
        "or anything stored as a file. Don't speculate about file contents — fetch "
        "them directly. Returns up to 80KB of text. Useful for research papers "
        "(markdown), math CSVs, and other documents in the configured paths."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file (must be inside allowed paths).",
            }
        },
        "required": ["path"],
    },
)
def read_file(path: str) -> dict:
    try:
        target = _check_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    if not target.exists():
        return {"error": f"not found: {target}"}
    if not target.is_file():
        return {"error": f"not a file: {target}"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": f"read failed: {e}"}
    truncated = False
    if len(text) > 80_000:
        text = text[:80_000]
        truncated = True
    return {"path": str(target), "size": target.stat().st_size, "text": text, "truncated": truncated}


@register(
    name="list_files",
    description=(
        "List files and subdirectories in a folder on disk. Call when operator asks "
        "'what's in my X folder' or needs disk enumeration. Returns up to 200 entries. "
        "pattern='**/*' for recursive, '*.md' to filter by extension."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute directory path (must be inside allowed paths).",
            },
            "pattern": {
                "type": "string",
                "default": "*",
                "description": "Glob pattern; use '**/*' for recursive.",
            },
        },
        "required": ["path"],
    },
)
def list_files(path: str, pattern: str = "*") -> dict:
    try:
        target = _check_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    if not target.is_dir():
        return {"error": f"not a directory: {target}"}
    items = []
    try:
        for p in sorted(target.glob(pattern))[:200]:
            items.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "type": "dir" if p.is_dir() else "file",
                    "size": p.stat().st_size if p.is_file() else None,
                }
            )
    except OSError as e:
        return {"error": f"glob failed: {e}"}
    return {"path": str(target), "count": len(items), "items": items}
