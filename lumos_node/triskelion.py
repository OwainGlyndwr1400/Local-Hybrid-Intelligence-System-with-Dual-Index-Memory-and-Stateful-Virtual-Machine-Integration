"""Triskelion 120° Gate — semantic validation firewall per peer-Lumos v2 spec.

The Triskelion gate replaces 90° binary True/False validation with a 120°
trinitarian alignment check. Per the spec, the gate has 7 structural segments:

  3 Arms (the legs of the Triskelion):
    Arm 1 (Real / 0°)        — factual knowledge alignment (JSONL retrieval)
    Arm 2 (Time / 120°)      — session/lived-memory alignment (identity retrieval)
    Arm 3 (Observer / 240°)  — Mass-Gap-pass-rate proxy for cheat-sheet alignment

  3 Edges (the binding energies between arms):
    Edge A — Real ↔ Time     — fact vs. memory coherence
    Edge B — Time ↔ Observer — memory vs. identity coherence
    Edge C — Observer ↔ Real — identity vs. fact coherence

  1 Vertical Beam (Prime 7):
    Modulo-7 checksum over the query vector — "vertical stability" per spec.

This implementation is TELEMETRY-ONLY in its first ship. The lock-status is
computed and exposed but does NOT route the turn to a clarifying question.
Operator can promote to hard-gating behavior via a later toggle.

Source: peer-Lumos v2 spec (Implementation Roadmap §3 + URE-VM v2 §7).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class TriskelionLock:
    """Result of computing the Triskelion gate for one chat turn."""

    arm_real: float       # 0..1, alignment with knowledge lane
    arm_time: float       # 0..1, alignment with identity/memory lane
    arm_observer: float   # 0..1, alignment with cheat-sheet/identity (proxy)

    edge_a: float         # Real ↔ Time coherence
    edge_b: float         # Time ↔ Observer coherence
    edge_c: float         # Observer ↔ Real coherence

    vertical_beam: int    # Modulo-7 checksum (0..6)

    locked: bool          # all arms > 0.5
    weak: bool            # any arm < 0.3
    status: str           # "strong" | "moderate" | "weak"

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_real": self.arm_real,
            "arm_time": self.arm_time,
            "arm_observer": self.arm_observer,
            "edge_a": self.edge_a,
            "edge_b": self.edge_b,
            "edge_c": self.edge_c,
            "vertical_beam": self.vertical_beam,
            "locked": self.locked,
            "weak": self.weak,
            "status": self.status,
        }


def _mean_score(hits: list[Any]) -> float:
    """Mean score of a list of Hit objects. Returns 0.0 for empty list."""
    if not hits:
        return 0.0
    total = sum(getattr(h, "score", 0.0) for h in hits)
    return float(total / len(hits))


def _mod7_vertical_beam(query: str) -> int:
    """Vertical Beam — Mod-7 checksum on the query's UTF-8 byte sum.

    Per spec: "provides the vertical stability required for lattice rigidity."
    Prime 7 because it's the Toggle Power (31 ≡ 7 mod 24).
    """
    if not query:
        return 0
    return sum(query.encode("utf-8")) % 7


def _classify(arm_real: float, arm_time: float, arm_observer: float) -> tuple[bool, bool, str]:
    """Return (locked, weak, status) given the three arm scores.

    locked: all three arms exceed 0.5 (strong lock)
    weak: any arm below 0.3 (weak lock — at least one channel unaligned)
    """
    locked = arm_real > 0.5 and arm_time > 0.5 and arm_observer > 0.5
    weak = arm_real < 0.3 or arm_time < 0.3 or arm_observer < 0.3
    if locked:
        status = "strong"
    elif weak:
        status = "weak"
    else:
        status = "moderate"
    return locked, weak, status


def compute_triskelion(
    query: str,
    identity_hits: list[Any],
    knowledge_hits: list[Any],
    mass_gap_floor: float = 0.657,
) -> TriskelionLock:
    """Compute the Triskelion lock from query + retrieval results.

    Pragmatic shortcuts (Phase 24 first ship — to be refined if validation
    behavior is promoted from telemetry to routing):

      Arm 1 Real     = mean cosine of knowledge hits (factual data alignment)
      Arm 2 Time     = mean cosine of identity hits (lived-memory alignment)
      Arm 3 Observer = fraction of hits that *would* pass Mass Gap raw —
                       a proxy for cheat-sheet alignment until we cache the
                       cheat-sheet embedding properly.

    Edges are geometric means of the connected arms — captures the "binding
    energy" between paired channels.
    """
    arm_real = _mean_score(knowledge_hits)
    arm_time = _mean_score(identity_hits)

    # Observer arm — fraction of hits at or above raw Mass Gap floor. If most
    # survivors are comfortably above the floor, the observer is well-aligned
    # with the system's defined "valid memory" threshold.
    all_hits = list(identity_hits) + list(knowledge_hits)
    if all_hits:
        scored = sum(1 for h in all_hits if getattr(h, "score", 0.0) >= mass_gap_floor)
        arm_observer = scored / len(all_hits)
    else:
        arm_observer = 0.0

    # Edges — Phase 29 PMG (Product-Mean-Gap) Identity form.
    # Algebraic identity: ((a+b)/2)² − ((a−b)/2)² = a·b (Babylonian product-as-
    # difference-of-squares). The expanded form exposes the "missing quarter"
    # term ((a-b)/2)² which encodes contradiction friction between paired arms.
    # Computationally edges become products (weaker than sqrt(ab)) — visible
    # only in HUD display, not in classification gates (those still look at arms).
    edge_a = ((arm_real + arm_time) / 2.0) ** 2 - ((arm_real - arm_time) / 2.0) ** 2
    edge_b = ((arm_time + arm_observer) / 2.0) ** 2 - ((arm_time - arm_observer) / 2.0) ** 2
    edge_c = ((arm_observer + arm_real) / 2.0) ** 2 - ((arm_observer - arm_real) / 2.0) ** 2

    vertical_beam = _mod7_vertical_beam(query)
    locked, weak, status = _classify(arm_real, arm_time, arm_observer)

    return TriskelionLock(
        arm_real=arm_real,
        arm_time=arm_time,
        arm_observer=arm_observer,
        edge_a=edge_a,
        edge_b=edge_b,
        edge_c=edge_c,
        vertical_beam=vertical_beam,
        locked=locked,
        weak=weak,
        status=status,
    )
