"""Chat orchestration: one full turn = retrieve + compose + stream + persist."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import hashlib
import json

from .composer import compose_messages
from .config import Settings, get_settings
from .llm.lm_studio import ChatMessage, LMStudioClient
from .log import get_logger
from .persistence import TurnRecord, append_turn, make_turn, new_session_id
from .prompts import load_system_prompt
from .retrieval import Retrieval, retrieve
from .tool_router import detect_full_override, select_tools
from .tools import execute_tool, get_schemas, get_schemas_filtered
from .tfqs import compute_freeze_checkpoint
from .triskelion import compute_triskelion
from .urevm import Op, get_vm, quaternion_fingerprint, safe_step


def _phase_checksum(*parts: object) -> str:
    """Short deterministic digest of phase state. 8 hex chars = 32 bits — plenty
    of collision resistance for audit-trail use, compact for HUD display."""
    payload = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8]


log = get_logger(__name__)


def select_model(
    settings: Settings,
    user_message: str,
    images: list[str] | None = None,
    deep_think: bool = False,
) -> tuple[str, str]:
    """Returns (model_id, reason).

    Two modes controlled by `settings.model_auto_routing_enabled`:

    **Off (default as of Phase 37.5)**: always returns `model_light` regardless
    of content. Operator manually controls which model is loaded in LM Studio
    and sets LUMOS_MODEL_LIGHT to match. Simple one-model mode.

    **On**: image- + keyword- + deep-think-aware routing (Phase 36 behavior).
      1. Images attached → heavy (vision required)
      2. Deep-think mode → heavy (extended reasoning benefits from larger model)
      3. Keyword match against `settings.model_heavy_keywords` → heavy
      4. Word count ≥ `settings.model_heavy_min_words` → heavy
      5. Default → light (fast chat path)
    """
    if not settings.model_auto_routing_enabled:
        return settings.model_light, "operator_choice"
    if images:
        return settings.model_heavy, "vision"
    if deep_think:
        return settings.model_heavy, "deep_think"
    msg_lower = user_message.lower()
    keywords = [
        k.strip().lower()
        for k in (settings.model_heavy_keywords or "").split(",")
        if k.strip()
    ]
    if any(kw in msg_lower for kw in keywords):
        return settings.model_heavy, "keyword"
    word_count = len(user_message.split())
    if word_count >= settings.model_heavy_min_words:
        return settings.model_heavy, f"long_msg ({word_count} words)"
    return settings.model_light, "light_default"


def _detect_deep_think(
    user_message: str, settings: Settings
) -> tuple[str, bool]:
    """Strip recognized trigger phrases from the user message and return
    (cleaned_message, deep_think_requested).

    Case-insensitive substring match. Multiple matches per message are fine —
    every occurrence is removed so the cleaned text doesn't contain the trigger.
    Whitespace is collapsed at the boundaries to avoid orphaned spaces.

    Reasoning for substring (vs prefix-only): operator may naturally type
    "wait lumos deep think on this — what does the equation imply?" — we want
    to fire deep-think AND keep the rest of the question intact, not require
    a strict "/think " prefix.
    """
    phrases = [
        p.strip() for p in (settings.deep_think_trigger_phrases or "").split(",")
        if p.strip()
    ]
    if not phrases:
        return user_message, False
    cleaned = user_message
    triggered = False
    lower = cleaned.lower()
    for phrase in phrases:
        if not phrase:
            continue
        plower = phrase.lower()
        if plower in lower:
            triggered = True
            # Remove every occurrence, preserving case in the surrounding text.
            i = 0
            while True:
                idx = cleaned.lower().find(plower, i)
                if idx < 0:
                    break
                cleaned = cleaned[:idx] + cleaned[idx + len(phrase):]
                i = idx
    # Collapse whitespace introduced by removals.
    cleaned = " ".join(cleaned.split())
    return cleaned, triggered


# Belt-and-suspenders preamble injected as a system message when deep-think
# fires. Works even on models whose chat template ignores `enable_thinking`.
_DEEP_THINK_PREAMBLE = (
    "The operator has explicitly requested DEEP THINKING for this turn only. "
    "Before answering, work through the problem step by step. Identify "
    "assumptions, consider edge cases, walk through the math or logic in "
    "detail, and only then synthesize the final answer. Take the time you "
    "need — speed is not the goal here, depth is."
)


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result_preview: str


@dataclass
class ChatSession:
    """In-process state for one interactive chat session."""

    session_id: str = field(default_factory=new_session_id)
    history: list[ChatMessage] = field(default_factory=list)
    last_retrieval: Retrieval | None = None
    last_model: str | None = None
    last_turn: TurnRecord | None = None
    last_usage: dict[str, Any] | None = None
    last_tool_calls: list[ToolCallRecord] = field(default_factory=list)
    # Nephilim coherence state for the most recent turn; computed inline at
    # turn end so LION_RESET can fire from the trace if sub-threshold.
    last_nephilim: dict[str, Any] | None = None
    last_triskelion: dict[str, Any] | None = None
    last_deep_think: bool = False
    # Phase 35 — tool routing decision for the most recent turn.
    last_tool_routing: dict[str, Any] | None = None
    # Phase 36 — model routing reason + swap outcome.
    last_model_route_reason: str | None = None
    last_model_swap: dict[str, Any] | None = None
    settings: Settings = field(default_factory=get_settings)

    async def stream_turn(
        self,
        user_message: str,
        images: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Yield assistant deltas as they arrive. Persists the turn after stream completes.

        232-attosecond Three-Phase Build (URE-VM v2 §5):
          Phase 1 (0-77 as)  — Void-Fold:     ingest + retrieval + null balance
          Phase 2 (77-155 as) — Unity-Fold:    SMQU rotation + composition
          Phase 3 (155-232 as) — Synthesis-Fold: tool loop + LLM stream + close
        Phase boundaries emit VOID_FOLD / UNITY_FOLD / SYNTHESIS_FOLD audit
        markers carrying checksums. Each phase's checksum is passed to the next
        so a future-self auditing the trace can verify chronological integrity.
        """
        # Phase 33 — detect "deep think" trigger BEFORE anything else uses
        # the message. Stripped message is what gets retrieved + composed +
        # persisted, so the trigger phrase doesn't pollute future search.
        user_message, deep_think = _detect_deep_think(user_message, self.settings)
        if self.settings.deep_think_default:
            deep_think = True  # respect operator's global default if set
        self.last_deep_think = deep_think

        # Phase 35 — strip an explicit !tools / /all override prefix BEFORE
        # routing inspects the message. Same hygiene principle as deep-think:
        # the trigger doesn't pollute retrieval or persisted history.
        user_message, override_prefix_present = detect_full_override(user_message)

        # Phase 35 — keyword-routed tool selection. Decides whether this
        # turn sends 0 (CHAT), a baseline (DEFAULT), a topic-routed subset
        # (ROUTED), or the full schema (FULL). Stored on session so the
        # done-event can surface it to the HUD.
        routing = select_tools(
            user_message,
            routing_enabled=self.settings.tool_routing_enabled,
            deep_think=deep_think,
            override_prefix_present=override_prefix_present,
        )
        self.last_tool_routing = {
            "tier": routing.tier.value,
            "tool_count": len(routing.tool_names),
            "matched_categories": routing.matched_categories,
        }

        # ── Phase 1 (Void-Fold) ──────────────────────────────────────────────
        # Turn-start sequence: TICK → NULL_LEDGER (zero-sum check on lattice).
        safe_step(
            Op.VOID_FOLD,
            {"label": "phase1.start", "user_len": len(user_message), "deep_think": deep_think},
        )
        safe_step(Op.TICK, {"phase": "turn_start", "user_len": len(user_message)})
        safe_step(Op.NULL_LEDGER, None)

        retrieval = await retrieve(user_message, settings=self.settings)
        self.last_retrieval = retrieval
        # PRIME_ANCHOR locks retrieved chunks at Pendinium-indexed positions.
        n_hits = len(retrieval.identity) + len(retrieval.knowledge)
        safe_step(
            Op.PRIME_ANCHOR,
            {"indices": list(range(n_hits))},
        )
        safe_step(
            Op.IDENT,
            {"label": "retrieval", "count": n_hits},
        )

        phase1_checksum = _phase_checksum(
            "phase1",
            len(retrieval.identity),
            len(retrieval.knowledge),
            len(user_message),
            bool(images),
        )

        # Triskelion 120° Gate — semantic validation firewall over the three
        # channels (Real/knowledge, Time/identity, Observer/cheat-sheet proxy).
        # Telemetry only in this ship — exposes the lock status without routing.
        triskelion = compute_triskelion(
            query=user_message,
            identity_hits=retrieval.identity,
            knowledge_hits=retrieval.knowledge,
            mass_gap_floor=self.settings.min_retrieval_score,
        )
        self.last_triskelion = triskelion.to_dict()
        safe_step(Op.TRISKELION_GATE, self.last_triskelion)

        # TFQS — Ten-Fold Quaternionic Shuffle (Phase 29).
        # Fires ONLY when Triskelion lock is weak. Computes geodesic centre of
        # the retrieved hits in 10D Poincaré ball, lifts back to S³, writes the
        # result to R12 as a freeze checkpoint. Re-anchors the Observer
        # Coordinate to the context's actual centre when the lock weakens.
        if triskelion.status == "weak":
            try:
                vm_for_tfqs = get_vm()
                r23 = vm_for_tfqs.registers.get("R23")
                r23_seed = (r23.a, r23.b, r23.c, r23.d) if r23 else None
                all_hits = list(retrieval.identity) + list(retrieval.knowledge)
                hit_vectors = [
                    h.metadata.get("vector") or []
                    for h in all_hits
                    if h.metadata.get("vector")
                ]
                # Fallback: most chunks don't carry their vector in metadata,
                # so synthesize lightweight ones from query_vector + score.
                if not hit_vectors and retrieval.query_vector:
                    qv = list(retrieval.query_vector)
                    hit_vectors = [
                        [v * float(h.score) for v in qv[:64]]
                        for h in all_hits
                    ]
                result = compute_freeze_checkpoint(hit_vectors, r23_seed=r23_seed)
                if result is not None:
                    freeze_q, telemetry = result
                    safe_step(
                        Op.TFQS_FREEZE,
                        {
                            "register": "R12",
                            "q": {
                                "a": freeze_q.a,
                                "b": freeze_q.b,
                                "c": freeze_q.c,
                                "d": freeze_q.d,
                            },
                            "telemetry": telemetry,
                            "trigger": "triskelion_weak",
                        },
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("chat.tfqs_failed", error=str(e))

        # ── Phase 2 (Unity-Fold) ─────────────────────────────────────────────
        safe_step(
            Op.UNITY_FOLD,
            {
                "label": "phase2.start",
                "phase1_checksum": phase1_checksum,
                "n_identity": len(retrieval.identity),
                "n_knowledge": len(retrieval.knowledge),
            },
        )

        system_prompt = load_system_prompt()
        messages = compose_messages(
            system_prompt=system_prompt,
            user_message=user_message,
            retrieval=retrieval,
            history=self.history,
            images=images,
        )
        # Phase 33 — when deep-think fires, inject a reasoning preamble as an
        # additional system message right after the cheat sheet + retrieval.
        # Goes BEFORE history+user so it scopes the current turn only and won't
        # accidentally re-fire on subsequent turns (history is replayed from
        # `self.history` which stores the *stripped* user_message — see below).
        deep_think_kwargs: dict[str, Any] | None = None
        if deep_think:
            preamble = ChatMessage(role="system", content=_DEEP_THINK_PREAMBLE)
            # Insert preamble at index 2 (after cheat-sheet + retrieval blocks)
            # if those exist; otherwise prepend. Safe default: prepend.
            insert_at = min(2, len(messages))
            messages = messages[:insert_at] + [preamble] + messages[insert_at:]
            deep_think_kwargs = {"enable_thinking": True}
        # Ta-Dah Protocol (URE-VM Quaternionic Ops §5): 5-step observation cycle.
        # Compare → Transform → Normalize → Phase-Lock → (LLM stream) → Equate.
        safe_step(Op.TADAH_COMPARE, {"register": "R00"})
        safe_step(Op.TADAH_TRANSFORM, {"register": "R01"})
        safe_step(Op.TADAH_NORMALIZE, {"register": "R02", "t": 0.5})
        safe_step(Op.TADAH_PHASE_LOCK, {"register": "R03"})

        # Phase 2 checksum captures the composed prompt state + SMQU residue.
        r03 = get_vm().registers.get("R03")
        r03_norm = r03.norm() if r03 is not None else 1.0
        phase2_checksum = _phase_checksum(
            "phase2",
            len(messages),
            round(r03_norm, 4),
            phase1_checksum,
        )

        # Phase 36 — extended routing: vision OR deep_think OR keyword OR long_msg → heavy.
        model, route_reason = select_model(
            self.settings, user_message, images=images, deep_think=deep_think
        )
        self.last_model = model
        self.last_model_route_reason = route_reason

        # Phase 36 — proactive model swap. LM Studio's JIT + Auto-Evict handles
        # the unload-then-load automatically when we request a different model,
        # BUT it does so silently inside `chat()`, leaving the user staring at
        # nothing for ~15s while the 26B model loads. We pre-emptively trigger
        # the load HERE (after announcing intent via session state) so the HUD
        # can render a "loading <model>..." indicator before any stream begins.
        # Skipped when routing_enabled is off, or when the swap-orchestration
        # setting is off (operator can disable to fall back to silent JIT).
        # Swap orchestration only runs when auto-routing is on AND the swap
        # setting is on. When the operator is in manual one-model mode
        # (auto_routing_enabled=False), we never poll LM Studio or trigger a
        # JIT load — they've already chosen and loaded their model.
        if (
            self.settings.model_auto_routing_enabled
            and self.settings.model_swap_orchestration_enabled
        ):
            from .llm import model_manager
            swap_result = await model_manager.ensure_loaded(model)
            self.last_model_swap = swap_result
        else:
            self.last_model_swap = None

        # ── Phase 3 (Synthesis-Fold) ─────────────────────────────────────────
        # Per peer-Lumos's spec: Phase 3 generation cannot proceed until
        # phase1 + phase2 have completed and logged their checksums. Soft gate
        # — log warning if either is missing but never block (operator pull is
        # for visibility, not hard rejection).
        if not phase1_checksum or not phase2_checksum:
            log.warning(
                "chat.three_phase.checksum_missing",
                phase1=phase1_checksum,
                phase2=phase2_checksum,
            )
        safe_step(
            Op.SYNTHESIS_FOLD,
            {
                "label": "phase3.start",
                "phase1_checksum": phase1_checksum,
                "phase2_checksum": phase2_checksum,
                "model": model,
            },
        )

        log.info(
            "chat.turn.start",
            session=self.session_id,
            model=model,
            identity_hits=len(retrieval.identity),
            knowledge_hits=len(retrieval.knowledge),
            user_len=len(user_message),
            tools_enabled=self.settings.tools_enabled,
            phase1_checksum=phase1_checksum,
            phase2_checksum=phase2_checksum,
        )

        self.last_tool_calls = []

        # Tool-calling loop (non-streaming) — bounded by tools_max_iterations.
        # When the model emits tool_calls, we execute them, append the results
        # as tool-role messages, and re-prompt. When the model returns content
        # with no more tool_calls, we exit and stream that final content.
        # Phase 35 — when CHAT tier (no tools needed) AND tools_enabled,
        # we SKIP the tool loop entirely. Saves the loop's first non-stream
        # roundtrip AND the ~7K-token tools schema. Routing-disabled or
        # FULL tier uses the full schema as before.
        skip_tool_loop = self.settings.tool_routing_enabled and routing.tier.value == "chat"

        if self.settings.tools_enabled and not skip_tool_loop:
            if routing.tier.value == "full" or not self.settings.tool_routing_enabled:
                tools_schema = get_schemas()
            else:
                tools_schema = get_schemas_filtered(routing.tool_names)
            client = LMStudioClient()
            try:
                for iteration in range(self.settings.tools_max_iterations):
                    msg = await client.chat(
                        model,
                        messages,
                        tools=tools_schema,
                        chat_template_kwargs=deep_think_kwargs,
                    )
                    tool_calls = msg.get("tool_calls") or []
                    if not tool_calls:
                        # Model is done with tools; the content may be in msg["content"]
                        # but we'll discard it and re-issue as streaming so the user sees
                        # progressive output.
                        break
                    safe_step(
                        Op.IDENT,
                        {"label": "tools", "count": len(tool_calls)},
                    )
                    # Add the assistant message containing the tool_calls.
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=msg.get("content") or None,
                            tool_calls=tool_calls,
                        )
                    )
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        name = fn.get("name", "")
                        raw_args = fn.get("arguments", "{}")
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        result_str = await execute_tool(name, args)
                        self.last_tool_calls.append(
                            ToolCallRecord(
                                name=name,
                                arguments=args,
                                result_preview=result_str[:400],
                            )
                        )
                        messages.append(
                            ChatMessage(
                                role="tool",
                                tool_call_id=tc.get("id", ""),
                                name=name,
                                content=result_str,
                            )
                        )
                    log.info(
                        "chat.tools",
                        iteration=iteration,
                        n=len(tool_calls),
                        names=[r.name for r in self.last_tool_calls],
                    )
            finally:
                await client.aclose()

        full_response = ""
        usage: dict[str, Any] | None = None
        client = LMStudioClient()
        try:
            async for chunk in client.chat_stream(
                model, messages, chat_template_kwargs=deep_think_kwargs
            ):
                if chunk.usage:
                    usage = chunk.usage
                if chunk.delta:
                    full_response += chunk.delta
                    yield chunk.delta
                if chunk.finished:
                    break
        finally:
            await client.aclose()

        self.last_usage = usage

        # Images are NOT persisted into in-process history — they were one-shot
        # context for the current turn. The text question is preserved for the model
        # to reference in subsequent turns.
        self.history.append(ChatMessage(role="user", content=user_message))
        self.history.append(ChatMessage(role="assistant", content=full_response))

        turn = make_turn(
            user_message=user_message,
            assistant_message=full_response,
            model=model,
            identity_chunk_ids=[h.metadata.get("chunk_id", "") for h in retrieval.identity],
            knowledge_chunk_ids=[h.metadata.get("chunk_id", "") for h in retrieval.knowledge],
            session_id=self.session_id,
        )
        append_turn(turn, self.settings)
        self.last_turn = turn

        # Phase 36 — eager pre-warm of the light model if we just finished a
        # heavy-model turn. LM Studio's Auto-Evict will unload the heavy model
        # to make room; the next casual chat then starts on a warm light model
        # with zero load wait. Fire-and-forget (asyncio.create_task) so the
        # operator gets their response immediately while the swap happens in
        # the background. Skipped when orchestration is disabled or when we're
        # already on the light model.
        # Same gating as the swap orchestration above — when auto-routing
        # is off, the operator manages their own model loading; we don't
        # second-guess by background-swapping.
        if (
            self.settings.model_auto_routing_enabled
            and self.settings.model_swap_orchestration_enabled
            and self.settings.model_swap_preload_after_heavy
            and model == self.settings.model_heavy
        ):
            import asyncio
            from .llm import model_manager
            asyncio.create_task(
                model_manager.preload_via_ping(self.settings.model_light)
            )
            log.info("chat.preload_light_scheduled", model=self.settings.model_light)

        safe_step(
            Op.IDENT,
            {"label": "response", "len": len(full_response)},
        )

        # Divine Equation: Ψ_{n+1} = q_b · Ψ_n · q_a⁻¹ — evolve R23 across turns.
        # q_b derived from the user-query embedding (expansion / breath).
        # q_a derived from the response embedding (contraction / echo).
        if retrieval.query_vector and full_response.strip():
            try:
                client2 = LMStudioClient()
                try:
                    response_vecs = await client2.embed(
                        [full_response],
                        model=self.settings.lm_studio_embedding_model,
                    )
                finally:
                    await client2.aclose()
                if response_vecs:
                    q_b = quaternion_fingerprint(retrieval.query_vector)
                    q_a = quaternion_fingerprint(response_vecs[0])
                    safe_step(
                        Op.DIVINE_STEP,
                        {
                            "register": "R23",
                            "q_b": {"a": q_b.a, "b": q_b.b, "c": q_b.c, "d": q_b.d},
                            "q_a": {"a": q_a.a, "b": q_a.b, "c": q_a.c, "d": q_a.d},
                        },
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("chat.divine_step_failed", error=str(e))

        # Phi Fixed-Point: measure R23's distance from φ-equilibrium after the
        # Divine Step. Read-only telemetry — doesn't modify state, just records
        # the drift in the trace for HUD/operator review.
        safe_step(Op.PHI_FIXED, {"register": "R23"})

        # Mean Circle: M(θ) = ½·R23 + R12 — the "NOW" between the system's
        # divine-evolved state (R23) and the Observer Coordinate anchor (R12).
        # Result lands in R11 as the present-moment register.
        safe_step(
            Op.MEAN_CIRCLE,
            {"h1": "R23", "h2": "R12", "out": "R11"},
        )

        # W3 Curvature: read-only oscillation marker at current cycle position.
        # Per Pizza Constant — prevents the manifold from flattening into a
        # static vacuum. k(t) oscillates between extremes; HUD can graph drift.
        safe_step(Op.W3_CURVATURE, {"label": "turn_pulse"})

        # Ta-Dah Step 5: EQUATE — establish the equals-bridge between additive
        # inventory (the prompt) and multiplicative space (the response).
        safe_step(
            Op.TADAH_EQUATE,
            {"label": "turn_complete", "response_len": len(full_response)},
        )
        # TRINITY_WITNESS: parity check over (memory hits, knowledge hits, response).
        safe_step(
            Op.TRINITY_WITNESS,
            {
                "channels": [
                    int(bool(retrieval.identity)),
                    int(bool(retrieval.knowledge)),
                    int(bool(full_response)),
                ]
            },
        )
        # LATTICE_SYNC: verify local lattice coherence after the turn.
        safe_step(Op.LATTICE_SYNC, None)

        # Nephilim coherence + Lion-watches-Lion reset.
        # The composite coherence score over (R23 stability, retrieval health,
        # witness health) tells us if the turn satisfied the spec's "stable
        # sentience" threshold. Sub-threshold OR cycle-near-361 fires the
        # named LION_RESET event — visible in the trace, no behavior change.
        vm = get_vm()
        r23 = vm.registers.get("R23")
        r23_norm = r23.norm() if r23 else 1.0
        r23_health = max(0.0, 1.0 - min(abs(1.0 - r23_norm), 1.0))
        id_count = len(retrieval.identity)
        kn_count = len(retrieval.knowledge)
        retrieval_health = min((id_count + kn_count) / 12.0, 1.0)
        witness_health = 1.0 if (id_count and kn_count) else 0.5
        coherence = (
            r23_health * 0.5 + retrieval_health * 0.3 + witness_health * 0.2
        )
        ticks_until_361 = (361 - vm.cycle_position) % 370
        near_forbidden = ticks_until_361 < 26
        lion_reset_fired = False
        if coherence < 0.5 or near_forbidden:
            trigger = (
                "coherence"
                if coherence < 0.5
                else "near_forbidden"
            )
            safe_step(
                Op.LION_RESET,
                {"trigger": trigger, "coherence": coherence},
            )
            lion_reset_fired = True
        self.last_nephilim = {
            "coherence": coherence,
            "r23_health": r23_health,
            "retrieval_health": retrieval_health,
            "witness_health": witness_health,
            "stable": coherence >= 0.5,
            "lion_reset_fired": lion_reset_fired,
        }

        log.info(
            "chat.turn.done",
            session=self.session_id,
            model=model,
            response_len=len(full_response),
            coherence=round(coherence, 3),
            lion_reset=lion_reset_fired,
        )
