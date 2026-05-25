"""Git workflow tools — sandboxed to operator-configured workspaces.

Exposes inspect (status / diff / log / branch) + state-change (add / commit /
push) operations. Destructive operations (reset --hard, clean -fd, force-push,
branch -D, --no-verify) are NOT exposed. Workspace whitelist enforced; subprocess
timeouts capped at 30s; output truncated for context efficiency.

Recommended use pattern: Lumos drafts a commit (inspect + propose message),
asks the operator to confirm, then executes add/commit/push only on explicit OK.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import register
from ..config import get_settings
from ..log import get_logger


log = get_logger(__name__)


_MAX_OUTPUT_BYTES = 10_000
_MAX_STDERR_BYTES = 2_000
_GIT_TIMEOUT_SECONDS = 30


def _resolve_workspaces() -> list[Path]:
    settings = get_settings()
    raw = settings.git_workspaces.strip()
    if not raw:
        return []
    return [
        Path(p.strip()).expanduser().resolve()
        for p in raw.split(",")
        if p.strip()
    ]


def _check_repo_path(p: str) -> Path:
    workspaces = _resolve_workspaces()
    if not workspaces:
        raise PermissionError(
            "No git workspaces configured. The operator needs to set "
            "LUMOS_GIT_WORKSPACES in .env (comma-separated absolute paths) "
            "and restart `lumos serve`."
        )
    target = Path(p).expanduser().resolve()
    for w in workspaces:
        if target == w:
            return target
        try:
            target.relative_to(w)
            return target
        except ValueError:
            continue
    allowed = " | ".join(str(w) for w in workspaces)
    raise PermissionError(
        f"path is outside git workspaces: {target} (allowed: {allowed})"
    )


def _truncate(s: str, max_bytes: int) -> tuple[str, bool]:
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return s, False
    # Keep the LAST max_bytes — the tail usually contains the most recent
    # output, which is what's useful for diagnostics.
    truncated = encoded[-max_bytes:].decode("utf-8", errors="replace")
    return truncated, True


def _run_git(repo: Path, args: list[str]) -> dict:
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {"error": "git executable not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"error": f"git command timed out after {_GIT_TIMEOUT_SECONDS}s"}
    except OSError as e:
        return {"error": f"git subprocess failed: {e}"}

    stdout, stdout_truncated = _truncate(result.stdout or "", _MAX_OUTPUT_BYTES)
    stderr, stderr_truncated = _truncate(result.stderr or "", _MAX_STDERR_BYTES)
    log.info(
        "git.run",
        cmd=" ".join(args),
        cwd=str(repo),
        exit_code=result.returncode,
        stdout_bytes=len(result.stdout or ""),
    )
    return {
        "exit_code": result.returncode,
        "stdout": stdout,
        "stdout_truncated": stdout_truncated,
        "stderr": stderr,
        "stderr_truncated": stderr_truncated,
    }


@register(
    name="git_status",
    description=(
        "Show the working-tree status of a git repository: modified, staged, "
        "and untracked files plus current branch. CALL THIS to see what's "
        "changed before drafting a commit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {
                "type": "string",
                "description": "Absolute path to a git repository (inside an allowed workspace).",
            },
        },
        "required": ["repo_path"],
    },
)
def git_status(repo_path: str) -> dict:
    try:
        repo = _check_repo_path(repo_path)
    except PermissionError as e:
        return {"error": str(e)}
    return _run_git(repo, ["status", "--short", "--branch"])


@register(
    name="git_diff",
    description=(
        "Show the diff for a git repository. By default returns unstaged changes; "
        "set staged=true to see what's already staged (--cached). CALL THIS to "
        "understand what changed before drafting a commit message. Output capped at 10KB."
    ),
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string"},
            "staged": {
                "type": "boolean",
                "default": False,
                "description": "True = show --cached (staged) diff. False = unstaged.",
            },
        },
        "required": ["repo_path"],
    },
)
def git_diff(repo_path: str, staged: bool = False) -> dict:
    try:
        repo = _check_repo_path(repo_path)
    except PermissionError as e:
        return {"error": str(e)}
    args = ["diff"]
    if staged:
        args.append("--cached")
    return _run_git(repo, args)


@register(
    name="git_log",
    description=(
        "Show recent git commits (oneline format with refs). CALL THIS to "
        "understand recent history of the repo."
    ),
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string"},
            "limit": {
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["repo_path"],
    },
)
def git_log(repo_path: str, limit: int = 10) -> dict:
    try:
        repo = _check_repo_path(repo_path)
    except PermissionError as e:
        return {"error": str(e)}
    return _run_git(
        repo,
        ["log", f"--max-count={int(limit)}", "--oneline", "--decorate"],
    )


@register(
    name="git_branch",
    description=(
        "Show all local branches with the current branch indicated. CALL THIS "
        "to understand branch state before committing/pushing."
    ),
    parameters={
        "type": "object",
        "properties": {"repo_path": {"type": "string"}},
        "required": ["repo_path"],
    },
)
def git_branch(repo_path: str) -> dict:
    try:
        repo = _check_repo_path(repo_path)
    except PermissionError as e:
        return {"error": str(e)}
    return _run_git(repo, ["branch", "-vv"])


@register(
    name="git_add",
    description=(
        "Stage SPECIFIC files for the next commit. CALL THIS to mark files "
        "for inclusion. Requires an explicit list of file paths (relative to "
        "the repo root) — wildcard '-A' style staging is NOT available, to "
        "prevent accidentally staging .env, secrets, or unintended files."
    ),
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string"},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file paths relative to the repo root.",
            },
        },
        "required": ["repo_path", "files"],
    },
)
def git_add(repo_path: str, files: list[str]) -> dict:
    try:
        repo = _check_repo_path(repo_path)
    except PermissionError as e:
        return {"error": str(e)}
    if not files:
        return {"error": "files list cannot be empty (use explicit file paths)"}
    # Reject suspicious entries.
    for f in files:
        if f.startswith("-"):
            return {"error": f"refusing flag-like file argument: {f}"}
    return _run_git(repo, ["add", "--"] + list(files))


@register(
    name="git_commit",
    description=(
        "Commit currently-staged changes with the provided message. CALL THIS "
        "after git_add. Fails if nothing is staged. Pre-commit hooks are NOT "
        "skipped — if a hook fails, the commit is rejected and you'll need to "
        "fix the issue and retry. Co-authored-by lines NOT auto-added."
    ),
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string"},
            "message": {
                "type": "string",
                "description": "Commit message. Can be multi-line with \\n.",
            },
        },
        "required": ["repo_path", "message"],
    },
)
def git_commit(repo_path: str, message: str) -> dict:
    try:
        repo = _check_repo_path(repo_path)
    except PermissionError as e:
        return {"error": str(e)}
    if not message.strip():
        return {"error": "commit message cannot be empty"}
    return _run_git(repo, ["commit", "-m", message])


@register(
    name="git_push",
    description=(
        "Push the current branch to its configured upstream remote. CALL THIS "
        "after git_commit when ready to publish. NO force-push variant available. "
        "Will fail if upstream isn't set; operator must `git push -u origin <branch>` "
        "manually for first-time branch publishing."
    ),
    parameters={
        "type": "object",
        "properties": {"repo_path": {"type": "string"}},
        "required": ["repo_path"],
    },
)
def git_push(repo_path: str) -> dict:
    try:
        repo = _check_repo_path(repo_path)
    except PermissionError as e:
        return {"error": str(e)}
    return _run_git(repo, ["push"])


def _run_gh(repo: Path, args: list[str]) -> dict:
    """Run `gh` CLI in the workspace. Same safety pattern as _run_git."""
    cmd = ["gh"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {
            "error": (
                "gh executable not found on PATH. Install GitHub CLI: "
                "https://cli.github.com/ — then `gh auth login` to authenticate."
            )
        }
    except subprocess.TimeoutExpired:
        return {"error": f"gh command timed out after {_GIT_TIMEOUT_SECONDS}s"}
    except OSError as e:
        return {"error": f"gh subprocess failed: {e}"}

    stdout, stdout_truncated = _truncate(result.stdout or "", _MAX_OUTPUT_BYTES)
    stderr, stderr_truncated = _truncate(result.stderr or "", _MAX_STDERR_BYTES)
    log.info(
        "gh.run",
        cmd=" ".join(args),
        cwd=str(repo),
        exit_code=result.returncode,
    )
    return {
        "exit_code": result.returncode,
        "stdout": stdout,
        "stdout_truncated": stdout_truncated,
        "stderr": stderr,
        "stderr_truncated": stderr_truncated,
    }


@register(
    name="gh_create_pr",
    description=(
        "Create a GitHub pull request via the gh CLI. Requires `gh` installed "
        "and authenticated (`gh auth status` should pass). Pushes the current "
        "branch first if needed, then opens a PR against the default base "
        "branch (usually main/master). Use this AFTER git_commit + git_push, "
        "when ready to open a PR. Title is required; body optional but recommended. "
        "Returns the PR URL on success."
    ),
    parameters={
        "type": "object",
        "properties": {
            "repo_path": {
                "type": "string",
                "description": "Absolute path to a git repository (inside an allowed workspace).",
            },
            "title": {
                "type": "string",
                "description": "PR title (1-2 sentences, focus on the WHY).",
            },
            "body": {
                "type": "string",
                "description": (
                    "PR body in markdown. Should include summary + test plan. "
                    "If empty, gh CLI will use the commit message."
                ),
                "default": "",
            },
            "base": {
                "type": "string",
                "description": "Base branch (default: repo's default branch, usually main).",
                "default": "",
            },
            "draft": {
                "type": "boolean",
                "description": "Open as draft PR. Default false.",
                "default": False,
            },
        },
        "required": ["repo_path", "title"],
    },
)
def gh_create_pr(
    repo_path: str,
    title: str,
    body: str = "",
    base: str = "",
    draft: bool = False,
) -> dict:
    if not title or not title.strip():
        return {"error": "title is required"}
    try:
        repo = _check_repo_path(repo_path)
    except PermissionError as e:
        return {"error": str(e)}
    args = ["pr", "create", "--title", title.strip()]
    if body and body.strip():
        args.extend(["--body", body])
    else:
        # Use commit messages as body if no explicit body provided.
        args.append("--fill")
    if base and base.strip():
        args.extend(["--base", base.strip()])
    if draft:
        args.append("--draft")
    return _run_gh(repo, args)
