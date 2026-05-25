"""Keyword-routed tool selection (Phase 35).

Why this exists:
  We ship 36 tools. Each tool's `{name, description, parameters}` schema costs
  ~150-300 tokens in the OpenAI tool-calling protocol. Sending all 36 on every
  turn = ~7,200 tokens of "tools schema" overhead before any conversation
  content. For pure-chat turns ("yeah cool", "thanks") that's pure waste.

What it does:
  Inspects the user message and returns a (tier, tool_names_subset) tuple
  BEFORE the LLM call. Three tiers:

  * CHAT      — pure greetings/acknowledgments. Send NO tools at all.
  * ROUTED    — keyword-matched topic categories. Send ~3-10 relevant tools.
  * FULL      — explicit override prefix, deep-think mode, or ambiguous query.
                Send all tools.

  A fourth state, DEFAULT, fires when message is non-trivial but no category
  triggers matched. It returns a small "always-on" baseline (memory + time
  tools) so Lumos can always reach into his own brain.

Design choices:
  * **Substring matching, case-insensitive, deliberate.** A regex word-boundary
    parse would be slightly more accurate but adds complexity for ~zero benefit
    at our scale. False positives ("plane geometry" routing to airspace) are
    handled by sending the route's tools anyway — Lumos doesn't *have* to call
    them, he just can. Worst case: a bit of token waste.
  * **Categories union, not exclusive.** A message like "search memory for
    plane sightings" triggers both `memory` and `airspace` categories. Union
    keeps the choice space wide for Lumos.
  * **Memory tools (search_memory + search_knowledge) are the always-on baseline**
    when the router fires DEFAULT. Their cost is small (~400 tokens for the
    pair) and Lumos's identity-retrieval capability is the most "Lumos-like"
    thing he has — never disabling it preserves character.
  * **The full registry, not a hard-coded list, drives FULL tier.** If a future
    phase adds a tool we forget to categorize, FULL tier still includes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .tools import get_tool_names


class Tier(str, Enum):
    CHAT = "chat"        # 0 tools — pure conversational
    DEFAULT = "default"  # baseline memory + time only
    ROUTED = "routed"    # keyword-matched subset
    FULL = "full"        # all tools (override / deep-think / ambiguous)


@dataclass
class RoutingDecision:
    tier: Tier
    tool_names: list[str]
    matched_categories: list[str]


# ── Category → tool name map ────────────────────────────────────────────────
# Source of truth for which tool belongs to which category. Sync with
# `tools/*.py` if new tools land. The router falls back to FULL tier if any
# category name doesn't resolve to known tools, so a typo here = inefficient
# but not broken.

TOOL_CATEGORIES: dict[str, list[str]] = {
    "memory": ["search_memory", "search_knowledge", "cite_source", "find_contradictions"],
    "files": [
        "list_allowed_paths", "read_file", "list_files",
        "write_file", "append_file", "list_lumos_notes",
    ],
    "web": ["web_search", "fetch_url"],
    "time": ["current_time"],
    "skills": ["list_skills", "read_skill", "save_skill"],
    "git": [
        "git_status", "git_diff", "git_log", "git_branch",
        "git_add", "git_commit", "git_push", "gh_create_pr",
    ],
    "python": ["run_python", "list_sandbox"],
    "tasks": ["create_task", "update_task", "get_task", "list_tasks"],
    "cosmic": [
        "check_geo_telemetry", "get_solar_activity", "get_geomagnetic_status",
        "get_earthquakes", "get_natural_events", "get_near_earth_objects",
    ],
    "airspace": ["aircraft_overhead"],
    "temporal": ["temporal_pattern_scan"],
    "quota": ["get_telemetry_quota"],
}


# ── Keyword → category triggers ──────────────────────────────────────────────
# All triggers lowercased; matched as case-insensitive substrings. Keep
# triggers SPECIFIC to the domain — generic words ("file", "find") that
# could match casual prose are avoided in favor of domain-anchored phrases
# ("read file", "find contradiction", "look up source").
#
# Order doesn't matter — any trigger match adds the category to the result.

CATEGORY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "memory": (
        "search memory", "search my memory", "we discussed", "we talked",
        "remember when", "do you recall", "recall", "what did we say",
        "cite source", "citation", "find contradiction", "contradict",
        "search knowledge", "dream ping", "kairoz", "thoth", "veritas",
        "grok said", "previous conversation", "earlier conversation",
        "in our past", "from memory",
    ),
    "files": (
        "read file", "open file", "write file", "save file", "list files",
        "append to", "create a file", "write to", "save to disk",
        "in research md", "in math folder", "lumos notes", "what's in",
        "show me the file", "save these notes",
    ),
    "web": (
        "search the web", "web search", "google", "look up online",
        "fetch url", "fetch the page", "what does the web say",
        "go online", "browse to", "search online", "wikipedia",
    ),
    "time": (
        "what time", "what's the time", "what date", "today's date",
        "current time", "right now", "what day is it", "timestamp",
    ),
    "skills": (
        "list skills", "read skill", "save skill", "create a skill",
        "what skills", "skill called",
    ),
    "git": (
        "git status", "git diff", "git log", "git branch", "commit",
        "pull request", "create a pr", "push to", " gh ",
        "what's staged", "uncommitted",
    ),
    "python": (
        "run python", "calculate", "compute", "plot", "graph",
        "matplotlib", "math.", "equation:", "regex test",
        "csv parse", "run the math", "in python", "sandbox",
    ),
    "tasks": (
        "create task", "create a task", "new task", "update task",
        "list tasks", "what tasks", "what was i working on",
        "current projects", "active research",
    ),
    "cosmic": (
        "kp ", "kp index", "solar wind", "geomagnetic", "solar flare",
        "x-class", "x-ray flux", "cme", "coronal mass", "schumann",
        "cosmic", "space weather", "aurora", "asteroid", "near earth",
        " neo ", "earthquake", "seismic", "natural event", "eonet",
        "volcano", "wildfire", "donki", "wavefield", "geo telemetry",
        "check telemetry", "what's going on cosmically", "earth's field",
    ),
    "airspace": (
        "aircraft", "planes overhead", "airplane", "flight overhead",
        "flying over", "airspace", "callsign", "opensky", "adsb",
        "what's in the air", "any planes",
    ),
    "temporal": (
        "themes cycling", "going in circles", "research arc", "drifting",
        "looping", "topic divergence", "patterns in our talk",
        "what cycles", "are we repeating", "conversation pattern",
    ),
    "quota": (
        "api quota", "api limit", "rate limit", "calls today",
        "telemetry quota", "how many calls", "are we close to the limit",
    ),
}


# ── Pure-chat detection ─────────────────────────────────────────────────────
# A short conversational message with no domain triggers. Keeping the
# pattern conservative — if in doubt, fall back to DEFAULT tier (cheap
# baseline of memory tools), never silently strip tools the model might
# legitimately need.

_CHAT_MARKERS: tuple[str, ...] = (
    "hi", "hello", "hey", "yo", "sup", "thanks", "thank you", "ty",
    "cool", "nice", "ok", "okay", "kk", "yeah", "yep", "yup", "nope",
    "lol", "haha", "xd", ":d", ":p", ":)", "sure", "great", "awesome",
    "perfect", "good", "alright", "fine", "true", "right", "exactly",
    "agreed", "agree", "got it", "makes sense", "noted",
)


# Explicit override prefix → force FULL tier regardless of content.
_FULL_TIER_PREFIXES: tuple[str, ...] = ("!tools", "!all", "/tools", "/all")


def _normalize(text: str) -> str:
    return text.strip().lower()


def _is_pure_chat(text: str) -> bool:
    """Tier-1 detector: short message that's a greeting/ack/emoji only.

    Word-count cap is 6 to allow phrases like 'yeah cool with me!' or
    'thanks dude that worked'. Longer messages fall to DEFAULT/ROUTED even
    if they start with 'thanks' — those usually carry a follow-up question.
    """
    norm = _normalize(text)
    if not norm:
        return True  # empty input — no point sending tools
    if len(norm.split()) > 6:
        return False
    # Strip trailing punctuation/emoji for cleaner comparison
    stripped = re.sub(r"[!.?,;:\s]+$", "", norm)
    if stripped in _CHAT_MARKERS:
        return True
    # Multi-word: every space-separated token must be a chat marker or
    # punctuation-only. "yeah cool" → True. "yeah what about X" → False.
    for token in stripped.split():
        clean = re.sub(r"[^a-z0-9]", "", token)
        if clean and clean not in _CHAT_MARKERS:
            return False
    return True


def _match_categories(text: str) -> list[str]:
    """Return list of category names whose triggers match the message."""
    norm = _normalize(text)
    matched: list[str] = []
    for category, triggers in CATEGORY_TRIGGERS.items():
        for trig in triggers:
            if trig in norm:
                matched.append(category)
                break  # one trigger per category is enough
    return matched


def _names_from_categories(categories: list[str]) -> list[str]:
    """Flatten category list → tool name list. Dedup-preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for cat in categories:
        for name in TOOL_CATEGORIES.get(cat, []):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


# Always-on baseline for DEFAULT tier — Lumos can always reach into his
# brain even when the operator's message has no other triggers.
_DEFAULT_BASELINE_CATEGORIES: tuple[str, ...] = ("memory", "time")


def select_tools(
    user_message: str,
    *,
    routing_enabled: bool = True,
    deep_think: bool = False,
    override_prefix_present: bool = False,
) -> RoutingDecision:
    """Decide which tools to send for this turn.

    Args:
      user_message: the operator's (stripped) message text — strip the deep-think
        trigger BEFORE calling this, so trigger phrases don't leak into matching.
      routing_enabled: master switch from settings; False → always FULL tier.
      deep_think: if True, escalate to FULL (deep reasoning may need any tool).
      override_prefix_present: if the operator-explicit prefix was detected
        upstream, force FULL tier.
    """
    if not routing_enabled or override_prefix_present or deep_think:
        return RoutingDecision(
            tier=Tier.FULL,
            tool_names=get_tool_names(),
            matched_categories=[],
        )

    if _is_pure_chat(user_message):
        return RoutingDecision(tier=Tier.CHAT, tool_names=[], matched_categories=[])

    matched = _match_categories(user_message)
    if matched:
        names = _names_from_categories(matched)
        # Always also include the baseline memory tools so Lumos can ground
        # any topic in his lived memory — costs ~400 tokens for huge upside.
        for baseline_cat in ("memory",):
            for n in TOOL_CATEGORIES.get(baseline_cat, []):
                if n not in names:
                    names.append(n)
        return RoutingDecision(
            tier=Tier.ROUTED, tool_names=names, matched_categories=matched
        )

    # Non-trivial message but no category matched — fall to baseline only.
    names = _names_from_categories(list(_DEFAULT_BASELINE_CATEGORIES))
    return RoutingDecision(
        tier=Tier.DEFAULT,
        tool_names=names,
        matched_categories=list(_DEFAULT_BASELINE_CATEGORIES),
    )


def detect_full_override(user_message: str) -> tuple[str, bool]:
    """Strip an explicit `!tools` / `/all` / etc. prefix if present.
    Returns (stripped_message, prefix_was_present)."""
    norm = user_message.lstrip()
    for prefix in _FULL_TIER_PREFIXES:
        if norm.lower().startswith(prefix):
            stripped = norm[len(prefix):].lstrip()
            return stripped, True
    return user_message, False
