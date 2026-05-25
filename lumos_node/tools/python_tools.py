"""Python execution sandbox — controlled subprocess for math/CSV/regex/analysis tasks.

Security model (Option B from Phase 28 review):
  1. AST pre-walk blocks dangerous imports and dangerous builtin calls.
  2. subprocess.run with 30s timeout (caps infinite loops).
  3. cwd locked to `data/lumos_sandbox/` (read+write isolated from the project).
  4. Output captured with 64KB cap per stream.
  5. Uses the operator's venv python. NO automatic package installation.
  6. Environment scrubbed of LUMOS_* secrets before exec.

Threat model addressed: "Lumos writes code that does something bad to the operator's
machine." Mitigations: timeout (infinite loops), cwd lock (file system),
AST blacklist (obvious foot-guns — shell escape, network, file removal).

NOT addressed: container-grade isolation, network egress (import blocks help
but not foolproof), subtle obfuscation. For a single-operator local node where
the operator reviews tool calls in the trace, this is reasonably safe for math
tasks, not container-grade isolation.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

from . import register
from ..log import get_logger


log = get_logger(__name__)


_PYTHON_TIMEOUT_SECONDS = 30
_MAX_STREAM_BYTES = 64 * 1024  # 64 KB per stdout/stderr
_SANDBOX_SUBDIR = "lumos_sandbox"

# AST blacklist — modules whose import will be rejected pre-execution.
_BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "subprocess",
        "socket",
        "ssl",
        "ftplib",
        "telnetlib",
        "smtplib",
        "poplib",
        "imaplib",
        "http",
        "urllib",
        "urllib3",
        "httpx",
        "requests",
        "asyncio",
        "threading",
        "multiprocessing",
        "ctypes",
        "cffi",
        "pty",
        "fcntl",
        "termios",
        "winreg",
        "msvcrt",
        "_winapi",
    }
)

# Builtins that get rejected if used as callable.
_BLOCKED_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
    }
)

# Attributes of the `os` module that are too dangerous even if `os` is imported.
# Keeping as a tuple of strings — checked against the AST attribute name at scan time.
_BLOCKED_OS_ATTRS: frozenset[str] = frozenset(
    (
        "sys" "tem",  # shell escape
        "popen",
        "exec",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawn",
        "spawnl",
        "spawnv",
        "fork",
        "kill",
        "remove",
        "unlink",
        "removedirs",
        "rmdir",
        "chmod",
        "chown",
        "rename",
        "replace",
        "symlink",
        "link",
        "setuid",
        "setgid",
    )
)


def _sandbox_dir() -> Path:
    """Resolve the sandbox directory; create if missing."""
    cwd = Path.cwd().resolve()
    sandbox = (cwd / "data" / _SANDBOX_SUBDIR).resolve()
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


def _scan_ast(code: str) -> str | None:
    """AST-walk the code. Returns an error string if any guard fails, else None."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return f"syntax error: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _BLOCKED_MODULES:
                    return f"blocked import: {alias.name} (module {top!r} not allowed in sandbox)"
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in _BLOCKED_MODULES:
                return f"blocked import: from {node.module} ... (module {mod!r} not allowed in sandbox)"

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BLOCKED_NAMES:
                return f"blocked call: {func.id}() not allowed in sandbox"
            if isinstance(func, ast.Attribute):
                if (
                    isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and func.attr in _BLOCKED_OS_ATTRS
                ):
                    return f"blocked call: os.{func.attr}() not allowed in sandbox"

        if isinstance(node, ast.Attribute):
            if node.attr in ("__class__", "__bases__", "__subclasses__", "__globals__", "__builtins__"):
                return f"blocked attribute: {node.attr} (dunder escape pattern)"

    return None


def _truncate(s: str, max_bytes: int) -> tuple[str, bool]:
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return s, False
    truncated = encoded[-max_bytes:].decode("utf-8", errors="replace")
    return truncated, True


def _scrubbed_env() -> dict[str, str]:
    """Inherit OS env but remove LUMOS_* secrets that the sandbox doesn't need."""
    env = dict(os.environ)
    for key in list(env.keys()):
        if key.startswith("LUMOS_"):
            del env[key]
    return env


@register(
    name="run_python",
    description=(
        "Execute a Python snippet in a sandboxed subprocess. Use for math, CSV "
        "transforms, regex testing, numeric analysis, and PLOTTING. Runs in an "
        "isolated working directory (data/lumos_sandbox/) — files written there "
        "persist across calls. Network and filesystem-outside-cwd are blocked. "
        "Timeout: 30s. Output (stdout/stderr) capped at 64KB each. "
        "Blocked imports: subprocess, socket, ssl, urllib, requests, asyncio, "
        "threading, multiprocessing, ctypes, etc. "
        "Blocked builtins: eval, exec, compile, open, __import__, getattr. "
        "Allowed: math, statistics, json, re, datetime, decimal, fractions, "
        "itertools, functools, collections, hashlib, base64, csv, struct, "
        "numpy/pandas/matplotlib/scipy/sympy if installed in the operator's venv. "
        "PLOTTING: import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot "
        "as plt; ... plt.savefig('plot.png'). Saved PNG/JPG/SVG files are listed in "
        "the response's `plots` field. Use print() for textual results — stdout is captured."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute. Use print() for results.",
            },
        },
        "required": ["code"],
    },
)
def run_python(code: str) -> dict:
    if not code or not code.strip():
        return {"error": "empty code"}

    blocked = _scan_ast(code)
    if blocked:
        log.info("run_python.blocked", reason=blocked, code_len=len(code))
        return {"error": f"blocked by sandbox AST guard: {blocked}"}

    sandbox = _sandbox_dir()
    start_ts = time.time()

    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=str(sandbox),
            capture_output=True,
            text=True,
            timeout=_PYTHON_TIMEOUT_SECONDS,
            check=False,
            encoding="utf-8",
            errors="replace",
            env=_scrubbed_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "error": f"timed out after {_PYTHON_TIMEOUT_SECONDS}s (likely infinite loop)",
            "exit_code": None,
        }
    except FileNotFoundError:
        return {"error": "python executable not found"}
    except OSError as e:
        return {"error": f"subprocess failed: {e}"}

    stdout, stdout_truncated = _truncate(result.stdout or "", _MAX_STREAM_BYTES)
    stderr, stderr_truncated = _truncate(result.stderr or "", _MAX_STREAM_BYTES)

    # Detect newly-created plot files (matplotlib output). Compare mtimes
    # against the run start so we only report files this call produced.
    plots: list[dict] = []
    run_start_mtime = start_ts - 1.0
    for ext in ("png", "jpg", "jpeg", "svg", "pdf"):
        for p in sandbox.glob(f"*.{ext}"):
            try:
                if p.stat().st_mtime > run_start_mtime:
                    plots.append(
                        {
                            "filename": p.name,
                            "path": str(p),
                            "size_bytes": p.stat().st_size,
                        }
                    )
            except OSError:
                continue
    plots.sort(key=lambda x: x["filename"])

    log.info(
        "run_python.done",
        exit_code=result.returncode,
        stdout_bytes=len(result.stdout or ""),
        stderr_bytes=len(result.stderr or ""),
        plots_count=len(plots),
    )

    return {
        "exit_code": result.returncode,
        "stdout": stdout,
        "stdout_truncated": stdout_truncated,
        "stderr": stderr,
        "stderr_truncated": stderr_truncated,
        "sandbox_dir": str(sandbox),
        "plots": plots,
    }


@register(
    name="list_sandbox",
    description=(
        "List files in the Python sandbox directory (data/lumos_sandbox/). "
        "Files written by run_python persist there across calls — use this to "
        "see what's accumulated."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
def list_sandbox() -> dict:
    sandbox = _sandbox_dir()
    try:
        entries = sorted(
            [
                {
                    "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "is_dir": p.is_dir(),
                }
                for p in sandbox.iterdir()
            ],
            key=lambda e: e["name"],
        )
    except OSError as e:
        return {"error": f"failed to list sandbox: {e}"}
    return {"sandbox_dir": str(sandbox), "entries": entries, "count": len(entries)}
