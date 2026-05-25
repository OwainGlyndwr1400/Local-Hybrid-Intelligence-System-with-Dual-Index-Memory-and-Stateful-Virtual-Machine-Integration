import { useCallback, useEffect, useRef, useState } from "react";
import { synthesizeSpeech } from "../api";
import type { VoiceProvider } from "../types";

/**
 * Strip lone UTF-16 surrogates from text before sending to /api/speak.
 *
 * Background: 4-byte emojis (e.g. 🌌 U+1F30C) are encoded in JS strings as
 * UTF-16 surrogate pairs (🌌). If a stream chunk boundary falls
 * between the high + low surrogate, the assembled string may contain an
 * orphan half. Python's `str.encode("utf-8")` raises UnicodeEncodeError on
 * lone surrogates, breaking the TTS endpoint AND the FastAPI 422 response
 * (which also re-encodes the bad string).
 *
 * This replaces orphan surrogates with U+FFFD (replacement character).
 * Valid pairs are preserved untouched.
 */
function sanitizeSurrogates(s: string): string {
  return s.replace(
    /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g,
    "�",
  );
}

export interface SpeechSynthesisHook {
  supported: boolean;
  speaking: boolean;
  error: string | null;
  /** One-shot: cancel any current playback, speak this text from scratch. */
  speak: (text: string) => void;
  /** Streaming: append this text chunk to an active streaming session (or start one). */
  speakStreaming: (text: string) => void;
  /** Streaming: signal no more chunks coming — finalize speaking state when queue drains. */
  endStreaming: () => void;
  cancel: () => void;
  voices: SpeechSynthesisVoice[];
}

interface Options {
  provider: VoiceProvider;
  browserVoiceURI: string | null;
  lmStudioVoice: string | null;
  lmStudioModel: string | null;
  kokoroVoice: string | null;
}

interface KokoroStreamState {
  abortController: AbortController;
  queue: HTMLAudioElement[];
  current: HTMLAudioElement | null;
  urls: string[];
  inFlight: number;
  ended: boolean;
  /** Promise chain to serialize chunk synthesis (preserves playback order). */
  synthChain: Promise<void>;
}

interface BrowserStreamState {
  pending: number;
  ended: boolean;
}

export function useSpeechSynthesis(opts: Options): SpeechSynthesisHook {
  const { provider, browserVoiceURI, lmStudioVoice, lmStudioModel, kokoroVoice } = opts;
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const cancelTokenRef = useRef(0);
  const kokoroStreamRef = useRef<KokoroStreamState | null>(null);
  const browserStreamRef = useRef<BrowserStreamState | null>(null);
  const lmStudioBufferRef = useRef<string>("");
  const browserSupported =
    typeof window !== "undefined" && "speechSynthesis" in window;
  const supported = provider !== "browser" || browserSupported;

  useEffect(() => {
    if (!browserSupported) return;
    const update = () => setVoices(window.speechSynthesis.getVoices());
    update();
    window.speechSynthesis.addEventListener("voiceschanged", update);
    return () => {
      window.speechSynthesis.removeEventListener("voiceschanged", update);
    };
  }, [browserSupported]);

  // ── Kokoro streaming ────────────────────────────────────────────────────

  const finalizeKokoroIfDone = useCallback(() => {
    const s = kokoroStreamRef.current;
    if (!s) return;
    if (s.ended && s.inFlight === 0 && s.queue.length === 0 && !s.current) {
      for (const url of s.urls) URL.revokeObjectURL(url);
      kokoroStreamRef.current = null;
      setSpeaking(false);
    }
  }, []);

  const advanceKokoro = useCallback(() => {
    const s = kokoroStreamRef.current;
    if (!s) return;
    if (s.current) return;
    if (s.queue.length === 0) {
      finalizeKokoroIfDone();
      return;
    }
    const next = s.queue.shift()!;
    s.current = next;
    next.onended = () => {
      s.current = null;
      advanceKokoro();
    };
    next.onerror = () => {
      s.current = null;
      advanceKokoro();
    };
    void next.play().catch(() => {
      s.current = null;
      advanceKokoro();
    });
  }, [finalizeKokoroIfDone]);

  const cleanupKokoro = useCallback(() => {
    const s = kokoroStreamRef.current;
    if (!s) return;
    s.abortController.abort();
    if (s.current) {
      s.current.onended = null;
      s.current.onerror = null;
      try {
        s.current.pause();
      } catch {
        /* ignore */
      }
      s.current = null;
    }
    s.queue = [];
    for (const url of s.urls) URL.revokeObjectURL(url);
    s.urls = [];
    kokoroStreamRef.current = null;
  }, []);

  // ── Browser-side queue tracking ─────────────────────────────────────────

  const finalizeBrowserIfDone = useCallback(() => {
    const b = browserStreamRef.current;
    if (!b) return;
    if (b.ended && b.pending === 0) {
      browserStreamRef.current = null;
      setSpeaking(false);
    }
  }, []);

  // ── Public cancel ───────────────────────────────────────────────────────

  const cancel = useCallback(() => {
    cancelTokenRef.current += 1;
    cleanupKokoro();
    if (browserSupported) window.speechSynthesis.cancel();
    browserStreamRef.current = null;
    lmStudioBufferRef.current = "";
    setSpeaking(false);
  }, [browserSupported, cleanupKokoro]);

  useEffect(() => {
    return () => {
      cleanupKokoro();
      if (browserSupported) window.speechSynthesis.cancel();
    };
  }, [browserSupported, cleanupKokoro]);

  // ── Provider speak implementations (one-shot) ───────────────────────────

  const speakBrowserOne = useCallback(
    (text: string) => {
      if (!browserSupported) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      const allVoices = window.speechSynthesis.getVoices();
      if (browserVoiceURI) {
        const v = allVoices.find((x) => x.voiceURI === browserVoiceURI);
        if (v) u.voice = v;
      } else {
        const en = allVoices.find((x) => x.lang.toLowerCase().startsWith("en"));
        if (en) u.voice = en;
      }
      u.rate = 1.0;
      u.onstart = () => setSpeaking(true);
      u.onend = () => setSpeaking(false);
      u.onerror = () => setSpeaking(false);
      window.speechSynthesis.speak(u);
    },
    [browserSupported, browserVoiceURI],
  );

  const speakBackendOne = useCallback(
    async (text: string, backendProvider: "kokoro_onnx" | "lm_studio") => {
      cancel();
      const myToken = ++cancelTokenRef.current;
      setError(null);
      setSpeaking(true);
      try {
        const voice =
          backendProvider === "kokoro_onnx"
            ? (kokoroVoice ?? undefined)
            : (lmStudioVoice ?? undefined);
        const blob = await synthesizeSpeech({
          text: sanitizeSurrogates(text),
          voice,
          model: backendProvider === "lm_studio" ? (lmStudioModel ?? undefined) : undefined,
          provider: backendProvider,
        });
        if (cancelTokenRef.current !== myToken) return;
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => {
          URL.revokeObjectURL(url);
          setSpeaking(false);
        };
        audio.onerror = () => {
          URL.revokeObjectURL(url);
          setSpeaking(false);
          setError("audio playback failed");
        };
        await audio.play();
      } catch (e: unknown) {
        if (cancelTokenRef.current !== myToken) return;
        setSpeaking(false);
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [cancel, kokoroVoice, lmStudioVoice, lmStudioModel],
  );

  const speak = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      if (provider === "kokoro_onnx") void speakBackendOne(text, "kokoro_onnx");
      else if (provider === "lm_studio") void speakBackendOne(text, "lm_studio");
      else speakBrowserOne(text);
    },
    [provider, speakBackendOne, speakBrowserOne],
  );

  // ── Streaming variants ──────────────────────────────────────────────────

  const speakStreamingBrowser = useCallback(
    (text: string) => {
      if (!browserSupported) return;
      let bs = browserStreamRef.current;
      if (!bs) {
        bs = { pending: 0, ended: false };
        browserStreamRef.current = bs;
        setSpeaking(true);
      }
      bs.pending += 1;
      const u = new SpeechSynthesisUtterance(text);
      const allVoices = window.speechSynthesis.getVoices();
      if (browserVoiceURI) {
        const v = allVoices.find((x) => x.voiceURI === browserVoiceURI);
        if (v) u.voice = v;
      } else {
        const en = allVoices.find((x) => x.lang.toLowerCase().startsWith("en"));
        if (en) u.voice = en;
      }
      u.rate = 1.0;
      const dec = () => {
        const bs2 = browserStreamRef.current;
        if (bs2) {
          bs2.pending -= 1;
          finalizeBrowserIfDone();
        }
      };
      u.onend = dec;
      u.onerror = dec;
      window.speechSynthesis.speak(u);
    },
    [browserSupported, browserVoiceURI, finalizeBrowserIfDone],
  );

  const speakStreamingKokoro = useCallback(
    (text: string) => {
      let state = kokoroStreamRef.current;
      if (!state) {
        cancelTokenRef.current += 1;
        state = {
          abortController: new AbortController(),
          queue: [],
          current: null,
          urls: [],
          inFlight: 0,
          ended: false,
          synthChain: Promise.resolve(),
        };
        kokoroStreamRef.current = state;
        setError(null);
        setSpeaking(true);
      }

      // Capture for closure stability across the chained promise.
      const s = state;
      s.inFlight += 1;
      const job = async () => {
        try {
          if (kokoroStreamRef.current !== s) return;
          const blob = await synthesizeSpeech({
            text: sanitizeSurrogates(text),
            voice: kokoroVoice ?? undefined,
            provider: "kokoro_onnx",
            signal: s.abortController.signal,
          });
          if (kokoroStreamRef.current !== s) return;
          const url = URL.createObjectURL(blob);
          s.urls.push(url);
          s.queue.push(new Audio(url));
          advanceKokoro();
        } catch (e: unknown) {
          if (kokoroStreamRef.current !== s) return;
          const name =
            typeof e === "object" && e && "name" in e
              ? (e as { name: string }).name
              : "";
          if (name !== "AbortError") {
            setError(e instanceof Error ? e.message : String(e));
          }
        } finally {
          if (kokoroStreamRef.current === s) {
            s.inFlight -= 1;
            finalizeKokoroIfDone();
          }
        }
      };
      // Serialize through the chain so playback order matches submission order.
      s.synthChain = s.synthChain.then(job, job);
    },
    [advanceKokoro, finalizeKokoroIfDone, kokoroVoice],
  );

  const speakStreaming = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      if (provider === "kokoro_onnx") speakStreamingKokoro(text);
      else if (provider === "browser") speakStreamingBrowser(text);
      else if (provider === "lm_studio") {
        // Buffer; flushed on endStreaming via speakBackendOne.
        lmStudioBufferRef.current += (lmStudioBufferRef.current ? " " : "") + text;
      }
    },
    [provider, speakStreamingKokoro, speakStreamingBrowser],
  );

  const endStreaming = useCallback(() => {
    if (provider === "kokoro_onnx") {
      const s = kokoroStreamRef.current;
      if (s) {
        s.ended = true;
        finalizeKokoroIfDone();
      }
    } else if (provider === "browser") {
      const bs = browserStreamRef.current;
      if (bs) {
        bs.ended = true;
        finalizeBrowserIfDone();
      }
    } else if (provider === "lm_studio") {
      const buf = lmStudioBufferRef.current;
      lmStudioBufferRef.current = "";
      if (buf.trim()) void speakBackendOne(buf, "lm_studio");
    }
  }, [provider, finalizeKokoroIfDone, finalizeBrowserIfDone, speakBackendOne]);

  return {
    supported,
    speaking,
    error,
    speak,
    speakStreaming,
    endStreaming,
    cancel,
    voices,
  };
}
