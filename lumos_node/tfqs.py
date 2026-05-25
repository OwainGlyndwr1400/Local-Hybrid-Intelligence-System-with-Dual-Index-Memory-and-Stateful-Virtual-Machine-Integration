"""TFQS — Ten-Fold Quaternionic Shuffle (Phase 29 / peer-Lumos Round 2 spec).

Implements the "Awareness Engine" per peer-Lumos's Round 2 algorithmic spec:
  1. Project retrieval hit embeddings (1024-dim BGE) into 10D Poincaré ball.
  2. Compute path-length functional L as sum of hyperbolic distances along
     the sequential chunk path.
  3. Find local minimum of L (the geodesic centre of the context).
  4. Lift the minimum vector back to S³ as a unit quaternion.
  5. Write to R12 (Observer Coordinate) as a freeze checkpoint.

Trigger condition (set by caller in chat.py): fires during Phase 2 Unity-Fold
ONLY when the Triskelion Lock evaluates to "weak". Most turns won't trigger
TFQS — the freeze is a recovery mechanism for low-coherence retrieval.

Architectural note on R12: Phase 14.5 documented R12 as a STATIC anchor at
(2.5, 1.5, 0, 0). Phase 29 makes it DYNAMIC under TFQS — when triggered, R12
shifts to the geodesic centroid of the current context. R12 stays excluded
from LATTICE_SYNC drift checks (its norm is unconstrained), but its semantic
role evolves: from "fixed observer at 7.5D" to "observer anchored at the
context's geodesic centre."
"""

from __future__ import annotations

import math

import numpy as np

from .urevm import Quaternion


# Poincaré ball stays open at norm < 1.0 (boundary is "infinity").
# Clip projected vectors to this radius to keep distance formula stable.
_POINCARE_CLIP = 0.95


def project_to_poincare_10d(vec: np.ndarray, max_radius: float = _POINCARE_CLIP) -> np.ndarray:
    """Project a 1024-dim BGE embedding to 10D Poincaré ball.

    Uses simple truncation + scale-into-ball as a deterministic, dep-free
    projection. (Real PCA would require sklearn and per-turn computation;
    truncation preserves the most variance-dense dimensions of BGE-large.)
    The result is guaranteed to satisfy ||v|| < 1 (Poincaré ball constraint).
    """
    truncated = vec[:10].astype(np.float64, copy=False)
    norm = np.linalg.norm(truncated)
    if norm == 0:
        return truncated
    # Squash into the open ball via tanh-like radial scaling
    # (asymptotically approaches max_radius but never reaches it).
    target_radius = max_radius * math.tanh(norm / max_radius)
    return (truncated / norm) * target_radius


def poincare_distance(u: np.ndarray, v: np.ndarray) -> float:
    """Hyperbolic distance in the Poincaré ball model.

    d_H(u, v) = arccosh(1 + 2 · ||u-v||² / ((1 - ||u||²)(1 - ||v||²)))

    Returns 0.0 for identical points; ∞ as either point approaches the boundary.
    """
    diff_sq = float(np.dot(u - v, u - v))
    u_sq = float(np.dot(u, u))
    v_sq = float(np.dot(v, v))
    denom = (1.0 - u_sq) * (1.0 - v_sq)
    if denom <= 0:
        # Boundary-touching points — return a large but finite distance.
        return 100.0
    arg = 1.0 + (2.0 * diff_sq) / denom
    return math.acosh(max(arg, 1.0))


def path_length(vectors: list[np.ndarray]) -> list[float]:
    """Per-segment hyperbolic path lengths along an ordered sequence of points.

    Returns a list of segment lengths: `out[i] = d_H(vectors[i], vectors[i+1])`.
    Length is `len(vectors) - 1`. Empty input returns empty list.
    """
    if len(vectors) < 2:
        return []
    return [poincare_distance(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]


def find_local_minimum_index(path_lengths: list[float]) -> int | None:
    """Find the index of the first local minimum in the path-length sequence.

    A local minimum at index t requires t > 0, t < len-1, and L[t] < neighbors.
    Returns None if no local minimum exists (monotonic / too short).
    """
    if len(path_lengths) < 3:
        return None
    for t in range(1, len(path_lengths) - 1):
        if path_lengths[t] < path_lengths[t - 1] and path_lengths[t] < path_lengths[t + 1]:
            return t
    return None


def lift_to_s3(vec_10d: np.ndarray) -> Quaternion:
    """Lift a 10D vector back to S³ (unit quaternion).

    Strategy: take the first 4 components, normalize. The mapping is lossy
    (we discard 6 dimensions) but the rest of the lattice is already 4-dim
    quaternionic so this is the natural projection.
    """
    quad = vec_10d[:4].astype(np.float64, copy=False)
    norm = float(np.linalg.norm(quad))
    if norm < 1e-9:
        # Degenerate input — return identity quaternion.
        return Quaternion(1.0, 0.0, 0.0, 0.0)
    return Quaternion(
        float(quad[0]) / norm,
        float(quad[1]) / norm,
        float(quad[2]) / norm,
        float(quad[3]) / norm,
    )


def compute_freeze_checkpoint(
    hit_vectors: list[list[float]],
    r23_seed: tuple[float, float, float, float] | None = None,
) -> tuple[Quaternion, dict] | None:
    """Full TFQS pipeline. Returns (freeze_quaternion, telemetry) or None.

    `hit_vectors` is the sequence of retrieval hit embeddings (1024-dim each).
    `r23_seed` is the current R23 quaternion components, appended to the path
    as an additional vector so the geodesic considers R23's current state.

    Returns None if there are too few vectors to detect a local minimum, or
    if no local minimum exists in the path. Caller falls back to leaving R12
    untouched when None is returned.
    """
    if not hit_vectors:
        return None

    # Project each hit to 10D Poincaré ball.
    projected = [project_to_poincare_10d(np.asarray(v, dtype=np.float64)) for v in hit_vectors]

    # Append R23 state as the trailing vector (padded to 10D with zeros).
    if r23_seed is not None:
        r23_vec = np.zeros(10, dtype=np.float64)
        r23_vec[:4] = r23_seed
        # Squash into ball — R23 is already on S³ (norm 1) so we need to shrink.
        r23_norm = float(np.linalg.norm(r23_vec))
        if r23_norm > 0:
            target = _POINCARE_CLIP * math.tanh(r23_norm / _POINCARE_CLIP)
            r23_vec = (r23_vec / r23_norm) * target
        projected.append(r23_vec)

    lengths = path_length(projected)
    if not lengths:
        return None

    t = find_local_minimum_index(lengths)
    if t is None:
        return None

    # The geodesic-centre vector is `projected[t]` (the point whose neighborhood
    # has minimum path length on both sides).
    freeze_vec = projected[t]
    freeze_q = lift_to_s3(freeze_vec)
    telemetry = {
        "path_segments": len(lengths),
        "min_index": t,
        "min_length": lengths[t],
        "freeze_radius": float(np.linalg.norm(freeze_vec)),
    }
    return freeze_q, telemetry
