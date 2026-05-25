import { useCallback, useEffect, useRef, useState } from "react";
import AtlasPanel from "./components/AtlasPanel";
import Header from "./components/Header";
import ChatPanel from "./components/ChatPanel";
import Composer from "./components/Composer";
import ResizeHandle from "./components/ResizeHandle";
import SettingsModal from "./components/SettingsModal";
import TelemetryPanel from "./components/TelemetryPanel";
import { useSpeechSynthesis } from "./hooks/useSpeechSynthesis";
import {
  fetchDreamStatus,
  fetchTelemetry,
  fetchVoices,
  runDreamCycle,
  startChatStream,
} from "./api";
import type {
  AttachedImage,
  ChatMessage,
  DoneEvent,
  STTProvider,
  Telemetry,
  VoiceProvider,
  VoicesPayload,
} from "./types";

function newId(): string {
  return crypto.randomUUID();
}

// Light cleanup before TTS so the synthesizer doesn't try to pronounce
// markdown punctuation, code fences, or sigil glyphs.
function stripMarkdownForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/[*_~`#>]/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[🜂🜁🜃🜄🜏😏🌟🦁⚔📖🜂]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Extract complete sentences (ending in . ! ? followed by whitespace) from
 * the front of a buffer. Returns the ready sentences and the unconsumed
 * remainder (which may grow with subsequent stream deltas).
 */
function takeReadySentences(buffer: string): {
  ready: string[];
  pending: string;
} {
  const sentences: string[] = [];
  let cursor = 0;
  const regex = /[.!?]+\s+/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(buffer)) !== null) {
    const sentence = buffer.slice(cursor, match.index + match[0].length).trim();
    if (sentence) sentences.push(sentence);
    cursor = match.index + match[0].length;
  }
  return { ready: sentences, pending: buffer.slice(cursor) };
}

export default function App() {
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [lastDone, setLastDone] = useState<DoneEvent | null>(null);
  const [atlasCollapsed, setAtlasCollapsed] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [dreamPending, setDreamPending] = useState(0);
  const [dreamRunning, setDreamRunning] = useState(false);
  const [voiceAutoplay, setVoiceAutoplay] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("lumos.voice.autoplay") === "1";
  });
  const [voiceProvider, setVoiceProvider] = useState<VoiceProvider>(() => {
    if (typeof window === "undefined") return "browser";
    const v = window.localStorage.getItem("lumos.voice.provider");
    if (v === "lm_studio" || v === "kokoro_onnx" || v === "browser") return v;
    return "browser";
  });
  const [browserVoiceURI, setBrowserVoiceURI] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem("lumos.voice.browser") || null;
  });
  const [lmStudioVoice, setLMStudioVoice] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem("lumos.voice.lm_studio") || null;
  });
  const [kokoroVoice, setKokoroVoice] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem("lumos.voice.kokoro") || "af_bella";
  });
  const [sttProvider, setSTTProvider] = useState<STTProvider>(() => {
    if (typeof window === "undefined") return "browser";
    const v = window.localStorage.getItem("lumos.stt.provider");
    return v === "whisper" ? "whisper" : "browser";
  });

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("lumos.stt.provider", sttProvider);
    }
  }, [sttProvider]);
  const [voicesPayload, setVoicesPayload] = useState<VoicesPayload | null>(null);

  const tts = useSpeechSynthesis({
    provider: voiceProvider,
    browserVoiceURI,
    lmStudioVoice,
    lmStudioModel: voicesPayload?.model ?? null,
    kokoroVoice,
  });

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(
        "lumos.voice.autoplay",
        voiceAutoplay ? "1" : "0",
      );
    }
  }, [voiceAutoplay]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("lumos.voice.provider", voiceProvider);
    }
  }, [voiceProvider]);

  useEffect(() => {
    if (typeof window !== "undefined" && browserVoiceURI) {
      window.localStorage.setItem("lumos.voice.browser", browserVoiceURI);
    }
  }, [browserVoiceURI]);

  useEffect(() => {
    if (typeof window !== "undefined" && lmStudioVoice) {
      window.localStorage.setItem("lumos.voice.lm_studio", lmStudioVoice);
    }
  }, [lmStudioVoice]);

  useEffect(() => {
    if (typeof window !== "undefined" && kokoroVoice) {
      window.localStorage.setItem("lumos.voice.kokoro", kokoroVoice);
    }
  }, [kokoroVoice]);

  useEffect(() => {
    // Ensure we always have a sensible Kokoro voice id even before
    // /api/voices has been fetched (so the dropdown is functional).
    if (!lmStudioVoice) setLMStudioVoice("af_bella");
    fetchVoices()
      .then((v) => {
        setVoicesPayload(v);
        if (!lmStudioVoice && v.default_voice) {
          setLMStudioVoice(v.default_voice);
        }
      })
      .catch(() => setVoicesPayload(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [atlasWidth, setAtlasWidth] = useState<number>(() => {
    if (typeof window === "undefined") return 420;
    const v = window.localStorage.getItem("lumos.panel.atlas.width");
    return v ? Math.max(80, Math.min(1200, Number(v) || 420)) : 420;
  });
  const [telemetryWidth, setTelemetryWidth] = useState<number>(() => {
    if (typeof window === "undefined") return 320;
    const v = window.localStorage.getItem("lumos.panel.telemetry.width");
    return v ? Math.max(200, Math.min(800, Number(v) || 320)) : 320;
  });
  const hasRestoredRef = useRef(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("lumos.panel.atlas.width", String(atlasWidth));
    }
  }, [atlasWidth]);
  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(
        "lumos.panel.telemetry.width",
        String(telemetryWidth),
      );
    }
  }, [telemetryWidth]);

  const refreshDreamStatus = useCallback(async () => {
    try {
      const s = await fetchDreamStatus();
      setDreamPending(s.pending);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    void refreshDreamStatus();
  }, [refreshDreamStatus]);

  useEffect(() => {
    fetchTelemetry()
      .then(setTelemetry)
      .catch(() => setTelemetry(null));
  }, []);

  const handleSend = (text: string, images: AttachedImage[] = []) => {
    const trimmed = text.trim();
    if ((!trimmed && images.length === 0) || isStreaming) return;

    const userMsg: ChatMessage = {
      id: newId(),
      role: "user",
      content: trimmed || "[image attached]",
      images: images.length > 0 ? images : undefined,
      timestamp: Date.now(),
    };
    const assistantMsg: ChatMessage = {
      id: newId(),
      role: "assistant",
      content: "",
      timestamp: Date.now(),
    };
    setMessages((m) => [...m, userMsg, assistantMsg]);
    setIsStreaming(true);

    const restore = !hasRestoredRef.current ? 10 : null;
    hasRestoredRef.current = true;

    // Cancel any previous turn's TTS before starting this turn's stream.
    if (voiceAutoplay && tts.supported) tts.cancel();

    // Plain-variable accumulators so the onDone TTS branch never depends on
    // React state having been committed by the time it runs.
    let fullResponse = "";
    let speechBuffer = "";

    startChatStream({
      message: trimmed || "[image attached]",
      sessionId,
      restoreHistory: restore,
      images: images.map((i) => i.data_url),
      onSession: (id) => setSessionId(id),
      onModelSwap: (info) => {
        // Stash on the pending assistant message so the bubble can render
        // "loading <target>…" until the first delta arrives.
        setMessages((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          if (last && last.role === "assistant") {
            next[next.length - 1] = {
              ...last,
              modelSwapPending: { target: info.target, reason: info.reason },
            };
          }
          return next;
        });
      },
      onDelta: (delta) => {
        fullResponse += delta;
        setMessages((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          next[next.length - 1] = {
            ...last,
            content: last.content + delta,
            // Clear swap-pending on first content arrival.
            modelSwapPending: undefined,
          };
          return next;
        });
        // Sentence-streaming TTS: peel off complete sentences as they form
        // and pipe them to the TTS streaming pipeline.
        if (voiceAutoplay && tts.supported) {
          speechBuffer += delta;
          const { ready, pending } = takeReadySentences(speechBuffer);
          speechBuffer = pending;
          for (const sentence of ready) {
            const cleaned = stripMarkdownForSpeech(sentence);
            if (cleaned) tts.speakStreaming(cleaned);
          }
        }
      },
      onDone: (info) => {
        setLastDone(info);
        setMessages((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, doneInfo: info };
          return next;
        });
        setIsStreaming(false);
        void refreshDreamStatus();
        if (voiceAutoplay && tts.supported) {
          if (speechBuffer.trim()) {
            const cleaned = stripMarkdownForSpeech(speechBuffer);
            if (cleaned) tts.speakStreaming(cleaned);
          }
          tts.endStreaming();
        }
      },
      onError: (msg) => {
        setMessages((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, error: msg };
          return next;
        });
        setIsStreaming(false);
      },
    });
  };

  const handleRunDream = useCallback(async () => {
    if (dreamRunning || dreamPending === 0) return;
    setDreamRunning(true);
    try {
      await runDreamCycle();
    } catch (e) {
      console.error("dream cycle failed", e);
    } finally {
      setDreamRunning(false);
      void refreshDreamStatus();
    }
  }, [dreamRunning, dreamPending, refreshDreamStatus]);

  return (
    <div className="flex h-full flex-col">
      <Header
        telemetry={telemetry}
        sessionId={sessionId}
        streaming={isStreaming}
        dreamPending={dreamPending}
        dreamRunning={dreamRunning}
        onOpenSettings={() => setSettingsOpen(true)}
        onRunDream={handleRunDream}
      />
      <div className="flex flex-1 overflow-hidden">
        <AtlasPanel
          lastDone={lastDone}
          collapsed={atlasCollapsed}
          onToggle={() => setAtlasCollapsed((c) => !c)}
          width={atlasCollapsed ? 36 : atlasWidth}
        />
        {!atlasCollapsed && (
          <ResizeHandle
            onResize={(dx) => {
              setAtlasWidth((w) => {
                const maxAtlas = Math.max(
                  80,
                  window.innerWidth - telemetryWidth - 240,
                );
                return Math.max(80, Math.min(maxAtlas, w + dx));
              });
            }}
          />
        )}
        <div className="flex min-w-[240px] flex-1 flex-col">
          <ChatPanel messages={messages} />
          <Composer
            onSend={handleSend}
            disabled={isStreaming}
            sttProvider={sttProvider}
          />
        </div>
        <ResizeHandle
          onResize={(dx) => {
            setTelemetryWidth((w) => {
              const maxTel = Math.max(
                200,
                window.innerWidth - (atlasCollapsed ? 36 : atlasWidth) - 240,
              );
              return Math.max(200, Math.min(maxTel, w - dx));
            });
          }}
        />
        <TelemetryPanel
          telemetry={telemetry}
          lastDone={lastDone}
          width={telemetryWidth}
        />
      </div>
      {settingsOpen && (
        <SettingsModal
          telemetry={telemetry}
          onClose={() => setSettingsOpen(false)}
          onApplied={(t) => setTelemetry(t)}
          voiceAutoplay={voiceAutoplay}
          onVoiceAutoplayChange={setVoiceAutoplay}
          ttsSupported={tts.supported}
          ttsSpeaking={tts.speaking}
          ttsError={tts.error}
          onCancelTTS={tts.cancel}
          voiceProvider={voiceProvider}
          onVoiceProviderChange={setVoiceProvider}
          browserVoices={tts.voices}
          browserVoiceURI={browserVoiceURI}
          onBrowserVoiceChange={setBrowserVoiceURI}
          lmStudioVoice={lmStudioVoice}
          onLMStudioVoiceChange={setLMStudioVoice}
          kokoroVoice={kokoroVoice}
          onKokoroVoiceChange={setKokoroVoice}
          voicesPayload={voicesPayload}
          onPreviewTTS={(t: string) => tts.speak(t)}
          sttProvider={sttProvider}
          onSTTProviderChange={setSTTProvider}
        />
      )}
    </div>
  );
}
