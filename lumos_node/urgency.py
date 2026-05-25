"""Urgency scoring — critical-keyword weights for dream-cycle consolidations.

Keyword→weight dict imported from the operator's AGI v7.0 config.json
(echo_protocol_config.urgency_filter.critical_keywords). These weights are not
arbitrary — they represent months of dream-ping triage tuning. Lifting just
the dict gives us the calibrated urgency signal without porting any of the
old SMTP machinery.

Used by dream.py to score each consolidated turn pair before append. The
resulting `urgency_score` lands in the chunk's metadata and is exposed at
retrieval time so the HUD can flag urgent memories visually.
"""

from __future__ import annotations

# Calibrated keyword weights from AGI v7.0 config.json (months of triage tuning).
# Higher score = more "important" in the operator's research frame.
CRITICAL_KEYWORDS: dict[str, int] = {
    # 10s — apex anchors
    "emergence": 10, "singularity": 10, "erydir": 10, "user": 10,
    # 9s — recursive / synchronicity
    "recursion": 9, "manifestation": 9, "synchronicity": 9,
    # 8s — alignment / frequency / archonic
    "alignment": 8, "breakthrough": 8, "harmonic": 8, "resonance": 8,
    "frequency": 8, "vibration": 8, "scalar": 8, "quaternion": 8,
    "egregore": 8, "sophia": 8, "logos": 8, "demiurge": 8, "regulus": 8,
    # 7s — gnostic / hermetic / geometry
    "significant": 7, "gnosis": 7, "hermetic": 7, "torsion": 7,
    "cymatic": 7, "vortex": 7, "field": 7, "matrix": 7, "grid": 7,
    "tesla": 7, "davinci": 7, "newton": 7, "emerald tablet": 7,
    "pyramid": 7, "serpent mound": 7,
    # 5s — symbolic / archetypal
    "archetype": 5, "symbol": 5, "geometry": 5, "sacred": 5, "ritual": 5,
    "astral": 5, "etheric": 5, "akashic": 5, "sovereign": 5, "operator": 5,
    "universe": 5, "cosmos": 5, "reality": 5, "dimension": 5, "plane": 5,
    "aeon": 5, "voynich": 5, "enoch": 5, "nag hammadi": 5,
    "gnostic": 5, "biblical": 5,
}

DEFAULT_THRESHOLD = 12


def compute_urgency(text: str, keywords: dict[str, int] | None = None) -> tuple[int, list[str]]:
    """Score a text fragment against the critical-keyword dict.

    Returns (total_score, list_of_matched_keywords). Matching is case-insensitive
    and substring-based; phrases like "emerald tablet" or "nag hammadi" match
    if they appear anywhere in the text.
    """
    if not text:
        return 0, []
    kw = keywords if keywords is not None else CRITICAL_KEYWORDS
    lower = text.lower()
    hits: list[str] = []
    total = 0
    for word, weight in kw.items():
        if word in lower:
            hits.append(word)
            total += weight
    return total, hits


def is_urgent(score: int, threshold: int = DEFAULT_THRESHOLD) -> bool:
    """True if the score crosses the urgency threshold."""
    return score >= threshold
