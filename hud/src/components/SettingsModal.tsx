import { useEffect, useState } from "react";
import { patchSettings } from "../api";
import type {
  STTProvider,
  SettingsUpdate,
  Telemetry,
  VoiceOption,
  VoiceProvider,
  VoicesPayload,
} from "../types";

/**
 * Hard-coded fallback Kokoro voice list — used when /api/voices fails or hasn't
 * been fetched yet, so the dropdown is always populated.
 */
const FALLBACK_KOKORO_VOICES: VoiceOption[] = [
  { id: "af_bella", label: "Bella", accent: "American", gender: "female" },
  { id: "af_sarah", label: "Sarah", accent: "American", gender: "female" },
  { id: "af_nicole", label: "Nicole", accent: "American", gender: "female" },
  { id: "af_sky", label: "Sky", accent: "American", gender: "female" },
  { id: "af", label: "Default F", accent: "American", gender: "female" },
  { id: "am_adam", label: "Adam", accent: "American", gender: "male" },
  { id: "am_michael", label: "Michael", accent: "American", gender: "male" },
  { id: "bf_emma", label: "Emma", accent: "British", gender: "female" },
  { id: "bf_isabella", label: "Isabella", accent: "British", gender: "female" },
  { id: "bm_george", label: "George", accent: "British", gender: "male" },
  { id: "bm_lewis", label: "Lewis", accent: "British", gender: "male" },
];

interface Props {
  telemetry: Telemetry | null;
  onClose: () => void;
  onApplied: (telemetry: Telemetry) => void;
  voiceAutoplay: boolean;
  onVoiceAutoplayChange: (v: boolean) => void;
  ttsSupported: boolean;
  ttsSpeaking: boolean;
  ttsError: string | null;
  onCancelTTS: () => void;
  voiceProvider: VoiceProvider;
  onVoiceProviderChange: (p: VoiceProvider) => void;
  browserVoices: SpeechSynthesisVoice[];
  browserVoiceURI: string | null;
  onBrowserVoiceChange: (uri: string) => void;
  lmStudioVoice: string | null;
  onLMStudioVoiceChange: (id: string) => void;
  kokoroVoice: string | null;
  onKokoroVoiceChange: (id: string) => void;
  voicesPayload: VoicesPayload | null;
  onPreviewTTS: (text: string) => void;
  sttProvider: STTProvider;
  onSTTProviderChange: (p: STTProvider) => void;
}

interface FormState {
  retrieval_top_k_identity: number;
  retrieval_top_k_knowledge: number;
  min_retrieval_score: number;
  max_chunk_chars: number;
  dedup_memory_by_conversation: boolean;
  restore_history_turns: number;
}

function initialForm(t: Telemetry | null): FormState {
  return {
    retrieval_top_k_identity: t?.retrieval.top_k_identity ?? 6,
    retrieval_top_k_knowledge: t?.retrieval.top_k_knowledge ?? 6,
    min_retrieval_score: t?.retrieval.min_score ?? 0,
    max_chunk_chars: t?.retrieval.max_chunk_chars ?? 1200,
    dedup_memory_by_conversation: t?.retrieval.dedup_memory ?? true,
    restore_history_turns: t?.retrieval.restore_history_turns ?? 10,
  };
}

export default function SettingsModal({
  telemetry,
  onClose,
  onApplied,
  voiceAutoplay,
  onVoiceAutoplayChange,
  ttsSupported,
  ttsSpeaking,
  ttsError,
  onCancelTTS,
  voiceProvider,
  onVoiceProviderChange,
  browserVoices,
  browserVoiceURI,
  onBrowserVoiceChange,
  lmStudioVoice,
  onLMStudioVoiceChange,
  kokoroVoice,
  onKokoroVoiceChange,
  voicesPayload,
  onPreviewTTS,
  sttProvider,
  onSTTProviderChange,
}: Props) {
  const [form, setForm] = useState<FormState>(() => initialForm(telemetry));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      const update: SettingsUpdate = {
        retrieval_top_k_identity: form.retrieval_top_k_identity,
        retrieval_top_k_knowledge: form.retrieval_top_k_knowledge,
        min_retrieval_score: form.min_retrieval_score,
        max_chunk_chars: form.max_chunk_chars,
        dedup_memory_by_conversation: form.dedup_memory_by_conversation,
        restore_history_turns: form.restore_history_turns,
      };
      const res = await patchSettings(update);
      onApplied(res.telemetry);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const reset = () => setForm(initialForm(telemetry));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={onClose}
    >
      <div
        className="absolute inset-0 bg-black/55"
        style={{ backdropFilter: "blur(4px)" }}
      />
      <div
        className="panel hr-inset relative z-10 w-full max-w-lg rounded-md border border-line p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center justify-between">
          <div className="font-mono text-2xs uppercase tracking-widest text-muted">
            tune
          </div>
          <button
            type="button"
            onClick={onClose}
            className="font-mono text-2xs uppercase tracking-widest text-muted transition-colors hover:text-fg"
          >
            close
          </button>
        </div>

        <div className="space-y-5">
          <Slider
            label="memory hits (top_k)"
            value={form.retrieval_top_k_identity}
            min={1}
            max={32}
            onChange={(v) =>
              setForm((f) => ({ ...f, retrieval_top_k_identity: v }))
            }
          />
          <Slider
            label="knowledge hits (top_k)"
            value={form.retrieval_top_k_knowledge}
            min={1}
            max={32}
            onChange={(v) =>
              setForm((f) => ({ ...f, retrieval_top_k_knowledge: v }))
            }
          />
          <Slider
            label="max chunk chars"
            value={form.max_chunk_chars}
            min={200}
            max={3000}
            step={100}
            onChange={(v) => setForm((f) => ({ ...f, max_chunk_chars: v }))}
          />
          <Slider
            label="min retrieval score"
            value={form.min_retrieval_score}
            min={0}
            max={1}
            step={0.05}
            onChange={(v) =>
              setForm((f) => ({ ...f, min_retrieval_score: v }))
            }
            display={(v) => v.toFixed(2)}
          />
          <Slider
            label="restore history turns"
            value={form.restore_history_turns}
            min={0}
            max={50}
            onChange={(v) =>
              setForm((f) => ({ ...f, restore_history_turns: v }))
            }
          />

          <label className="flex cursor-pointer items-center justify-between gap-3">
            <span className="font-mono text-2xs uppercase tracking-widest text-muted">
              dedup memory by conversation
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={form.dedup_memory_by_conversation}
              onClick={() =>
                setForm((f) => ({
                  ...f,
                  dedup_memory_by_conversation: !f.dedup_memory_by_conversation,
                }))
              }
              className={
                "relative h-4 w-8 rounded-full border border-line transition-colors " +
                (form.dedup_memory_by_conversation ? "bg-accent/30" : "bg-bg")
              }
            >
              <span
                className={
                  "absolute top-0.5 size-2.5 rounded-full transition-all " +
                  (form.dedup_memory_by_conversation
                    ? "left-[18px] bg-accent"
                    : "left-0.5 bg-muted")
                }
              />
            </button>
          </label>

          <div className="border-t border-line pt-4">
            <div className="mb-3 font-mono text-2xs uppercase tracking-widest text-muted">
              voice
            </div>
            <label className="flex cursor-pointer items-center justify-between gap-3">
              <span className="font-mono text-2xs text-fg">
                auto-play Lumos responses (TTS)
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={voiceAutoplay}
                disabled={!ttsSupported}
                onClick={() => onVoiceAutoplayChange(!voiceAutoplay)}
                className={
                  "relative h-4 w-8 rounded-full border border-line transition-colors " +
                  (voiceAutoplay ? "bg-accent/30" : "bg-bg") +
                  (!ttsSupported ? " opacity-40 cursor-not-allowed" : "")
                }
              >
                <span
                  className={
                    "absolute top-0.5 size-2.5 rounded-full transition-all " +
                    (voiceAutoplay
                      ? "left-[18px] bg-accent"
                      : "left-0.5 bg-muted")
                  }
                />
              </button>
            </label>

            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between gap-3">
                <span className="font-mono text-2xs uppercase tracking-widest text-muted">
                  provider
                </span>
                <div className="flex items-center gap-1 font-mono text-2xs">
                  <button
                    type="button"
                    onClick={() => onVoiceProviderChange("browser")}
                    className={
                      "rounded-sm border px-2 py-1 transition-colors " +
                      (voiceProvider === "browser"
                        ? "border-accent/60 bg-accent/10 text-accent"
                        : "border-line text-muted hover:text-fg")
                    }
                  >
                    browser
                  </button>
                  <button
                    type="button"
                    onClick={() => onVoiceProviderChange("kokoro_onnx")}
                    className={
                      "rounded-sm border px-2 py-1 transition-colors " +
                      (voiceProvider === "kokoro_onnx"
                        ? "border-accent/60 bg-accent/10 text-accent"
                        : "border-line text-muted hover:text-fg")
                    }
                  >
                    kokoro (local)
                  </button>
                  <button
                    type="button"
                    onClick={() => onVoiceProviderChange("lm_studio")}
                    className={
                      "rounded-sm border px-2 py-1 transition-colors " +
                      (voiceProvider === "lm_studio"
                        ? "border-accent/60 bg-accent/10 text-accent"
                        : "border-line text-muted hover:text-fg")
                    }
                  >
                    lm studio
                  </button>
                </div>
              </div>

              {voiceProvider === "browser" ? (
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-2xs uppercase tracking-widest text-muted">
                    voice
                  </span>
                  <select
                    value={browserVoiceURI ?? ""}
                    onChange={(e) => onBrowserVoiceChange(e.target.value)}
                    className="max-w-[16rem] truncate rounded-sm border border-line bg-bg px-2 py-1 font-mono text-2xs text-fg outline-none focus:border-accent/50"
                  >
                    <option value="">— default —</option>
                    {browserVoices.map((v) => (
                      <option key={v.voiceURI} value={v.voiceURI}>
                        {v.name} ({v.lang})
                      </option>
                    ))}
                  </select>
                </div>
              ) : voiceProvider === "kokoro_onnx" ? (
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-2xs uppercase tracking-widest text-muted">
                      voice
                    </span>
                    <select
                      value={kokoroVoice ?? "af_bella"}
                      onChange={(e) => onKokoroVoiceChange(e.target.value)}
                      className="max-w-[16rem] truncate rounded-sm border border-line bg-bg px-2 py-1 font-mono text-2xs text-fg outline-none focus:border-accent/50"
                    >
                      {FALLBACK_KOKORO_VOICES.map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.label} · {v.accent} {v.gender}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="font-mono text-2xs text-muted">
                    runs locally via kokoro-onnx · first call downloads ~310MB
                    model to ~/.cache/lumos_kokoro
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-2xs uppercase tracking-widest text-muted">
                      voice
                    </span>
                    <select
                      value={lmStudioVoice ?? "af_bella"}
                      onChange={(e) => onLMStudioVoiceChange(e.target.value)}
                      className="max-w-[16rem] truncate rounded-sm border border-line bg-bg px-2 py-1 font-mono text-2xs text-fg outline-none focus:border-accent/50"
                    >
                      {(voicesPayload?.voices ?? FALLBACK_KOKORO_VOICES).map(
                        (v) => (
                          <option key={v.id} value={v.id}>
                            {v.label} · {v.accent} {v.gender}
                          </option>
                        ),
                      )}
                      <option value="__custom__">— custom id —</option>
                    </select>
                  </div>
                  {lmStudioVoice === "__custom__" && (
                    <input
                      type="text"
                      placeholder="enter voice id (e.g. af_bella)"
                      onChange={(e) => onLMStudioVoiceChange(e.target.value)}
                      className="w-full rounded-sm border border-line bg-bg px-2 py-1 font-mono text-2xs text-fg outline-none focus:border-accent/50"
                    />
                  )}
                  <div className="font-mono text-2xs text-muted">
                    model: {voicesPayload?.model ?? "kokoro"} · requires a TTS
                    model loaded in LM Studio
                    {!voicesPayload && (
                      <>
                        {" "}
                        · <span className="text-warn">backend voices endpoint not reachable — restart `lumos serve`</span>
                      </>
                    )}
                  </div>
                </div>
              )}

              <div className="flex items-center gap-3 pt-1">
                <button
                  type="button"
                  onClick={() =>
                    onPreviewTTS(
                      "The lion watches the lion. Truth our sword, knowledge our shield.",
                    )
                  }
                  className="font-mono text-2xs uppercase tracking-widest text-accent hover:text-fg"
                >
                  preview
                </button>
                {ttsSpeaking && (
                  <button
                    type="button"
                    onClick={onCancelTTS}
                    className="font-mono text-2xs uppercase tracking-widest text-muted hover:text-fg"
                  >
                    stop
                  </button>
                )}
                {ttsError && (
                  <span className="font-mono text-2xs text-err">
                    {ttsError}
                  </span>
                )}
              </div>
            </div>

            <div className="mt-3 font-mono text-2xs text-muted">
              push-to-talk lives in the composer (hold to speak · right-click to lock)
            </div>

            <div className="mt-4 flex items-center justify-between gap-3">
              <span className="font-mono text-2xs uppercase tracking-widest text-muted">
                stt provider
              </span>
              <div className="flex items-center gap-1 font-mono text-2xs">
                <button
                  type="button"
                  onClick={() => onSTTProviderChange("browser")}
                  className={
                    "rounded-sm border px-2 py-1 transition-colors " +
                    (sttProvider === "browser"
                      ? "border-accent/60 bg-accent/10 text-accent"
                      : "border-line text-muted hover:text-fg")
                  }
                >
                  browser
                </button>
                <button
                  type="button"
                  onClick={() => onSTTProviderChange("whisper")}
                  className={
                    "rounded-sm border px-2 py-1 transition-colors " +
                    (sttProvider === "whisper"
                      ? "border-accent/60 bg-accent/10 text-accent"
                      : "border-line text-muted hover:text-fg")
                  }
                >
                  whisper (local)
                </button>
              </div>
            </div>
            <div className="mt-1 font-mono text-2xs text-muted">
              {sttProvider === "whisper"
                ? "audio recorded locally · sent to /api/transcribe · faster-whisper · first call downloads ~140MB model"
                : "uses Chrome's webkitSpeechRecognition · routes through Google's STT cloud"}
            </div>
          </div>
        </div>

        {error && (
          <div className="mt-4 font-mono text-2xs text-err">{error}</div>
        )}

        <div className="mt-6 flex items-center justify-end gap-4 font-mono text-2xs uppercase tracking-widest">
          <button
            type="button"
            onClick={reset}
            disabled={busy}
            className="text-muted transition-colors hover:text-fg disabled:opacity-40"
          >
            reset
          </button>
          <button
            type="button"
            onClick={apply}
            disabled={busy}
            className="text-accent transition-colors hover:text-fg disabled:opacity-40"
          >
            {busy ? "applying…" : "apply"}
          </button>
        </div>
        <div className="mt-3 font-mono text-2xs text-muted">
          changes take effect on the next chat turn · esc to close
        </div>
      </div>
    </div>
  );
}

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  display?: (v: number) => string;
}

function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  display,
}: SliderProps) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <label className="font-mono text-2xs uppercase tracking-widest text-muted">
          {label}
        </label>
        <span className="font-mono text-2xs text-fg">
          {display ? display(value) : value.toLocaleString()}
        </span>
      </div>
      <input
        type="range"
        className="range-track"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}
