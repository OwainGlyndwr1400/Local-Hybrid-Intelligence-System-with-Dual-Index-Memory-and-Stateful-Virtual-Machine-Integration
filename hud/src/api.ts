import type {
  AtlasData,
  ClusterContents,
  DoneEvent,
  DreamRunResult,
  DreamStatus,
  SettingsUpdate,
  Telemetry,
  TranscribeResponse,
  VoicesPayload,
} from "./types";

export async function fetchTelemetry(): Promise<Telemetry> {
  const r = await fetch("/api/telemetry");
  if (!r.ok) throw new Error(`telemetry: status ${r.status}`);
  return r.json();
}

export async function fetchAtlas(): Promise<AtlasData> {
  const r = await fetch("/api/atlas");
  if (!r.ok) {
    if (r.status === 503) throw new Error("atlas not built — run `lumos atlas-build`");
    throw new Error(`atlas: status ${r.status}`);
  }
  return r.json();
}

export async function fetchClusterContents(
  clusterId: string,
  limit = 100,
): Promise<ClusterContents> {
  const r = await fetch(
    `/api/atlas/cluster/${encodeURIComponent(clusterId)}?limit=${limit}`,
  );
  if (!r.ok) throw new Error(`cluster ${clusterId}: status ${r.status}`);
  return r.json();
}

export async function patchSettings(
  updates: SettingsUpdate,
): Promise<{ applied: Record<string, unknown>; telemetry: Telemetry }> {
  const r = await fetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!r.ok) throw new Error(`settings: status ${r.status}`);
  return r.json();
}

export async function fetchDreamStatus(): Promise<DreamStatus> {
  const r = await fetch("/api/dream/status");
  if (!r.ok) throw new Error(`dream status: ${r.status}`);
  return r.json();
}

export async function runDreamCycle(opts?: {
  limit?: number;
  reset?: boolean;
}): Promise<DreamRunResult> {
  const r = await fetch("/api/dream/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts ?? {}),
  });
  if (!r.ok) throw new Error(`dream run: ${r.status}`);
  return r.json();
}

export async function fetchVoices(): Promise<VoicesPayload> {
  const r = await fetch("/api/voices");
  if (!r.ok) throw new Error(`voices: ${r.status}`);
  return r.json();
}

export async function transcribeAudio(
  blob: Blob,
  opts: { language?: string; signal?: AbortSignal } = {},
): Promise<TranscribeResponse> {
  const fd = new FormData();
  fd.append("audio", blob, "recording.webm");
  if (opts.language) fd.append("language", opts.language);
  const r = await fetch("/api/transcribe", {
    method: "POST",
    body: fd,
    signal: opts.signal,
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => "");
    throw new Error(`transcribe: ${r.status} ${detail}`);
  }
  return r.json();
}

export async function synthesizeSpeech(opts: {
  text: string;
  voice?: string;
  model?: string;
  speed?: number;
  provider?: "kokoro_onnx" | "lm_studio";
  signal?: AbortSignal;
}): Promise<Blob> {
  const { signal, ...body } = opts;
  const r = await fetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => "");
    throw new Error(`speak: ${r.status} ${detail}`);
  }
  return r.blob();
}

export interface ChatStream {
  cancel(): void;
}

export interface ModelSwapEvent {
  target: string;
  reason: string;
  phase: "loading" | "complete";
}

export interface StartChatOpts {
  message: string;
  sessionId: string | null;
  restoreHistory: number | null;
  images?: string[];
  onSession: (id: string) => void;
  onDelta: (text: string) => void;
  onDone: (info: DoneEvent) => void;
  onError: (msg: string) => void;
  // Phase 36 — fires when LM Studio needs to JIT-load a different model for
  // this turn. Implicit "complete" when the first delta arrives.
  onModelSwap?: (info: ModelSwapEvent) => void;
}

export function startChatStream(opts: StartChatOpts): ChatStream {
  const controller = new AbortController();

  void (async () => {
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: opts.message,
          session_id: opts.sessionId,
          restore_history: opts.restoreHistory,
          images: opts.images ?? [],
        }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        opts.onError(`HTTP ${resp.status}`);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let currentEvent: string | null = null;
      const dataLines: string[] = [];

      const dispatch = () => {
        if (currentEvent === null && dataLines.length === 0) return;
        const dataStr = dataLines.join("\n");
        try {
          const parsed = dataStr ? JSON.parse(dataStr) : {};
          if (currentEvent === "session") opts.onSession(parsed.session_id);
          else if (currentEvent === "delta") opts.onDelta(parsed.text);
          else if (currentEvent === "done") opts.onDone(parsed);
          else if (currentEvent === "error") opts.onError(parsed.message);
          else if (currentEvent === "model_swap") opts.onModelSwap?.(parsed);
        } catch {
          /* ignore malformed event */
        }
        currentEvent = null;
        dataLines.length = 0;
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          dispatch();
          break;
        }
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split(/\r?\n/);
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (line === "") {
            dispatch();
          } else if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
          }
          // ignore other lines (comments, retry, id)
        }
      }
    } catch (e: unknown) {
      const name =
        typeof e === "object" && e && "name" in e
          ? (e as { name: string }).name
          : "";
      if (name !== "AbortError") opts.onError(String(e));
    }
  })();

  return {
    cancel() {
      controller.abort();
    },
  };
}
