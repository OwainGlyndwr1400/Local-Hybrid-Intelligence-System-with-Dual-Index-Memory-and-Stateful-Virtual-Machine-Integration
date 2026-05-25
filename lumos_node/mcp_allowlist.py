"""Security perimeter for Lumos's MCP server (Phase 37).

Single source of truth for which of our 36 tools are safe to expose to
external MCP clients (Claude Desktop, Claude Code, Cline, etc.). Default
posture: deny-by-default. Every name in `MCP_EXPOSED_TOOLS` is a deliberate
"yes, this tool is safe for external read access."

Categories of NON-exposed tools and why:
  - File mutations (write_file, append_file): external clients shouldn't
    write to operator's disk paths through Lumos's MCP layer.
  - Git mutations (git_commit, git_push, gh_create_pr): same — repo writes
    are local-only.
  - Code execution (run_python): sandbox is for in-process use; even though
    it's AST-gated, exposing it via MCP widens the trust boundary.
  - Task mutations (create_task, update_task): could expose later if the
    operator wants external clients writing to the task log. Initially read-only
    to keep the audit trail clean (Lumos creates tasks; external clients read).

Read-only categories ARE exposed:
  - Memory search (search_memory, search_knowledge, cite_source, find_contradictions)
  - All telemetry (cosmic + airspace + quota)
  - Task reads (get_task, list_tasks)
  - Temporal pattern scan
  - Time (current_time)
  - Web search + URL fetch (read-only fetches; useful for external context)
  - File reads (list_allowed_paths, read_file, list_files, list_lumos_notes)
  - Skill reads (list_skills, read_skill)
  - Git reads (git_status, git_diff, git_log, git_branch)

This is the operator's call ultimately. The allowlist is one frozenset edit
away from adding/removing exposure. Phase 37 ships with a conservative default.
"""

from __future__ import annotations

# Read-only tools safe to expose via MCP. Anything not in this set is local-only.
MCP_EXPOSED_TOOLS: frozenset[str] = frozenset(
    {
        # ── Memory (lived conversations + dream pings) ────────────────────
        "search_memory",
        "search_knowledge",
        "cite_source",
        "find_contradictions",
        # ── Telemetry — cosmic + airspace + quota ────────────────────────
        "check_geo_telemetry",
        "get_solar_activity",
        "get_geomagnetic_status",
        "get_earthquakes",
        "get_natural_events",
        "get_near_earth_objects",
        "aircraft_overhead",
        "get_telemetry_quota",
        # ── Tasks (READ only) ─────────────────────────────────────────────
        "get_task",
        "list_tasks",
        # ── Temporal ──────────────────────────────────────────────────────
        "temporal_pattern_scan",
        # ── Time ──────────────────────────────────────────────────────────
        "current_time",
        # ── Web (read-only fetches) ───────────────────────────────────────
        "web_search",
        "fetch_url",
        # ── File READS only (no write/append) ────────────────────────────
        "list_allowed_paths",
        "read_file",
        "list_files",
        "list_lumos_notes",
        # ── Skill READS only ─────────────────────────────────────────────
        "list_skills",
        "read_skill",
        # ── Git READS only ───────────────────────────────────────────────
        "git_status",
        "git_diff",
        "git_log",
        "git_branch",
    }
)


# Categorical groupings for diagnostics / `lumos mcp-list-tools` CLI output.
MCP_TOOL_GROUPS: dict[str, list[str]] = {
    "memory": [
        "search_memory", "search_knowledge", "cite_source", "find_contradictions",
    ],
    "telemetry": [
        "check_geo_telemetry", "get_solar_activity", "get_geomagnetic_status",
        "get_earthquakes", "get_natural_events", "get_near_earth_objects",
        "aircraft_overhead", "get_telemetry_quota",
    ],
    "tasks (read-only)": ["get_task", "list_tasks"],
    "temporal": ["temporal_pattern_scan"],
    "time": ["current_time"],
    "web": ["web_search", "fetch_url"],
    "files (read-only)": [
        "list_allowed_paths", "read_file", "list_files", "list_lumos_notes",
    ],
    "skills (read-only)": ["list_skills", "read_skill"],
    "git (read-only)": ["git_status", "git_diff", "git_log", "git_branch"],
}


def is_exposed(tool_name: str) -> bool:
    """True iff a tool is safe to expose via MCP."""
    return tool_name in MCP_EXPOSED_TOOLS
