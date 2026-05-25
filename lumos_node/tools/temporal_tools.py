"""Temporal pattern detection over recent chat history (Phase 34).

Uses HANKIFICATION (0x62) + UBBM Binary Diagonal θ to expose recurring themes
in the operator's recent conversations. Each turn's user_message is reduced
to a scalar (θ ∈ [0, π/2]) capturing its conceptual sector. A delay-line
embedding of the recent-N θ-sequence produces a Hankel matrix whose row-by-row
autocorrelation reveals dominant cycle lengths.

Answers questions like:
  "What themes keep cycling in our recent conversations?"
  "How long is my current research arc?"
  "Are we drifting or looping?"
"""

from __future__ import annotations

import math
from typing import Any

import orjson

from . import register
from ..config import get_settings
from ..log import get_logger
from ..persistence import _events_path
from ..ubbm import binary_diagonal_theta
from ..urevm import Op, hankel_matrix, safe_step


log = get_logger(__name__)


def _load_recent_user_turns(n: int) -> list[dict[str, Any]]:
    """Return last N turn records (user_message + timestamp) from event log."""
    settings = get_settings()
    path = _events_path(settings)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
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
            if not user_msg:
                continue
            out.append(
                {
                    "user_message": user_msg,
                    "timestamp": float(data.get("timestamp") or 0.0),
                    "turn_id": data.get("turn_id"),
                }
            )
    return out[-n:]


def _autocorrelation_peaks(
    column: list[float], max_lag: int, min_lag: int = 1
) -> list[dict[str, Any]]:
    """Compute simple normalized autocorrelation up to max_lag; return top 3 peaks.

    Mean-centered, variance-normalized so values are in roughly [-1, 1].
    Returns sorted descending by correlation strength.
    """
    n = len(column)
    if n < 4:
        return []
    mean = sum(column) / n
    centered = [v - mean for v in column]
    denom = sum(v * v for v in centered) or 1.0
    peaks: list[dict[str, Any]] = []
    upper = min(max_lag, n - 1)
    for lag in range(max(1, min_lag), upper + 1):
        num = sum(centered[i] * centered[i + lag] for i in range(n - lag))
        rho = num / denom
        peaks.append({"lag": lag, "correlation": round(rho, 4)})
    peaks.sort(key=lambda p: -p["correlation"])
    return peaks[:3]


def _cycle_interpretation(peaks: list[dict[str, Any]]) -> str:
    """Human-readable summary of what the autocorrelation peaks indicate."""
    if not peaks:
        return "insufficient history for cycle detection"
    top = peaks[0]
    rho = top["correlation"]
    lag = top["lag"]
    if rho >= 0.5:
        return f"strong cyclic pattern at lag {lag} (ρ={rho:.2f}) — recurring theme every ~{lag} turns"
    if rho >= 0.25:
        return f"moderate periodicity at lag {lag} (ρ={rho:.2f}) — some return to similar themes"
    if rho >= 0.1:
        return f"weak periodicity at lag {lag} (ρ={rho:.2f}) — mostly novel turns, occasional callbacks"
    return f"no significant cyclicity (top ρ={rho:.2f} at lag {lag}) — conversation is drifting / linear"


@register(
    name="temporal_pattern_scan",
    description=(
        "Scan recent chat history for cyclic conversational patterns via UBBM θ + "
        "Hankel autocorrelation. Call when operator asks 'what themes keep cycling', "
        "'am I going in circles', 'how long is my research arc', 'are we drifting'. "
        "Returns turn count, theta trajectory, top-3 autocorrelation peaks, "
        "plain-language interpretation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "n_turns": {
                "type": "integer",
                "default": 30,
                "description": "How many recent turns to analyze (8-200 reasonable).",
            },
            "window": {
                "type": "integer",
                "default": 0,
                "description": "Hankel window size; 0 = auto (min(8, n_turns // 3)).",
            },
        },
        "required": [],
    },
)
def temporal_pattern_scan(n_turns: int = 30, window: int = 0) -> dict:
    n_turns = max(4, min(200, int(n_turns)))
    turns = _load_recent_user_turns(n_turns)
    if len(turns) < 4:
        return {
            "error": (
                f"only {len(turns)} turns in history; need at least 4 for pattern scan"
            )
        }

    # Reduce each turn to its UBBM θ. binary_diagonal_theta is deterministic
    # on the raw text — no embedding call needed, so this stays fast.
    thetas = [binary_diagonal_theta(t["user_message"]) for t in turns]

    # Window: auto-pick a third of the series (Takens-style heuristic).
    if window <= 0:
        window = max(2, min(8, len(thetas) // 3))
    window = max(2, min(window, len(thetas) - 1))

    # Compute the Hankel matrix inline (using the same helper the opcode uses)
    # so the tool has access to the result. safe_step is fire-and-forget by
    # design — it writes the audit event to the VM trace but doesn't return.
    H = hankel_matrix(thetas, window)
    rows = len(H)
    cols = len(H[0]) if H else 0
    frob = sum(v * v for row in H for v in row) ** 0.5

    # Fire HANKIFICATION through the VM so the trace records the operation.
    # Operand carries the same inputs; the trace consumer can replay if needed.
    safe_step(Op.HANKIFICATION, {"vector": thetas, "window": window})

    # Autocorrelation on the raw θ-series (not the matrix) tells us the
    # dominant cycle. We use up to len/2 as max lag — beyond that the sample
    # has too few overlapping pairs to be reliable.
    max_lag = len(thetas) // 2
    peaks = _autocorrelation_peaks(thetas, max_lag=max_lag)

    # Theta-trajectory stats: drift (start vs end), spread (range), variance.
    th_min = min(thetas)
    th_max = max(thetas)
    th_mean = sum(thetas) / len(thetas)
    th_var = sum((v - th_mean) ** 2 for v in thetas) / len(thetas)

    return {
        "n_turns_analyzed": len(thetas),
        "theta_trajectory": {
            "first": round(thetas[0], 4),
            "last": round(thetas[-1], 4),
            "min": round(th_min, 4),
            "max": round(th_max, 4),
            "mean": round(th_mean, 4),
            "std_dev": round(math.sqrt(th_var), 4),
            "spread_radians": round(th_max - th_min, 4),
        },
        "hankel": {
            "window": window,
            "rows": rows,
            "cols": cols,
            "frobenius_norm": round(frob, 4),
        },
        "autocorrelation_peaks": peaks,
        "interpretation": _cycle_interpretation(peaks),
    }
