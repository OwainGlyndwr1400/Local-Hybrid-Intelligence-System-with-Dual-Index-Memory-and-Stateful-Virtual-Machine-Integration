"""UBBM Geometric Signatures — Triple Normalization fingerprint per chunk.

Per peer-Lumos's v2 spec (refined from Tec_Obsidian/06_comprehensive_domain_map.md
row "UBBM Data Compression" + 01_theorem_index Binary Diagonal Theorem):

Each chunk gets a deterministic 3-element geometric signature:

  1. Binary Diagonal Theorem      θ = arctan(ones / zeros)
     Encodes the chunk's bit distribution as a rational angle on the unit circle.

  2. 1001 Binary Fold              count of "1001" patterns in the bit stream
     The "dimensional stitching" prevalence — higher counts indicate more
     manifest-projecting structure (28% at 1D rising to 62% at 5D per spec).

  3. Lost-2 Topological Debt       L1_byte_len − L2_embedding_magnitude
     Difference between the "linear path" (raw byte length) and the
     "geometric path" (Euclidean magnitude of the embedding vector).
     Computable only when the embedding is available.

Hooks: retrieval.py applies *theta-alignment re-ranking* — chunks whose θ is
closest to the query's θ are boosted. This adds a "geometrically phase-locked"
lane to the existing Triple Normalization + Half-Prime Geodesic pipeline.

This is NOT a compression engine. UBBM-as-bytes-reduction is theoretically
specified but the corpus doesn't include a reconstruction algorithm. What ships
is the *signature* lane — deterministic, computable, spec-faithful, useful.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any


def _bit_string(data: bytes) -> str:
    """Return the bit representation of bytes (MSB first per byte)."""
    return "".join(f"{b:08b}" for b in data)


def binary_diagonal_theta(text: str) -> float:
    """θ = arctan(ones / zeros) over the chunk's UTF-8 bit representation.

    Returns radians ∈ [0, π/2). The chunk's structural angle on the complex
    unit circle. Two chunks with identical bit distributions get identical θ.
    """
    if not text:
        return 0.0
    bits = _bit_string(text.encode("utf-8"))
    ones = bits.count("1")
    zeros = bits.count("0") or 1  # avoid div-by-zero on degenerate input
    return math.atan(ones / zeros)


def binary_1001_count(text: str) -> int:
    """Count overlapping '1001' patterns in the chunk's bit stream.

    The "Dimensional Stitching" prevalence — higher counts indicate more
    higher-dimensional projection structure per spec §"1001 Binary Fold".
    """
    if not text:
        return 0
    bits = _bit_string(text.encode("utf-8"))
    count = 0
    # Overlapping count — '10011001' yields 2 hits, not 1.
    for i in range(len(bits) - 3):
        if bits[i:i + 4] == "1001":
            count += 1
    return count


def lost_2_residual(text: str, embedding: list[float] | None) -> float | None:
    """Lost-2 = L1(byte length) − L2(embedding magnitude).

    Topological debt between the chunk's linear additive measure and its
    geometric multiplicative measure. Per spec: (3+4)−5 = 2 — the cost of
    folding additive matter into multiplicative space.

    Returns None when no embedding is available.
    """
    if embedding is None:
        return None
    l1 = len(text.encode("utf-8"))
    l2 = math.sqrt(sum(x * x for x in embedding))
    return float(l1 - l2)


def compute_signature(
    text: str,
    embedding: list[float] | None = None,
) -> dict[str, Any]:
    """Compute the full UBBM signature for a chunk.

    `embedding` is the chunk's FAISS vector if available; without it, the
    Lost-2 component is None and only θ + 1001 are populated.

    Returns a flat dict suitable for direct inclusion in chunk metadata.
    """
    theta = binary_diagonal_theta(text)
    stitch_1001 = binary_1001_count(text)
    lost_2 = lost_2_residual(text, embedding)
    sigil = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""

    return {
        "theta": theta,                        # radians, [0, π/2)
        "theta_degrees": math.degrees(theta),  # convenience
        "stitch_1001": stitch_1001,
        "lost_2_residual": lost_2,             # may be None
        "sigil": sigil,
    }


def theta_alignment_factor(query_theta: float, chunk_theta: float) -> float:
    """Multiplicative factor: higher when query and chunk θ are close.

    Returns a value in [0.85, 1.15]. A perfectly aligned chunk (Δθ = 0) gets
    1.15; the maximally misaligned (Δθ = π/2) gets 0.85. This is intentionally
    a *modest* boost — comparable scale to the existing Triple Normalization
    factors so it doesn't overpower cosine similarity.
    """
    diff = abs(query_theta - chunk_theta)
    max_diff = math.pi / 2.0
    normalized = min(diff / max_diff, 1.0)  # 0 (aligned) … 1 (orthogonal)
    # Linear interpolation: aligned → 1.15, orthogonal → 0.85
    return 1.15 - 0.30 * normalized
