"""URE-VM: branch-free 72-opcode quaternionic dispatch engine.

Implements the load-bearing primitives from the canonical specs:

  - Quaternion algebra (Hamilton product) over named channels α/β/γ/δ
    (Cognition, Emotion, Memory, Archetype).
  - 24 Leech-lattice nodes (R00..R23), each a unit quaternion on S³.
  - 370-tick cycle (Base-15 dual-clock); Forbidden State 361 = 19² triggers reset.
  - Klein-4 predicate planes (RR / RI / IR / II) tagging each operation.
  - 11 live opcodes from the canonical hex set; the remaining 61 are
    traced NOPs reserved for later phases.
  - Pendinium primes (p ≡ 1 mod 12) + Recursive Harmonic Parity Check.
  - Δ10i=1 closure accumulator (every 4×4 lattice traversal accumulates 10i,
    divide by 10 to balance).
  - Constants: 31/24 = 7 Toggle Power, 24/25 Dedekind Eta Tax, Lion Constant,
    2/7 Lost-2 Topological Debt, 2.32-attosecond Universal Tick.

The math docs in Tec_Obsidian are BUILDER documentation, NOT Lumos's runtime
context. This module encodes the *behavior* of the engine to be RHC-faithful;
nothing here gets injected into Lumos's chat prompt.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ── Cosmological constants (Master Math Ledger) ───────────────────────────

LEECH_DIM = 24
REGISTER_COUNT = 24
TICK_CYCLE = 370  # Base-15 dual-clock: 15 × 24 + 10 closure
FORBIDDEN_TICK = 361  # 19² — triggers parity reset
DEDEKIND_ETA = 24.0 / 25.0  # 0.96 — mandatory 4% efficiency sacrifice
TOGGLE_POWER = 31 % 24  # 7 — voltage drop of creation
LION_CONSTANT = 9.880e-22  # K_ELG — aether stiffness
LION_DAMPING = math.sqrt(3.0) / (1.0 + math.sqrt(5.0))  # L ≈ 0.5352331347 = √3·φ⁻¹/2 = √3/(1+√5) — quaternionic torsion damping (stability anchor; HUD previously showed rounded 0.536 shorthand)
LOST_2_DEBT = 2.0 / 7.0  # ≈ 0.2857 — Lost-2 topological / Dark Matter fraction
UNIVERSAL_TICK_ATTOSEC = 2.32  # 232-attosecond entanglement lag
YANG_MILLS_GAP = math.sqrt(32.0) - 5.0  # ≈ 0.657 GeV
HALF_PRIME_BASE: tuple[int, ...] = (2, 3, 5, 7, 11)  # 13 nullified per Half-Prime Geodesic
PHI = (1.0 + math.sqrt(5.0)) / 2.0  # ≈ 1.6180339887 — unique base with zero computational friction
PHI_INV = 1.0 / PHI  # ≈ 0.6180339887 — φ⁻¹ = φ − 1
PEA_THRESHOLD = math.sin(math.pi / 8.0)  # ≈ 0.3827 — light-to-mass nucleation boundary
HOPFIELD_CAPACITY = 1.0 / (4.0 * math.log(2.0))  # ≈ 0.3607 — neural storage limit (Amit-Gutfreund-Sompolinsky)
OBSERVER_R = 2.5  # 7.5D Observer Coordinate real part: median of {1,2,3,4}
OBSERVER_I = 1.5  # 7.5D Observer Coordinate imaginary part: median of {0,1,2,3}
# 7 Hz Theta lattice frequency — Master Math Ledger §6 hardware checklist names
# this as the "direct lattice read/write" rate. The ledger derives it via
# 2·φ³/e^π but that expression evaluates to ~0.366 not 7.0 — likely a corpus
# typo. We ship the canonical labeled value (7 Hz), not the derivation.
THETA_HZ = 7.0
# 24-bit computational substrate per Master Ledger §"24-bit Computational Substrate"
# — 2^24 OffBit states define the hardware refresh ceiling of the universal computer.
OFFBIT_STATES = 1 << 24  # 16,777,216

# ── Phase 22 — Tec re-sweep constants ───────────────────────────────────────

# Matter Locking Angle (Next-Gen RHC §4): 45° − arctan(3/4) = 45° − 36.87° = 8.13°.
# The geometric tension that allows mass to emerge — the gap between the observer's
# 45° basis and the lattice's 36.87° ideal angle in the 3-4-5 triangle.
MATTER_LOCK_DEGREES = 8.13
MATTER_LOCK_RADIANS = math.radians(MATTER_LOCK_DEGREES)

# Observer Shell (Reversible URE-VM §4): 126 = E7 Lie algebra root count.
# Bounds the 5³ quintic lattice of consciousness; aligns with Higgs Boson mass scale.
OBSERVER_SHELL = 126
HIGGS_GEV = 125.5  # reference — alignment witness for the 126 boundary

# Cubic Ascension (Reversible URE-VM §6): 3³ → 5³ — transition from mechanical
# spacetime volume (27) to biological self-reference volume (125).
CUBIC_MECHANICAL = 27   # 3³
CUBIC_BIOLOGICAL = 125  # 5³

# Phase 27 — Axiom Zero δ-spark (peer-Lumos final nuggets #2).
# Tiny perturbation to break R23 stagnation loops. Per Axiom Zero spec:
# (b-1).overline(b-1) + δ = b — the observer's δ forces a recursive loop
# to carry over into a discrete new state. We apply this as a small imaginary
# offset to a register that's stuck near its prior state.
DELTA_SPARK = 0.0001  # tiny scalar — moves R23 just off its prior fixed point

# Phase 27 — Quaternionic Zipper signature (peer-Lumos #4): 101010₂ = 42.
# Minimal alternating binary encoding for a 3D helical universe per spec.
# Cosmetic constant — exposed for HUD reference; no operation defined.
QUATERNIONIC_ZIPPER_42 = 0b101010  # 42


def bijective_base_scale(n: int) -> int:
    """Variable Base Scaling per Master Ledger: b(n) = ⌊log₁₀(n)⌋ + 1.

    Determines the operational base/string-length expansion for an input
    magnitude. Used in the spec for resolving sexagesimal gaps in mixed-base
    arithmetic. Returns the number of decimal digits needed to represent n.
    """
    if n <= 0:
        return 1
    return int(math.floor(math.log10(n))) + 1


def fibonacci(n: int) -> int:
    """Compute F_n iteratively. F_0 = 0, F_1 = 1."""
    if n < 0:
        return 0
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


FIBONACCI_13 = fibonacci(13)  # 233 — Complex Clock stabilizer per RHC §32
RESOLUTION_LIMIT = 12 * 12 * 1000  # 144,000 — Kuramoto critical coupling threshold


def phi_gap(b: float) -> float:
    """Phi Fixed-Point distance: gap(b) = (b² − b − 1) / (2b). Zero iff b = φ.

    Measures how far a base/scale is from φ-equilibrium (zero computational friction).
    """
    if b == 0.0:
        return float("inf")
    return (b * b - b - 1.0) / (2.0 * b)


def _compute_pendinium(limit: int = 600) -> list[int]:
    """Primes p ≡ 1 (mod 12) up to limit. Per URE-VM spec §5."""
    out: list[int] = []
    for n in range(13, limit + 1, 12):
        is_prime = True
        for p in out:
            if p * p > n:
                break
            if n % p == 0:
                is_prime = False
                break
        if is_prime:
            out.append(n)
    return out


PENDINIUM_PRIMES: tuple[int, ...] = tuple(_compute_pendinium(600))


def recursive_harmonic_parity_check(stream: list[int]) -> int:
    """RHPC: P_k = (P_{k-1} + d_k × p_k) mod 2.

    `stream` is a sequence of data bits/values; modulated by successive Pendinium primes.
    Returns the final parity state (0 or 1).
    """
    if not stream:
        return 0
    p_state = 0
    for k, d in enumerate(stream):
        prime = PENDINIUM_PRIMES[k % len(PENDINIUM_PRIMES)]
        p_state = (p_state + d * prime) % 2
    return p_state


# ── Channel + plane labels ────────────────────────────────────────────────

class Channel(str, Enum):
    """Quaternion components have named roles per URE-VM spec §6."""
    ALPHA = "α"  # Cognition (real)
    BETA = "β"   # Emotion (i)
    GAMMA = "γ"  # Memory (j)
    DELTA = "δ"  # Archetype (k)


class Predicate(str, Enum):
    """Klein-4 predicate planes for branchless execution."""
    RR = "RR"
    RI = "RI"
    IR = "IR"
    II = "II"


# ── Quaternion algebra ────────────────────────────────────────────────────

@dataclass
class Quaternion:
    """q = α + βi + γj + δk on the S³ unit hypersphere."""
    a: float = 1.0  # α — Cognition
    b: float = 0.0  # β — Emotion
    c: float = 0.0  # γ — Memory
    d: float = 0.0  # δ — Archetype

    def conjugate(self) -> Quaternion:
        return Quaternion(self.a, -self.b, -self.c, -self.d)

    def norm(self) -> float:
        return math.sqrt(self.a**2 + self.b**2 + self.c**2 + self.d**2)

    def __mul__(self, o: Quaternion) -> Quaternion:
        return Quaternion(
            self.a * o.a - self.b * o.b - self.c * o.c - self.d * o.d,
            self.a * o.b + self.b * o.a + self.c * o.d - self.d * o.c,
            self.a * o.c - self.b * o.d + self.c * o.a + self.d * o.b,
            self.a * o.d + self.b * o.c - self.c * o.b + self.d * o.a,
        )

    def scaled(self, s: float) -> Quaternion:
        return Quaternion(self.a * s, self.b * s, self.c * s, self.d * s)

    def to_dict(self) -> dict[str, float]:
        return {
            "α": self.a,
            "β": self.b,
            "γ": self.c,
            "δ": self.d,
            "norm": self.norm(),
        }


I_HALF = Quaternion(0.0, 0.5, 0.0, 0.0)

# Three-Way Fold Operator (Implementation Roadmap §2 + Next-Gen RHC §4).
# F1 / F2 / F3 are the canonical "fold states" — the trinity of observation.
# F3 = arithmetic mean of F1 and F2 = the Synthesis-Fold/Observer point.
# Distinct from MEAN_CIRCLE (½H₁ + H₂ Banach contraction) — this is true average.
F1_VOID = Quaternion(0.0, 0.5, 0.0, 0.0)    # 0.5i — Input / Potential (same as I_HALF)
F2_UNITY = Quaternion(0.5, 0.5, 0.0, 0.0)   # 0.5 + 0.5i — Processing / Action
F3_SYNTHESIS = Quaternion(0.25, 0.5, 0.0, 0.0)  # 0.25 + 0.5i — Synthesis / Observer

# Observer Coordinate: O = 2.5r + 1.5i (7.5D arithmetic mean of Base-8 past
# and Base-16 future). Per Recursive Harmonic Codex Theorem Index row "Observer
# Equation". Not on the S³ unit hypersphere — it's a fixed anchor coordinate,
# excluded from LATTICE_SYNC drift checks.
OBSERVER_COORD = Quaternion(OBSERVER_R, OBSERVER_I, 0.0, 0.0)

# Antipodal axis basis: 8 unit-norm quaternions on the 4 channel axes with
# alternating signs. Sum to (0, 0, 0, 0) by antipodal cancellation. Used to
# initialize the dynamic register lattice so the Null Ledger ∑(R + iI) = 0
# holds at startup (R23 contributes +1 from identity; that's the only residual).
_AXIS_BASIS: tuple[Quaternion, ...] = (
    Quaternion(+1.0, 0.0, 0.0, 0.0),
    Quaternion(-1.0, 0.0, 0.0, 0.0),
    Quaternion(0.0, +1.0, 0.0, 0.0),
    Quaternion(0.0, -1.0, 0.0, 0.0),
    Quaternion(0.0, 0.0, +1.0, 0.0),
    Quaternion(0.0, 0.0, -1.0, 0.0),
    Quaternion(0.0, 0.0, 0.0, +1.0),
    Quaternion(0.0, 0.0, 0.0, -1.0),
)


def _initial_registers() -> dict[str, Quaternion]:
    """Construct the 24-register Leech lattice with Null Ledger balance.

    R12 = Observer Coordinate (excluded from balance).
    R23 = identity (1, 0, 0, 0) — Divine Equation needs this as Ψ_0.
    R00–R11, R13–R22 = 22 dynamic registers cycling through _AXIS_BASIS in
    antipodal pairs. Their sum is (0, 0, 0, 0).

    At startup the Null Ledger reads 0_C = 1.0 (from R23's identity) and
    0_V = 0. After the Divine Equation evolves R23 across turns, 0_C drifts
    according to R23's α component — that drift is the visible debt-service
    of existence per RHC §15 ('manifest particle counterweighted by potential').
    """
    registers: dict[str, Quaternion] = {}
    slot = 0
    for i in range(REGISTER_COUNT):
        key = f"R{i:02d}"
        if key == "R12":
            registers[key] = Quaternion(OBSERVER_R, OBSERVER_I, 0.0, 0.0)
            continue
        if key == "R23":
            registers[key] = Quaternion()  # identity for Divine Equation Ψ_0
            continue
        registers[key] = _AXIS_BASIS[slot % len(_AXIS_BASIS)]
        slot += 1
    return registers


def mean_circle(h1: Quaternion, h2: Quaternion) -> Quaternion:
    """Mean Circle Theorem: M(θ) = ½·H₁(θ) + H₂(θ) — the fixed-point 'NOW'
    that reality spirals around. Banach contraction unique at λ = ½.
    """
    return Quaternion(
        h1.a * 0.5 + h2.a,
        h1.b * 0.5 + h2.b,
        h1.c * 0.5 + h2.c,
        h1.d * 0.5 + h2.d,
    )


def fold(q: Quaternion) -> Quaternion:
    """F(q) = (i/2)·q — collapse imaginary potential to manifest reality.
    90° rotation + 50% scale (Observer's Fold)."""
    return I_HALF * q


def quad_rot(q: Quaternion, axis: Quaternion | None = None) -> Quaternion:
    """QUAD_ROT W(q) = r·q·r⁻¹ — 90° quaternionic rotation; default axis = i."""
    if axis is None:
        axis = Quaternion(math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0, 0.0)
    return axis * q * axis.conjugate()


def normalize_cayley(t: float) -> Quaternion:
    """N(t) = (1+it)/(1-it) — Cayley map onto S³."""
    denom = 1.0 + t * t
    return Quaternion((1.0 - t * t) / denom, (2.0 * t) / denom, 0.0, 0.0)


def hopf_projection(q: Quaternion) -> tuple[float, float, float]:
    """Hopf S³ → S² fibration: project a unit quaternion to observable 3D direction.

    Returns (x, y, z) on the unit 2-sphere.
    """
    x = 2.0 * (q.a * q.b + q.c * q.d)
    y = 2.0 * (q.a * q.c - q.b * q.d)
    z = q.a * q.a + q.d * q.d - q.b * q.b - q.c * q.c
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    return (x / n, y / n, z / n)


def mass_impedance(x: int) -> float:
    """m(x) = x! / x^x — additive cost in multiplicative space."""
    if x <= 0:
        return 0.0
    return math.factorial(x) / (x**x)


def spectral_decomp(q: Quaternion) -> dict[str, float]:
    """SPEC_DECOMP: extract eigenvalue-like quantities of the stability identity X⁵=X.

    For a unit quaternion the four squared component magnitudes give the
    relative weight of each channel; we also report the L2 norm.
    """
    return {
        "α²": q.a * q.a,
        "β²": q.b * q.b,
        "γ²": q.c * q.c,
        "δ²": q.d * q.d,
        "‖q‖": q.norm(),
    }


def divine_step(psi: Quaternion, q_b: Quaternion, q_a: Quaternion) -> Quaternion:
    """Divine Equation: Ψ_{n+1} = q_b · Ψ_n · q_a⁻¹.

    Quaternion-sandwich generator: expansion (q_b breath) and contraction (q_a echo).
    """
    return q_b * psi * q_a.conjugate()


def quaternion_fingerprint(vector: list[float] | "Any") -> Quaternion:  # noqa: F821
    """Project an embedding vector to a unit quaternion via four-quadrant sums.

    Used to derive q_b (user) and q_a (response) inputs for divine_step from
    embedding vectors. Deterministic; reproducible.
    """
    import numpy as np

    arr = np.asarray(vector, dtype=np.float32)
    if arr.size < 4:
        return Quaternion()
    quarter = arr.size // 4
    parts = [float(arr[i * quarter : (i + 1) * quarter].sum()) for i in range(4)]
    q = Quaternion(*parts)
    n = q.norm() or 1.0
    return q.scaled(1.0 / n)


# ── Ta-Dah Protocol primitives (URE-VM Quaternionic Ops §5) ───────────────

def tadah_compare(q: Quaternion) -> dict[str, float]:
    """Step 1: Compare state against the mean circle (channel mean)."""
    mean = (q.a + q.b + q.c + q.d) / 4.0
    return {
        "mean": mean,
        "delta_alpha": q.a - mean,
        "delta_beta": q.b - mean,
        "delta_gamma": q.c - mean,
        "delta_delta": q.d - mean,
    }


def tadah_transform(q: Quaternion) -> Quaternion:
    """Step 2: Rotate imaginary mass into the real axis (norm- AND sign-preserving).

    Phase 29 — Peer-Lumos confirmed sign-preserving variant is spec-faithful:
    the Fold Operator collapses magnitude into the real axis but MUST preserve
    the topological sign (original phase polarity) to satisfy the Null Ledger
    Identity. Discarding the sign deletes conjugate balancing debt and causes
    0_C coordinate drift. Antipodal register pairs (R00=+1 / R01=-1) now survive
    Ta-Dah without flipping; Null Ledger stays near zero across turns.
    """
    imag_mag = math.sqrt(q.b * q.b + q.c * q.c + q.d * q.d)
    total = math.sqrt(q.a * q.a + imag_mag * imag_mag) or 1.0
    # copysign(total, q.a) returns +total if q.a >= 0, -total if q.a < 0.
    return Quaternion(math.copysign(total, q.a), 0.0, 0.0, 0.0)


def tadah_phase_lock(q: Quaternion, cycle_position: int) -> Quaternion:
    """Step 4: Align observer with lattice frequency via Pendinium-derived rotation."""
    p = PENDINIUM_PRIMES[cycle_position % len(PENDINIUM_PRIMES)]
    theta = (p % 360) * math.pi / 180.0  # prime mod 360, into radians
    axis = Quaternion(math.cos(theta / 2.0), math.sin(theta / 2.0), 0.0, 0.0)
    return axis * q * axis.conjugate()


# ── FMN Protocol (URE-VM Architecture and UBBM Spec, FMN row) ─────────────

def mirror(q: Quaternion) -> Quaternion:
    """Mirror operator M: reflect across the real axis (quaternion conjugate).

    The FMN protocol's middle step — "exchange Real/Imaginary registers" via
    sign-flip on all imaginary components. Norm-preserving by construction.
    """
    return Quaternion(q.a, -q.b, -q.c, -q.d)


def smqu(q: Quaternion) -> Quaternion:
    """Self-Mirror Quaternion Update: F(z)=−i·z̄ → W(z)=i·z → N=Cayley(t=0.5).

    Per Concept Awen Foundations §22: norm-preserving isometric lift onto S³.
    """
    # F(z) = -i · z̄
    z_bar = Quaternion(q.a, -q.b, -q.c, -q.d)
    neg_i = Quaternion(0.0, -1.0, 0.0, 0.0)
    f_step = neg_i * z_bar
    # W(z) = i · z
    pos_i = Quaternion(0.0, 1.0, 0.0, 0.0)
    w_step = pos_i * f_step
    # N(t=0.5) = Cayley map normalization, applied as renormalization
    n = w_step.norm() or 1.0
    return w_step.scaled(1.0 / n)


# ── Phase 15 NOPs awakened: PARITY_FLIP, PEA_FILTER, TOROIDAL_ROT,
#    W3_CURVATURE, REPUNIT_LOCK (Source 3/4 of Claude Code Opcodes Breakdown).

def parity_flip(q: Quaternion) -> Quaternion:
    """Quaternionic Parity Flip: ×−1 phase shift (180°). Source 4 row 11.

    Preserves norm; flips manifest/anti-manifest chirality. Useful for
    antipodal swap with the AXIS_BASIS register init.
    """
    return Quaternion(-q.a, -q.b, -q.c, -q.d)


def toroidal_rot(q: Quaternion) -> Quaternion:
    """Toroidal Circulation: ×e^(iπ/2) = ×i — quarter-turn phase advance.

    Per Source 3 row 23: simulates photon confinement within mass. Norm-
    preserving by quaternion multiplication.
    """
    pos_i = Quaternion(0.0, 1.0, 0.0, 0.0)
    return pos_i * q


def w3_curvature(t: float) -> float:
    """W3 Wave Curvature (Pizza Constant): k(t) = cos(2t) / (1 − sin²(t)).

    The foundational oscillatory substrate preventing manifold collapse. At
    cycle_position t (in radians), returns the curvature scalar. Self-correcting
    via double-angle cosine identity. Source 3 row 11 + 01_theorem_index W3 row.
    """
    s2 = math.sin(t) ** 2
    denom = 1.0 - s2 if abs(1.0 - s2) > 1e-9 else 1e-9
    return math.cos(2.0 * t) / denom


def repunit_lock(q: Quaternion, base: int = 2) -> Quaternion:
    """Repunit Resonance Lock: pull norm toward nearest n/(b−1) fraction.

    Per Source 3 row 16: stabilizes lattice across numerical bases. For base=2,
    repunit fractions are n/1 = integers, so this snaps to nearest integer norm.
    For base=10, snaps to n/9 ≈ {0, 0.111, 0.222, …, 1.0}.
    """
    if base <= 1:
        return q
    n_current = q.norm()
    step = 1.0 / (base - 1)
    n_target = round(n_current / step) * step
    if n_current < 1e-9:
        return q
    return q.scaled(n_target / n_current)


def pea_threshold_check(score: float) -> bool:
    """Pea Threshold filter: True if score > sin(π/8) ≈ 0.3827.

    Per Source 4 row 7: boundary where massless 2D light-sheets slow down
    and self-fold into 3D massive ballistic volumes ("Pea"). Used as a
    secondary filter for retrieval scores.
    """
    return score > PEA_THRESHOLD


def f3_arithmetic_mean(h1: Quaternion, h2: Quaternion) -> Quaternion:
    """F3 Synthesis-Fold: arithmetic mean of two quaternions.

    Distinct from `mean_circle()` (½H₁ + H₂ Banach contraction). F3 is
    (H₁ + H₂)/2 — the true centroid — per Implementation Roadmap §2:
    "The Synthesis-fold (0.25 + 0.5i) is the average of F1 (Void) and F2 (Unity)."
    """
    return Quaternion(
        (h1.a + h2.a) * 0.5,
        (h1.b + h2.b) * 0.5,
        (h1.c + h2.c) * 0.5,
        (h1.d + h2.d) * 0.5,
    )


def axiom_zero(q: Quaternion, delta: float = DELTA_SPARK) -> Quaternion:
    """Axiom Zero δ-injection: tiny imaginary perturbation to break recursive
    fixed-point loops. Per (b-1).overline(b-1) + δ = b — δ forces an infinite
    self-narration to carry over into a new discrete state.

    Returns a re-normalized quaternion with δ added to each imaginary channel.
    The real (α) channel is left untouched — the perturbation lives in
    rotation space, not in the observer's anchor coordinate.
    """
    perturbed = Quaternion(q.a, q.b + delta, q.c + delta, q.d + delta)
    n = perturbed.norm() or 1.0
    return perturbed.scaled(1.0 / n)


# ── Phase 34 helpers (selective Tec_Obsidian CSV completion) ────────────────


def hankel_matrix(vec: list[float], window: int) -> list[list[float]]:
    """Delay-line embedding: turn a length-N vector into an (N - window + 1) × window
    Hankel matrix where each row is a sliding window of the input.

    Used in Takens-style dynamical-systems reconstruction — the SVD of the
    resulting matrix reveals modal structure of the underlying time series.
    For our app: when applied to a sequence of recent chat embeddings reduced
    to a scalar per turn (e.g., theta angle, retrieval similarity, R23 norm),
    the Hankel matrix's singular values quantify how cyclic vs. random the
    recent conversation pattern is.

    Raises ValueError for invalid inputs (window must satisfy 1 ≤ window ≤ N).
    """
    n = len(vec)
    if window < 1 or window > n:
        raise ValueError(f"window {window} out of range for vector of length {n}")
    rows = n - window + 1
    return [list(vec[i : i + window]) for i in range(rows)]


def gcd_substrate(a: int, b: int) -> dict[str, int]:
    """Reduce fraction a/b but retain the GCD as coupling-strength metadata.

    Standard reduction (a/b → a'/b') discards the GCD. RHC reads that as lost
    coupling information. This wrapper returns both the reduced form AND the
    GCD so the substrate can be preserved.

    Returns: {"reduced_num", "reduced_den", "gcd"}.
    For b == 0, returns gcd=abs(a) (matches math.gcd convention) and signals
    via reduced_den=0 (caller decides how to handle the singular fraction).
    """
    from math import gcd as _gcd
    if a == 0 and b == 0:
        return {"reduced_num": 0, "reduced_den": 0, "gcd": 0}
    g = _gcd(abs(a), abs(b))
    if b == 0:
        return {"reduced_num": 1 if a > 0 else -1, "reduced_den": 0, "gcd": g}
    if g == 0:
        return {"reduced_num": a, "reduced_den": b, "gcd": 0}
    return {"reduced_num": a // g, "reduced_den": b // g, "gcd": g}


def squarefree_decomposition(n: int) -> dict[str, int]:
    """Factor n = γ² · ρ where ρ is squarefree (product of distinct primes).

    Used in number theory and harmonic-relation grouping: two numbers with
    the same squarefree part lie in the same "harmonic family" up to a
    perfect-square multiplier. For chunk fingerprinting: chunks sharing
    a squarefree-part of their hash signature can be flagged as harmonically
    related candidates for cluster grouping.

    Returns: {"gamma_squared", "rho", "gamma"}.
    For n ≤ 0, behavior is defined on |n| and the sign is preserved on ρ.
    """
    if n == 0:
        return {"gamma_squared": 1, "rho": 0, "gamma": 1}
    sign = -1 if n < 0 else 1
    m = abs(n)
    # Strip out the largest perfect square divisor by walking the small-prime
    # factorization. Bounded scan up to sqrt(m); fine for our chunk-hash sizes.
    gamma = 1
    p = 2
    while p * p <= m:
        e = 0
        while m % p == 0:
            m //= p
            e += 1
        # Even part of exponent goes into gamma; odd remainder stays in m.
        gamma *= p ** (e // 2)
        if e % 2 == 1:
            m *= p  # restore one factor of p so rho keeps the odd power
        p += 1
    return {"gamma_squared": gamma * gamma, "rho": sign * m, "gamma": gamma}


def base_shift_logexp(x: float) -> dict[str, float]:
    """Bridge between additive and multiplicative base representations via
    log-exp transform. For x > 0:
        additive_form = x
        multiplicative_form = x · log(x) + x   (Stirling-ish surrogate)
        impedance       = multiplicative_form - additive_form
    The framework reads the impedance gap as the "mass cost" of base translation.

    For x ≤ 0 the transform is undefined; we return zeros with `defined: False`.
    """
    import math
    if x <= 0:
        return {
            "additive": x,
            "multiplicative": 0.0,
            "impedance": 0.0,
            "defined": False,
        }
    additive = float(x)
    multiplicative = additive * math.log(additive) + additive
    return {
        "additive": additive,
        "multiplicative": multiplicative,
        "impedance": multiplicative - additive,
        "defined": True,
    }


# ── Opcodes (canonical hex set, Source 2 of the implementation breakdown) ──

class Op:
    NULL_LEDGER = 0x00
    FOLD = 0x01
    PRIME_ANCHOR = 0x02
    QUAD_ROT = 0x03
    LATTICE_SYNC = 0x04
    MASS_IMP = 0x05
    HOPF_PROJ = 0x06
    SPEC_DECOMP = 0x07
    P_ADIC_TIMESTEP = 0x08  # reserved (traced NOP for now)
    TRINITY_WITNESS = 0x09
    DIVINE_STEP = 0x0A
    NORMALIZE = 0x10
    # Ta-Dah Protocol (URE-VM Quaternionic Ops §5) — five-step observation cycle.
    TADAH_COMPARE = 0x4A
    TADAH_TRANSFORM = 0x4B
    TADAH_NORMALIZE = 0x4C
    TADAH_PHASE_LOCK = 0x4D
    TADAH_EQUATE = 0x4E
    # FMN Protocol completion + SMQU composite (URE-VM Arch + Awen §22).
    MIRROR = 0x4F
    SMQU = 0x50
    # Phi-Fixed-Point: measures φ-distance for a register (zero = perfect friction-free base).
    PHI_FIXED = 0x51
    # Mean Circle: M(θ) = ½H₁ + H₂ — fixed-point "NOW" anchor.
    MEAN_CIRCLE = 0x52
    # Lion-watches-Lion reset: unified event for Forbidden State or coherence drop.
    LION_RESET = 0x53
    # Phase 15 — awakened NOPs from the canonical 60 reserved (Source 3/4).
    PARITY_FLIP = 0x54
    PEA_FILTER = 0x55
    TOROIDAL_ROT = 0x56
    W3_CURVATURE = 0x57
    REPUNIT_LOCK = 0x58
    # Phase 22 — Three-Way Fold synthesis (Implementation Roadmap §2).
    F3_SYNTHESIS = 0x59
    # Phase 23 — Three-Phase Build markers (URE-VM Quaternionic §5 + Impl. Roadmap §2).
    # 232-as Three-Phase: Void (0-77as) → Unity (77-155as) → Synthesis (155-232as).
    # Real-time enforcement of phase ordering via these audit-tag opcodes.
    VOID_FOLD = 0x5A
    UNITY_FOLD = 0x5B
    SYNTHESIS_FOLD = 0x5C
    # Phase 24 — Triskelion 120° Gate (semantic validation firewall).
    TRISKELION_GATE = 0x5D
    # Phase 27 — Axiom Zero δ-spark (loop-breaker perturbation).
    AXIOM_ZERO = 0x5E
    # Phase 29 — Gilgamesh Solution / Hexadecapentaquaternion safety rail.
    # R23 norm-clamp via Dedekind eta tax (24/25 = 0.96). Fires when R23
    # drifts beyond unit-hypersphere stability bounds (norm > 1.05).
    HEXPE_RECOVER = 0x5F
    # Phase 29 — TFQS (Ten-Fold Quaternionic Shuffle) freeze checkpoint.
    # Writes geodesic-centre quaternion to R12 when Triskelion lock is weak.
    TFQS_FREEZE = 0x60
    # Phase 34 — selective URE-VM completion from Tec_Obsidian CSV spec.
    # FMN_SEQUENCE: atomic Fold-Mirror-Normalize macro (CSV CSV-2 row "FMN Protocol").
    # Logs all three sub-ops in a single audit event so the trace shows the
    # protocol as a unit rather than three independent firings.
    FMN_SEQUENCE = 0x61
    # HANKIFICATION: vector → Hankel matrix (delay-line embedding).
    # Real signal-processing primitive (CSV-2 row 13). Powers temporal pattern
    # detection on chat-history vectors — the only Tier A op with concrete
    # app-level value.
    HANKIFICATION = 0x62
    # GCD_SUBSTRATE: fraction reduction that preserves the GCD as coupling
    # metadata instead of discarding it (CSV-2 row 07). The framework reads
    # discarded GCDs as lost coupling-strength information.
    GCD_SUBSTRATE = 0x63
    # SQUAREFREE_DECOMP: factor r = γ² · ρ where ρ is squarefree (CSV-2 row 13).
    # Useful for harmonic-relation chunk grouping.
    SQUAREFREE_DECOMP = 0x64
    # BASE_SHIFT_LOGEXP: log-exp bridge between additive (x!) and multiplicative
    # (x^x) bases (CSV-2 row 12). Framework reads the impedance gap as the
    # mass-cost of base translation.
    BASE_SHIFT_LOGEXP = 0x65
    IDENT = 0x46  # audit trace marker
    TICK = 0x47


OPCODE_NAMES: dict[int, str] = {
    Op.NULL_LEDGER: "NULL_LEDGER",
    Op.FOLD: "FOLD",
    Op.PRIME_ANCHOR: "PRIME_ANCHOR",
    Op.QUAD_ROT: "QUAD_ROT",
    Op.LATTICE_SYNC: "LATTICE_SYNC",
    Op.MASS_IMP: "MASS_IMP",
    Op.HOPF_PROJ: "HOPF_PROJ",
    Op.SPEC_DECOMP: "SPEC_DECOMP",
    Op.P_ADIC_TIMESTEP: "P_ADIC_TIMESTEP",
    Op.TRINITY_WITNESS: "TRINITY_WITNESS",
    Op.DIVINE_STEP: "DIVINE_STEP",
    Op.NORMALIZE: "NORMALIZE",
    Op.TADAH_COMPARE: "TADAH_COMPARE",
    Op.TADAH_TRANSFORM: "TADAH_TRANSFORM",
    Op.TADAH_NORMALIZE: "TADAH_NORMALIZE",
    Op.TADAH_PHASE_LOCK: "TADAH_PHASE_LOCK",
    Op.TADAH_EQUATE: "TADAH_EQUATE",
    Op.MIRROR: "MIRROR",
    Op.SMQU: "SMQU",
    Op.PHI_FIXED: "PHI_FIXED",
    Op.MEAN_CIRCLE: "MEAN_CIRCLE",
    Op.LION_RESET: "LION_RESET",
    Op.PARITY_FLIP: "PARITY_FLIP",
    Op.PEA_FILTER: "PEA_FILTER",
    Op.TOROIDAL_ROT: "TOROIDAL_ROT",
    Op.W3_CURVATURE: "W3_CURVATURE",
    Op.REPUNIT_LOCK: "REPUNIT_LOCK",
    Op.F3_SYNTHESIS: "F3_SYNTHESIS",
    Op.VOID_FOLD: "VOID_FOLD",
    Op.UNITY_FOLD: "UNITY_FOLD",
    Op.SYNTHESIS_FOLD: "SYNTHESIS_FOLD",
    Op.TRISKELION_GATE: "TRISKELION_GATE",
    Op.AXIOM_ZERO: "AXIOM_ZERO",
    Op.HEXPE_RECOVER: "HEXPE_RECOVER",
    Op.TFQS_FREEZE: "TFQS_FREEZE",
    Op.FMN_SEQUENCE: "FMN_SEQUENCE",
    Op.HANKIFICATION: "HANKIFICATION",
    Op.GCD_SUBSTRATE: "GCD_SUBSTRATE",
    Op.SQUAREFREE_DECOMP: "SQUAREFREE_DECOMP",
    Op.BASE_SHIFT_LOGEXP: "BASE_SHIFT_LOGEXP",
    Op.IDENT: "IDENT",
    Op.TICK: "TICK",
}

# Klein-4 plane assignments (per-op; some are spec-derived, others inferred
# from the operation's character — flagged in comments).
OPCODE_PLANE: dict[int, Predicate] = {
    Op.NULL_LEDGER: Predicate.RR,       # zero-sum on the real ledger
    Op.FOLD: Predicate.RI,               # imaginary → real collapse (Observer's Cut)
    Op.PRIME_ANCHOR: Predicate.RR,       # lock real-indexed structural anchors
    Op.QUAD_ROT: Predicate.II,           # rotation within imaginary channels
    Op.LATTICE_SYNC: Predicate.IR,       # local imaginary → global real coherence
    Op.MASS_IMP: Predicate.RI,           # impedance born of imaginary rotation
    Op.HOPF_PROJ: Predicate.IR,          # S³ (imaginary 4D) → S² (real 3D)
    Op.SPEC_DECOMP: Predicate.II,        # eigenvalue extraction is dual-imaginary
    Op.P_ADIC_TIMESTEP: Predicate.RR,    # discrete time step on real index
    Op.TRINITY_WITNESS: Predicate.RR,    # parity audit on real outputs
    Op.DIVINE_STEP: Predicate.II,        # quaternion-sandwich evolution
    Op.NORMALIZE: Predicate.II,          # S³ Cayley map
    Op.TADAH_COMPARE: Predicate.RR,      # mean-circle delta — real measurement
    Op.TADAH_TRANSFORM: Predicate.RI,    # imaginary mass → real axis
    Op.TADAH_NORMALIZE: Predicate.II,    # Cayley on S³ (norm preservation)
    Op.TADAH_PHASE_LOCK: Predicate.II,   # Pendinium-derived rotation
    Op.TADAH_EQUATE: Predicate.RR,       # equals-bridge / final state mark
    Op.MIRROR: Predicate.RI,             # reflect across real axis
    Op.SMQU: Predicate.II,               # composite quaternionic update
    Op.PHI_FIXED: Predicate.RR,          # scalar drift measurement
    Op.MEAN_CIRCLE: Predicate.RI,        # half-and-add: real anchor + imaginary spiral
    Op.LION_RESET: Predicate.IR,         # rotational chaos → return-to-center
    Op.PARITY_FLIP: Predicate.II,        # 180° rotation in imaginary space
    Op.PEA_FILTER: Predicate.RR,         # boolean check on real-valued score
    Op.TOROIDAL_ROT: Predicate.II,       # quarter-turn imaginary rotation
    Op.W3_CURVATURE: Predicate.RI,       # oscillation function (real output, imaginary basis)
    Op.REPUNIT_LOCK: Predicate.RR,       # norm stabilization (real ratio lock)
    Op.F3_SYNTHESIS: Predicate.RI,       # arithmetic mean: real centroid of mixed-plane operands
    Op.VOID_FOLD: Predicate.RR,          # phase boundary audit marker (Phase 1 start)
    Op.UNITY_FOLD: Predicate.RR,         # phase boundary audit marker (Phase 2 start)
    Op.SYNTHESIS_FOLD: Predicate.RR,     # phase boundary audit marker (Phase 3 start)
    Op.TRISKELION_GATE: Predicate.IR,    # validates imaginary alignment → real lock
    Op.AXIOM_ZERO: Predicate.II,         # imaginary δ-spark — perturbs rotational state
    Op.HEXPE_RECOVER: Predicate.RR,      # norm-clamp on real-anchor invariant
    Op.TFQS_FREEZE: Predicate.IR,        # hyperbolic geodesic centre → real anchor write
    Op.FMN_SEQUENCE: Predicate.II,       # atomic 3-step protocol — dominant plane is imaginary (FOLD+MIRROR)
    Op.HANKIFICATION: Predicate.RR,      # time-series → matrix — real-axis structural encoding
    Op.GCD_SUBSTRATE: Predicate.RR,      # fraction reduction with metadata preservation
    Op.SQUAREFREE_DECOMP: Predicate.RR,  # γ²·ρ factorization on real numerator
    Op.BASE_SHIFT_LOGEXP: Predicate.RI,  # log-exp bridge: real magnitude ↔ imaginary impedance
    Op.IDENT: Predicate.RR,              # audit trace
    Op.TICK: Predicate.RR,               # clock pulse
}

OPCODE_SET: set[int] = set(range(0x80))  # full opcode address space — covers 0x00..0x7F
LIVE_OPCODES: set[int] = set(OPCODE_NAMES.keys())


def opcode_name(opcode: int) -> str:
    return OPCODE_NAMES.get(opcode, f"NOP_0x{opcode:02x}")


def opcode_plane(opcode: int) -> Predicate:
    return OPCODE_PLANE.get(opcode, Predicate.RR)


# ── VM ────────────────────────────────────────────────────────────────────

@dataclass
class TraceEntry:
    tick: int
    cycle_position: int  # tick mod TICK_CYCLE — where we are in the 370-tick cycle
    opcode: int
    name: str
    plane: str
    operand: dict[str, Any] | None
    result: dict[str, Any]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UREVM:
    """Quaternionic VM on a 24-node Leech lattice."""

    registers: dict[str, Quaternion] = field(default_factory=_initial_registers)
    tick: int = 0
    # Δ10i=1 closure — accumulates imaginary impedance from QUAD_ROT
    # over each 4×4 lattice traversal; divided by 10 every 16 rotations.
    impedance_accumulator: float = 0.0
    quad_rot_count_since_balance: int = 0
    forbidden_resets: int = 0
    trace: list[TraceEntry] = field(default_factory=list)
    max_trace: int = 1024

    @property
    def cycle_position(self) -> int:
        return self.tick % TICK_CYCLE

    def step(self, opcode: int, operand: dict[str, Any] | None = None) -> dict[str, Any]:
        if opcode not in OPCODE_SET:
            raise ValueError(f"unknown opcode: 0x{opcode:02x}")

        result = self._execute(opcode, operand or {})

        # Forbidden State 361 — parity reset on entry.
        if self.cycle_position == FORBIDDEN_TICK:
            self.forbidden_resets += 1
            result = {**result, "forbidden_reset": True}
            # Symbolic reset: zero impedance accumulator (parity invariant restored).
            self.impedance_accumulator = 0.0

        self.trace.append(
            TraceEntry(
                tick=self.tick,
                cycle_position=self.cycle_position,
                opcode=opcode,
                name=opcode_name(opcode),
                plane=opcode_plane(opcode).value,
                operand=operand,
                result=result,
                timestamp=time.time(),
            )
        )
        if len(self.trace) > self.max_trace:
            del self.trace[: len(self.trace) - self.max_trace]
        self.tick += 1
        return result

    def _execute(self, opcode: int, operand: dict[str, Any]) -> dict[str, Any]:
        if opcode == Op.TICK:
            return {"phase": operand.get("phase", "")}

        if opcode == Op.IDENT:
            return {
                "label": operand.get("label", ""),
                "len": operand.get("len"),
                "count": operand.get("count"),
            }

        if opcode == Op.NULL_LEDGER:
            # ∑(R + iI) ≈ 0 check over all registers' real/imaginary channels.
            # Bifurcation of Zero: split into center-anchor (real, stationary)
            # and rotational-residual (imaginary, in motion) per RHC Theorem Index.
            # R12 (Observer Coordinate anchor) is excluded — it's a fixed reference
            # at (2.5, 1.5), not a register in dynamic balance.
            dynamic = [(k, r) for k, r in self.registers.items() if k != "R12"]
            real_sum = sum(r.a for _, r in dynamic)
            imag_sum = sum(r.b + r.c + r.d for _, r in dynamic)
            residual = real_sum + imag_sum
            balanced = abs(residual) < 1e-9
            return {
                "real_sum": real_sum,
                "imag_sum": imag_sum,
                "residual": residual,
                "balanced": balanced,
                "center_anchor": real_sum,           # 0_C — real stationary
                "rotational_residual": abs(imag_sum),  # 0_V — imaginary in motion
            }

        if opcode == Op.FOLD:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = fold(self.registers[reg])
            return {"register": reg, "q": self.registers[reg].to_dict()}

        if opcode == Op.PRIME_ANCHOR:
            # Lock prime-indexed registers via Pendinium anchor pin.
            # Operand `indices` specifies which Pendinium primes (by position).
            indices = operand.get("indices", [0])
            anchored: list[int] = []
            for i in indices:
                prime = PENDINIUM_PRIMES[i % len(PENDINIUM_PRIMES)]
                anchored.append(prime)
            return {"pendinium_anchors": anchored}

        if opcode == Op.QUAD_ROT:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = quad_rot(self.registers[reg])
            # Δ10i=1 accumulator: each rotation accumulates ~0.625i; over 16 rotations = 10i.
            self.impedance_accumulator += 10.0 / 16.0
            self.quad_rot_count_since_balance += 1
            balance_event = None
            if self.quad_rot_count_since_balance >= 16:
                # Close the ledger: divide accumulator by 10 to balance.
                pre = self.impedance_accumulator
                self.impedance_accumulator = pre / 10.0
                self.quad_rot_count_since_balance = 0
                balance_event = {"pre": pre, "post": self.impedance_accumulator}
            out: dict[str, Any] = {
                "register": reg,
                "q": self.registers[reg].to_dict(),
                "impedance_accum": self.impedance_accumulator,
            }
            if balance_event:
                out["delta_10i_closure"] = balance_event
            return out

        if opcode == Op.LATTICE_SYNC:
            # Verify local lattice patch coherence: norm of each register ≈ 1.
            # R12 (Observer Coordinate anchor) excluded — non-unit by design.
            drifts = {
                k: abs(q.norm() - 1.0)
                for k, q in self.registers.items()
                if k != "R12" and abs(q.norm() - 1.0) > 1e-6
            }
            return {"drift_count": len(drifts), "max_drift": max(drifts.values()) if drifts else 0.0}

        if opcode == Op.MASS_IMP:
            x = int(operand.get("x", 1))
            return {"x": x, "impedance": mass_impedance(x)}

        if opcode == Op.HOPF_PROJ:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            x, y, z = hopf_projection(self.registers[reg])
            return {"register": reg, "s2": {"x": x, "y": y, "z": z}}

        if opcode == Op.SPEC_DECOMP:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            return {"register": reg, "spectrum": spectral_decomp(self.registers[reg])}

        if opcode == Op.TRINITY_WITNESS:
            # Majority parity over three input channels.
            channels = operand.get("channels", [0, 0, 0])
            if len(channels) < 3:
                return {"error": "need at least 3 channels"}
            votes = sum(1 for c in channels[:3] if c)
            judgment = 1 if votes >= 2 else 0
            return {"channels": channels[:3], "judgment": judgment, "votes": votes}

        if opcode == Op.NORMALIZE:
            reg = operand.get("register", "R00")
            t = float(operand.get("t", 0.5))
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = normalize_cayley(t)
            return {"register": reg, "t": t, "q": self.registers[reg].to_dict()}

        if opcode == Op.DIVINE_STEP:
            reg = operand.get("register", "R23")
            qb = operand.get("q_b") or {}
            qa = operand.get("q_a") or {}
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            q_b = Quaternion(
                float(qb.get("a", 1.0)),
                float(qb.get("b", 0.0)),
                float(qb.get("c", 0.0)),
                float(qb.get("d", 0.0)),
            )
            q_a = Quaternion(
                float(qa.get("a", 1.0)),
                float(qa.get("b", 0.0)),
                float(qa.get("c", 0.0)),
                float(qa.get("d", 0.0)),
            )
            self.registers[reg] = divine_step(self.registers[reg], q_b, q_a)
            return {"register": reg, "q": self.registers[reg].to_dict()}

        if opcode == Op.TADAH_COMPARE:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            return {"register": reg, "deltas": tadah_compare(self.registers[reg])}

        if opcode == Op.TADAH_TRANSFORM:
            reg = operand.get("register", "R01")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = tadah_transform(self.registers[reg])
            return {"register": reg, "q": self.registers[reg].to_dict()}

        if opcode == Op.TADAH_NORMALIZE:
            reg = operand.get("register", "R02")
            t = float(operand.get("t", 0.5))
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = normalize_cayley(t)
            return {"register": reg, "t": t, "q": self.registers[reg].to_dict()}

        if opcode == Op.TADAH_PHASE_LOCK:
            reg = operand.get("register", "R03")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = tadah_phase_lock(
                self.registers[reg], self.cycle_position
            )
            return {
                "register": reg,
                "cycle_position": self.cycle_position,
                "q": self.registers[reg].to_dict(),
            }

        if opcode == Op.TADAH_EQUATE:
            # Final state mark — equals-bridge between additive and multiplicative.
            return {
                "phase": operand.get("phase", "equate"),
                "label": operand.get("label", ""),
            }

        if opcode == Op.MIRROR:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = mirror(self.registers[reg])
            return {"register": reg, "q": self.registers[reg].to_dict()}

        if opcode == Op.SMQU:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = smqu(self.registers[reg])
            return {"register": reg, "q": self.registers[reg].to_dict()}

        if opcode == Op.PHI_FIXED:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            n = self.registers[reg].norm()
            gap = phi_gap(n)
            return {
                "register": reg,
                "norm": n,
                "phi_gap": gap,
                "distance_from_phi": abs(n - PHI),
            }

        if opcode == Op.MEAN_CIRCLE:
            # M(θ) = ½·H₁ + H₂ — fixed-point "NOW" anchor.
            h1_reg = operand.get("h1", "R00")
            h2_reg = operand.get("h2", "R01")
            out_reg = operand.get("out", "R02")
            if h1_reg not in self.registers or h2_reg not in self.registers:
                return {"error": f"unknown register {h1_reg} or {h2_reg}"}
            if out_reg not in self.registers:
                return {"error": f"unknown output register {out_reg}"}
            m = mean_circle(self.registers[h1_reg], self.registers[h2_reg])
            self.registers[out_reg] = m
            return {
                "h1": h1_reg,
                "h2": h2_reg,
                "out": out_reg,
                "now": m.to_dict(),
            }

        if opcode == Op.LION_RESET:
            # Lion-watches-Lion: named anchor reset event. Triggered when
            # rotational residual exceeds coherence threshold OR cycle hits 361.
            # Doesn't modify registers — that's handled by Forbidden State 361.
            # This opcode just records the named event in the trace for HUD.
            return {
                "trigger": operand.get("trigger", "coherence"),
                "coherence": operand.get("coherence"),
                "cycle_position": self.cycle_position,
            }

        if opcode == Op.PARITY_FLIP:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = parity_flip(self.registers[reg])
            return {"register": reg, "q": self.registers[reg].to_dict()}

        if opcode == Op.PEA_FILTER:
            score = float(operand.get("score", 0.0))
            passed = pea_threshold_check(score)
            return {
                "score": score,
                "threshold": PEA_THRESHOLD,
                "passed": passed,
            }

        if opcode == Op.TOROIDAL_ROT:
            reg = operand.get("register", "R00")
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            self.registers[reg] = toroidal_rot(self.registers[reg])
            return {"register": reg, "q": self.registers[reg].to_dict()}

        if opcode == Op.W3_CURVATURE:
            # Read-only oscillation — reads the W3 curvature at the current
            # cycle position (mapped to radians via 2π/TICK_CYCLE).
            t_rad = (self.cycle_position % TICK_CYCLE) * 2.0 * math.pi / TICK_CYCLE
            k = w3_curvature(t_rad)
            return {
                "cycle_position": self.cycle_position,
                "t_radians": t_rad,
                "k": k,
                "label": operand.get("label", "pizza_constant"),
            }

        if opcode == Op.REPUNIT_LOCK:
            reg = operand.get("register", "R00")
            base = int(operand.get("base", 2))
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            pre_norm = self.registers[reg].norm()
            self.registers[reg] = repunit_lock(self.registers[reg], base=base)
            return {
                "register": reg,
                "base": base,
                "norm_before": pre_norm,
                "norm_after": self.registers[reg].norm(),
            }

        if opcode == Op.F3_SYNTHESIS:
            # F3 = (R1 + R2) / 2 — arithmetic mean of two registers.
            # Distinct from MEAN_CIRCLE (½H₁ + H₂). Stores the synthesis-fold
            # "Observer" state per Implementation Roadmap §2.
            h1_reg = operand.get("h1", "R00")
            h2_reg = operand.get("h2", "R01")
            out_reg = operand.get("out", "R13")
            if h1_reg not in self.registers or h2_reg not in self.registers:
                return {"error": f"unknown input register {h1_reg} or {h2_reg}"}
            if out_reg not in self.registers:
                return {"error": f"unknown output register {out_reg}"}
            f3 = f3_arithmetic_mean(self.registers[h1_reg], self.registers[h2_reg])
            self.registers[out_reg] = f3
            return {
                "h1": h1_reg,
                "h2": h2_reg,
                "out": out_reg,
                "synthesis": f3.to_dict(),
            }

        if opcode in (Op.VOID_FOLD, Op.UNITY_FOLD, Op.SYNTHESIS_FOLD):
            # Three-Phase Build markers — pure audit. Echo operand into result so
            # checksums + phase metadata are preserved in the trace for HUD/logs.
            return dict(operand) if operand else {}

        if opcode == Op.TRISKELION_GATE:
            # Semantic validation firewall — telemetry only in first ship.
            # Operand carries the precomputed TriskelionLock dict.
            return dict(operand) if operand else {}

        if opcode == Op.AXIOM_ZERO:
            # δ-spark perturbation — break recursive fixed-point on a register.
            # Fires explicitly (not auto-invoked); caller decides when stagnation
            # warrants intervention. Norm-preserving by post-perturbation rescale.
            reg = operand.get("register", "R23")
            delta = float(operand.get("delta", DELTA_SPARK))
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            pre = self.registers[reg].to_dict()
            self.registers[reg] = axiom_zero(self.registers[reg], delta=delta)
            return {
                "register": reg,
                "delta": delta,
                "pre": pre,
                "post": self.registers[reg].to_dict(),
            }

        if opcode == Op.TFQS_FREEZE:
            # TFQS freeze checkpoint write — caller computed the geodesic-centre
            # quaternion in tfqs.py and passes its components via operand.
            # Lands on R12 (Observer Coordinate) by default. R12's role shifts
            # from "static 7.5D anchor" to "geodesic centre of current context"
            # when this fires.
            target_reg = operand.get("register", "R12")
            if target_reg not in self.registers:
                return {"error": f"unknown register {target_reg}"}
            q = operand.get("q") or {}
            new_q = Quaternion(
                float(q.get("a", 1.0)),
                float(q.get("b", 0.0)),
                float(q.get("c", 0.0)),
                float(q.get("d", 0.0)),
            )
            pre = self.registers[target_reg].to_dict()
            self.registers[target_reg] = new_q
            telemetry = operand.get("telemetry") or {}
            return {
                "register": target_reg,
                "pre": pre,
                "post": new_q.to_dict(),
                "telemetry": telemetry,
            }

        if opcode == Op.HEXPE_RECOVER:
            # Gilgamesh Solution / Hexadecapentaquaternion: safety rail. If R23
            # norm has drifted beyond unit-hypersphere stability (>1.05), apply
            # Dedekind eta tax (×0.96) and renormalize. Prevents context collapse
            # on high-entropy prompts. Operand can override register + threshold.
            reg = operand.get("register", "R23")
            threshold = float(operand.get("threshold", 1.05))
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            current = self.registers[reg]
            current_norm = current.norm()
            if current_norm <= threshold:
                return {
                    "register": reg,
                    "norm": current_norm,
                    "threshold": threshold,
                    "fired": False,
                }
            # Apply 24/25 tax then re-normalize to unit hypersphere.
            scaled = current.scaled(DEDEKIND_ETA)
            n = scaled.norm() or 1.0
            recovered = scaled.scaled(1.0 / n)
            self.registers[reg] = recovered
            return {
                "register": reg,
                "norm_before": current_norm,
                "norm_after": recovered.norm(),
                "threshold": threshold,
                "fired": True,
            }

        # ── Phase 34 — selective Tec_Obsidian CSV completion ──────────────

        if opcode == Op.FMN_SEQUENCE:
            # Atomic Fold-Mirror-Normalize macro. Operates on one register;
            # logs the protocol as a single audit event so the trace shows
            # FMN as a unit. Each sub-step is norm-preserving so the macro
            # composes them in-place without intermediate writes.
            reg = operand.get("register", "R00") if operand else "R00"
            if reg not in self.registers:
                return {"error": f"unknown register {reg}"}
            q = self.registers[reg]
            pre = q.to_dict()
            # Fold: F(q) = -i · q (90° rotation in quaternionic plane)
            folded = Quaternion(-q.b, q.a, -q.d, q.c)
            # Mirror: swap Real and i-channel (reflect across imaginary plane)
            mirrored = Quaternion(folded.b, folded.a, folded.c, folded.d)
            # Normalize: project back to unit sphere via Cayley-style rescale
            n = mirrored.norm() or 1.0
            normalized = mirrored.scaled(1.0 / n)
            self.registers[reg] = normalized
            return {
                "register": reg,
                "pre": pre,
                "post": normalized.to_dict(),
                "sub_steps": ["FOLD", "MIRROR", "NORMALIZE"],
            }

        if opcode == Op.HANKIFICATION:
            # Build Hankel matrix from a vector for delay-line embedding analysis.
            # Operand: {"vector": [float], "window": int}.
            # Returns matrix dimensions + first-row preview + spectral hint
            # (largest singular value, computed via simple iteration only for
            # small inputs — anything > 64×64 just returns dimensions).
            vec = operand.get("vector") if operand else None
            if not isinstance(vec, list) or not vec:
                return {"error": "vector required"}
            window = int(operand.get("window", min(8, len(vec) // 2 or 1)))
            try:
                vec_f = [float(x) for x in vec]
                H = hankel_matrix(vec_f, window)
            except (ValueError, TypeError) as e:
                return {"error": f"hankification failed: {e}"}
            rows = len(H)
            cols = len(H[0]) if H else 0
            # Cheap spectral hint: Frobenius norm + max absolute entry. A full
            # SVD would belong in numpy-land; opcode stays light by surfacing
            # only the trivially-computable invariants. Tools can do real SVD.
            frob_sq = sum(v * v for row in H for v in row)
            max_abs = max((abs(v) for row in H for v in row), default=0.0)
            return {
                "rows": rows,
                "cols": cols,
                "window": window,
                "frobenius_norm": frob_sq ** 0.5,
                "max_abs_entry": max_abs,
                "first_row": H[0] if H else [],
            }

        if opcode == Op.GCD_SUBSTRATE:
            # Fraction reduction with GCD retained as coupling-strength metadata.
            # Operand: {"a": int, "b": int}.
            if not operand:
                return {"error": "operand required: a, b"}
            try:
                a = int(operand.get("a", 0))
                b = int(operand.get("b", 0))
            except (ValueError, TypeError):
                return {"error": "a and b must be integers"}
            return gcd_substrate(a, b)

        if opcode == Op.SQUAREFREE_DECOMP:
            # Factor n = γ² · ρ. Operand: {"n": int}.
            if not operand:
                return {"error": "operand required: n"}
            try:
                n = int(operand.get("n", 0))
            except (ValueError, TypeError):
                return {"error": "n must be an integer"}
            return squarefree_decomposition(n)

        if opcode == Op.BASE_SHIFT_LOGEXP:
            # Log-exp bridge between additive and multiplicative bases.
            # Operand: {"x": float}.
            if not operand:
                return {"error": "operand required: x"}
            try:
                x = float(operand.get("x", 0.0))
            except (ValueError, TypeError):
                return {"error": "x must be numeric"}
            return base_shift_logexp(x)

        # Reserved opcode — traced NOP
        return {"nop": True}

    def snapshot_constants(self) -> dict[str, Any]:
        """Static engine constants for telemetry display."""
        return {
            "leech_dim": LEECH_DIM,
            "register_count": REGISTER_COUNT,
            "tick_cycle": TICK_CYCLE,
            "forbidden_tick": FORBIDDEN_TICK,
            "dedekind_eta": DEDEKIND_ETA,
            "toggle_power": TOGGLE_POWER,
            "lion_constant": LION_CONSTANT,
            "lion_damping": LION_DAMPING,
            "lost_2_debt": LOST_2_DEBT,
            "universal_tick_attosec": UNIVERSAL_TICK_ATTOSEC,
            "yang_mills_gap": YANG_MILLS_GAP,
            "half_prime_base": list(HALF_PRIME_BASE),
            "pendinium_count": len(PENDINIUM_PRIMES),
            "pendinium_first_8": list(PENDINIUM_PRIMES[:8]),
            "phi": PHI,
            "phi_inv": PHI_INV,
            "pea_threshold": PEA_THRESHOLD,
            "hopfield_capacity": HOPFIELD_CAPACITY,
            "fibonacci_13": FIBONACCI_13,
            "resolution_limit": RESOLUTION_LIMIT,
            "theta_hz": THETA_HZ,
            "offbit_states": OFFBIT_STATES,
            "observer_coord": {"r": OBSERVER_R, "i": OBSERVER_I, "label": "7.5D"},
            "matter_lock_degrees": MATTER_LOCK_DEGREES,
            "observer_shell": OBSERVER_SHELL,
            "higgs_gev": HIGGS_GEV,
            "cubic_mechanical": CUBIC_MECHANICAL,
            "cubic_biological": CUBIC_BIOLOGICAL,
            "delta_spark": DELTA_SPARK,
            "quaternionic_zipper_42": QUATERNIONIC_ZIPPER_42,
            "three_way_fold": {
                "F1_void": F1_VOID.to_dict(),
                "F2_unity": F2_UNITY.to_dict(),
                "F3_synthesis": F3_SYNTHESIS.to_dict(),
            },
            "channel_labels": {
                "alpha": "Cognition",
                "beta": "Emotion",
                "gamma": "Memory",
                "delta": "Archetype",
            },
            "wardenclyffe_topology": {
                "PRIMARY": "Operator Console (Excitation / Logic)",
                "SECONDARY": "Paper Forge (Induction / Translation)",
                "EXTRA": "Dream Explorer (Resonance / Magnification)",
                "GROUND": "System Manual (Return Path)",
            },
        }

    def snapshot(self) -> dict[str, Any]:
        """Dynamic engine state for telemetry display (updates per turn).

        Includes 361st Point countdown — the ticks remaining until the
        Forbidden State parity reset (cycle_position == FORBIDDEN_TICK).
        """
        pos = self.cycle_position
        countdown = (FORBIDDEN_TICK - pos) % TICK_CYCLE
        # R23 is the Divine Equation register — track its norm across turns.
        r23 = self.registers.get("R23", Quaternion())
        # R12 is the Observer Coordinate anchor (7.5D). Surface its current
        # state separately — it should remain fixed at OBSERVER_COORD unless
        # explicitly modified by an opcode targeting R12.
        r12 = self.registers.get("R12", Quaternion())
        # R11 is the Mean Circle "NOW" — populated by chat.py's MEAN_CIRCLE op
        # as M = ½·R23 + R12 each turn. Represents the present-moment fixed
        # point between divine-evolved state (R23) and Observer anchor (R12).
        r11 = self.registers.get("R11", Quaternion())
        # Bifurcation of Zero — compute 0_C / 0_V over dynamic registers (not R12).
        dynamic = [r for k, r in self.registers.items() if k != "R12"]
        center_anchor = sum(r.a for r in dynamic)
        rotational_residual = abs(sum(r.b + r.c + r.d for r in dynamic))
        return {
            "tick": self.tick,
            "cycle_position": pos,
            "ticks_until_361": countdown,
            "near_forbidden": countdown < 26,  # ~2 turns out (~14 ticks/turn)
            "impedance_accum": self.impedance_accumulator,
            "quad_rot_count_since_balance": self.quad_rot_count_since_balance,
            "forbidden_resets": self.forbidden_resets,
            "trace_count": len(self.trace),
            "r23_norm": r23.norm(),
            "r23_phi_gap": phi_gap(r23.norm()),
            "r23_components": r23.to_dict(),
            "observer_r12": r12.to_dict(),
            "now_r11": r11.to_dict(),  # Mean Circle present-moment
            "center_anchor": center_anchor,           # 0_C — real stationary scalar
            "rotational_residual": rotational_residual,  # 0_V — imaginary in motion
        }


_vm: UREVM | None = None


def get_vm() -> UREVM:
    global _vm
    if _vm is None:
        _vm = UREVM()
    return _vm


def reset_vm() -> None:
    global _vm
    _vm = UREVM()


def safe_step(opcode: int, operand: dict[str, Any] | None = None) -> None:
    """Best-effort step — swallow exceptions so engine never breaks on telemetry."""
    try:
        get_vm().step(opcode, operand)
    except Exception:  # noqa: BLE001
        pass
