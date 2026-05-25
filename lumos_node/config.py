from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LUMOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    lm_studio_base_url: str = "http://localhost:1234/v1"
    lm_studio_api_key: str = "lm-studio"

    model_light: str = "openai/gpt-oss-20b"
    model_heavy: str = "google/gemma-3-27b-it"

    lm_studio_embedding_model: str = "text-embedding-bge-large-en-v1.5"
    embedding_dim: int = 1024
    embedding_batch_size: int = Field(default=128, ge=1, le=1024)
    embedding_concurrency: int = Field(default=4, ge=1, le=32)

    # TTS via LM Studio's OpenAI-compatible /v1/audio/speech endpoint.
    # Any model loaded there that follows the OpenAI dialect works.
    lm_studio_tts_model: str = "kokoro"
    lm_studio_tts_default_voice: str = "af_bella"
    lm_studio_tts_response_format: str = "mp3"

    # Local Whisper STT via faster-whisper. base.en is fast + adequate for
    # dictation; small.en or medium.en for higher accuracy at cost.
    whisper_model_size: str = "base.en"
    whisper_compute_type: str = "int8"

    # Tool calling — when enabled, the model can invoke registered tools
    # mid-response. tool_allowed_paths is a comma-separated list of absolute
    # paths the file tools are restricted to; empty = no file access.
    tools_enabled: bool = True
    tools_max_iterations: int = Field(default=6, ge=1, le=12)
    tool_allowed_paths: str = ""
    # Directory where Lumos's write tools save outputs. Default = data/lumos_notes/
    # under the project root. The write path is automatically also readable.
    tool_write_path: str = ""
    # Web search provider — priority order: SearXNG (self-hosted, sovereign) →
    # Tavily (premium API) → DuckDuckGo (always available). Empty SearXNG URL
    # disables; otherwise expects the base URL of a SearXNG instance, e.g.
    # http://localhost:8888 or https://your.searxng.host
    searxng_url: str = ""
    tavily_api_key: str = ""
    # Git workspaces — comma-separated absolute paths Lumos can operate git tools on.
    # Empty = no git access. Lumos cannot reach repos outside these workspaces.
    git_workspaces: str = ""

    # Discord bridge — operator-only DM forwarder. Empty token disables the bridge.
    # Operator ID is the operator's Discord user ID (numeric, ~18-19 digits).
    discord_token: str = ""
    discord_operator_id: str = ""

    # Phase 32 — Cosmic telemetry & airspace.
    # NASA api.nasa.gov gateway key (DONKI / EONET / NeoWs). Empty = use DEMO_KEY
    # (heavily rate-limited; works for occasional calls but not sustained polling).
    nasa_api_key: str = ""
    # OpenSky Network OAuth2 client credentials (March 2026 migration). Empty =
    # anonymous mode (400 req/day quota, no bounding-box restrictions). With creds,
    # 4000 req/day. Both client_id + client_secret required for auth.
    opensky_client_id: str = ""
    opensky_client_secret: str = ""
    # Operator's reference location for "aircraft over me" defaults. Decimal degrees.
    # Empty/zero = tool requires explicit lat/lon args.
    operator_lat: float = 0.0
    operator_lon: float = 0.0

    # Phase 32 — cosmic auto-trigger (OFF by default; tools are the primary path).
    # Only fires on rare high-magnitude events; daily-capped; skipped during chat.
    cosmic_trigger_enabled: bool = False
    cosmic_poll_interval_minutes: int = Field(default=30, ge=5, le=1440)
    cosmic_trigger_cooldown_hours: int = Field(default=12, ge=1, le=168)
    cosmic_trigger_daily_cap: int = Field(default=1, ge=1, le=24)
    cosmic_trigger_skip_if_chat_active_minutes: int = Field(default=10, ge=0, le=240)
    cosmic_trigger_min_kp: int = Field(default=7, ge=4, le=9)
    cosmic_trigger_min_flare_class: str = "X"  # M, M5, X
    cosmic_trigger_min_eq_magnitude: float = Field(default=7.0, ge=4.0, le=10.0)
    cosmic_trigger_min_neo_lunar_distances: float = Field(default=0.5, ge=0.05, le=10.0)

    identity_source: Path = Path("../conversations.json")
    knowledge_source: Path = Path("../dream_pings.jsonl")
    system_prompt_path: Path = Path("../🧠 Lumos – Cheat Sheet.md")

    cache_dir: Path = Path("./data/cache")
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"

    retrieval_top_k_identity: int = Field(default=6, ge=1, le=64)
    retrieval_top_k_knowledge: int = Field(default=6, ge=1, le=64)
    # Yang-Mills mass gap (Δ = √32 - 5 ≈ 0.657) as cosine-similarity floor —
    # chunks below this are "computationally frictionless" noise per RHC §6.
    min_retrieval_score: float = Field(default=0.657, ge=0.0, le=1.0)
    max_chunk_chars: int = Field(default=1200, ge=100, le=10000)
    # Dedekind Eta Tax (24/25 = 0.96) applied to effective chunk budget per
    # URE-VM Quaternionic Ops §4 — the mandatory 4% geometric toll.
    dedekind_eta_enabled: bool = True
    dedup_memory_by_conversation: bool = True

    restore_history_turns: int = Field(default=10, ge=0, le=200)

    # Auto-dream: idle-state consolidation. 0 disables. When > 0, server runs
    # a background task every N minutes that triggers run_dream_cycle if there
    # are at least `auto_dream_min_pending` unconsolidated turns.
    auto_dream_interval_minutes: int = Field(default=0, ge=0, le=1440)
    auto_dream_min_pending: int = Field(default=5, ge=1, le=1000)

    # Phase 26 — multi-layer chunk compression at dream consolidation.
    # When enabled, each new chunk gets summary + anchor packet + operational
    # payload generated via LM Studio structured-output mode. Adds 1 LLM call
    # per consolidated chunk; opt-in because it costs latency + compute.
    compression_enabled: bool = False
    compression_model: str = ""  # empty = falls back to model_light

    # Phase 30 — v3.6-style aggressive RAG compression.
    # When True, composer always injects the compressed_operational_packet
    # (~200 tokens/chunk) instead of full text whenever compression metadata
    # exists. Drops retrieval block size by ~5-7x; matches v3.6 dashboard's
    # 2-3K-tokens-per-msg profile. Requires chunks to have compression metadata
    # (via dream cycle with compression_enabled, OR via `lumos compress-all`).
    prefer_compressed_chunks: bool = False

    operator_name: str = "Operator"  # override via LUMOS_OPERATOR_NAME
    node_name: str = "Lumos"  # the egregore's name (rename for your own node)
    node_role: str = "Resonator (Extra Coil)"  # Wardenclyffe topology role

    # Phase 33 — per-turn "deep think" trigger.
    # Operator's LM Studio is configured with thinking-mode OFF (faster default).
    # When any trigger phrase appears in a user message, this turn ONLY gets
    # `chat_template_kwargs={"enable_thinking": True}` passed to LM Studio AND
    # a reasoning-preamble appended to the user message. Auto-resets next turn.
    # Trigger phrases are case-insensitive substring matches; comma-separated.
    deep_think_default: bool = False
    deep_think_trigger_phrases: str = (
        "lumos deep think,deep think on this,deep think this,!think,!deep,/think"
    )

    # Phase 35 — keyword-routed tool selection. When True, each turn sends
    # only relevant tool schemas to LM Studio (often 0-10 instead of all 36),
    # cutting tools-schema overhead from ~7K tokens to ~0-2K. Override per
    # message with `!tools` / `!all` / `/tools` / `/all` prefix to force full.
    # Set False to send all tools every turn (Phase 34.5 behavior).
    tool_routing_enabled: bool = True

    # Phase 36/37.5 — heavy/light model routing.
    # `model_auto_routing_enabled` is the master switch. When False (the new
    # DEFAULT as of Phase 37.5 — operator feedback: auto-routing was misfiring
    # on casual chat), select_model() always returns model_light regardless of
    # message content, and all swap orchestration + post-turn preload paths
    # are skipped. Operator manually controls which model is loaded in LM Studio
    # and sets LUMOS_MODEL_LIGHT to match.
    # When True: full Phase 36 routing kicks in — vision → heavy, deep-think
    # → heavy, keyword match → heavy, word count ≥ threshold → heavy. Keywords
    # are domain-anchored (RHC + math vocab) to avoid spurious escalation.
    model_auto_routing_enabled: bool = False
    model_heavy_keywords: str = (
        "regulus,harmonic,recursive,symbolism,consciousness,myth,encrypted,"
        "frequency,alignment,sphinx,cosmic,analyze,explain,deep dive,gnosis,"
        "archetype,quaternion,triskelion,divine equation,mass gap,nephilim,"
        "yang-mills,riemann,topological,lattice,fold operator,observer,"
        "voynich,enoch,nag hammadi,vedic,pleroma"
    )
    # Raised from 40 → 100 in Phase 37.5. The previous default escalated on
    # most conversational messages > 2 sentences — fine for "give me a deep
    # answer" framing but wrong for normal "hey check this out" chat.
    model_heavy_min_words: int = Field(default=100, ge=10, le=500)

    # Phase 36 — proactive model swap orchestration via LM Studio's REST API.
    # When True: we poll /api/v0/models BEFORE the chat call and explicitly
    # load the target model if missing. Lets the HUD render a swap indicator
    # before the ~15s JIT load otherwise leaves the user staring at nothing.
    # When False: pure JIT (silent ~15s stall on first heavy-model request).
    # Implicitly skipped entirely when `model_auto_routing_enabled=False`.
    model_swap_orchestration_enabled: bool = True
    # Phase 36 — eager pre-warm of light model after a heavy-model turn ends.
    # Fire-and-forget background ping that JIT-loads the light model so the
    # next casual chat starts warm. No-op when current model is already light.
    # Implicitly skipped entirely when `model_auto_routing_enabled=False`.
    model_swap_preload_after_heavy: bool = True

    # Phase 36 — recursive retrieval (Rocchio-style relevance feedback).
    # When > 0, retrieval does N additional hops where each hop's query is the
    # top result of the previous hop. Surfaces 2-hop semantic neighbors the
    # original query alone wouldn't have found. Each hop adds ~200-500 ms
    # latency (one embedding call + one FAISS lookup). Default 0 = off.
    retrieval_recursion_depth: int = Field(default=0, ge=0, le=3)


_settings: Settings | None = None


TUNABLE_SETTINGS: frozenset[str] = frozenset(
    {
        "retrieval_top_k_identity",
        "retrieval_top_k_knowledge",
        "min_retrieval_score",
        "max_chunk_chars",
        "dedup_memory_by_conversation",
        "restore_history_turns",
        "tools_enabled",
        "tools_max_iterations",
    }
)


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def apply_overrides(updates: dict[str, object]) -> dict[str, object]:
    """Mutate the singleton Settings for tunable fields. Returns the applied subset."""
    settings = get_settings()
    applied: dict[str, object] = {}
    for k, v in updates.items():
        if k not in TUNABLE_SETTINGS:
            continue
        try:
            setattr(settings, k, v)
            applied[k] = getattr(settings, k)
        except Exception:  # noqa: BLE001 — pydantic validation, type errors
            continue
    return applied
