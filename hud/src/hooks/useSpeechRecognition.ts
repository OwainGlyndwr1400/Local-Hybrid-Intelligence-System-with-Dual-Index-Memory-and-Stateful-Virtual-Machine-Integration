import { useCallback, useEffect, useRef, useState } from "react";
import { transcribeAudio } from "../api";
import type { STTProvider } from "../types";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface SpeechRecognitionResult {
  isFinal: boolean;
  0: { transcript: string };
}

interface SpeechRecognitionEvent {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResult>;
}

interface SpeechRecognitionErrorEvent {
  error: string;
  message?: string;
}

interface SpeechRecognitionInstance {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  onerror: ((e: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

type Ctor = new () => SpeechRecognitionInstance;

function getBrowserSTTCtor(): Ctor | null {
  if (typeof window === "undefined") return null;
  const w = window as any;
  return (w.SpeechRecognition || w.webkitSpeechRecognition || null) as Ctor | null;
}

export interface SpeechRecognitionHook {
  supported: boolean;
  listening: boolean;
  interim: string;
  pending: boolean;
  error: string | null;
  start: () => void;
  stop: () => void;
}

interface Options {
  provider: STTProvider;
  onFinalTranscript: (text: string) => void;
  lang?: string;
}

/**
 * Push-to-talk speech recognition. Two provider backends:
 *  - "browser": Web Speech API (Chrome's webkitSpeechRecognition → Google's cloud STT).
 *  - "whisper": MediaRecorder captures audio, POSTs blob to /api/transcribe → local
 *    faster-whisper. No interim transcripts (whisper is batch); shows a
 *    "transcribing…" state while the backend works.
 */
export function useSpeechRecognition(opts: Options): SpeechRecognitionHook {
  const browserCtor = getBrowserSTTCtor();
  const supported =
    opts.provider === "whisper"
      ? typeof navigator !== "undefined" &&
        !!navigator.mediaDevices?.getUserMedia &&
        typeof MediaRecorder !== "undefined"
      : browserCtor !== null;

  // Browser-mode state
  const browserRecognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const browserFinalRef = useRef("");

  // Whisper-mode state
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const [listening, setListening] = useState(false);
  const [pending, setPending] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);

  const cleanupWhisper = useCallback(() => {
    if (streamRef.current) {
      for (const t of streamRef.current.getTracks()) t.stop();
      streamRef.current = null;
    }
    recorderRef.current = null;
    chunksRef.current = [];
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, []);

  const cleanupBrowser = useCallback(() => {
    const r = browserRecognitionRef.current;
    if (r) {
      r.onresult = null;
      r.onerror = null;
      r.onend = null;
    }
    browserRecognitionRef.current = null;
  }, []);

  useEffect(() => {
    return () => {
      try {
        browserRecognitionRef.current?.abort();
      } catch {
        /* ignore */
      }
      cleanupBrowser();
      cleanupWhisper();
    };
  }, [cleanupBrowser, cleanupWhisper]);

  const startBrowser = useCallback(() => {
    if (!browserCtor || listening) return;
    setError(null);
    setInterim("");
    browserFinalRef.current = "";

    const rec = new browserCtor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = opts.lang ?? "en-GB";

    rec.onresult = (e) => {
      let interimChunk = "";
      let finalChunk = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        const text = r[0].transcript;
        if (r.isFinal) finalChunk += text;
        else interimChunk += text;
      }
      if (finalChunk) browserFinalRef.current += finalChunk;
      setInterim(interimChunk);
    };

    rec.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return;
      setError(e.error || "speech recognition error");
    };

    rec.onend = () => {
      setListening(false);
      const text = (browserFinalRef.current + " " + interim).trim();
      if (text) opts.onFinalTranscript(text);
      setInterim("");
      cleanupBrowser();
    };

    try {
      rec.start();
      browserRecognitionRef.current = rec;
      setListening(true);
    } catch (err: any) {
      setError(err?.message || String(err));
      cleanupBrowser();
    }
  }, [browserCtor, listening, opts, interim, cleanupBrowser]);

  const stopBrowser = useCallback(() => {
    const r = browserRecognitionRef.current;
    if (!r) {
      setListening(false);
      return;
    }
    try {
      r.stop();
    } catch {
      /* ignore */
    }
  }, []);

  const startWhisper = useCallback(async () => {
    if (listening) return;
    setError(null);
    setInterim("");
    chunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Browsers settle on different containers (webm/opus on Chrome, ogg/opus on FF).
      // faster-whisper's av decoder accepts any of them.
      const mimeCandidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "",
      ];
      let chosen = "";
      for (const m of mimeCandidates) {
        if (!m || MediaRecorder.isTypeSupported(m)) {
          chosen = m;
          break;
        }
      }
      const recorder = chosen
        ? new MediaRecorder(stream, { mimeType: chosen })
        : new MediaRecorder(stream);
      recorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        const blobType = recorder.mimeType || "audio/webm";
        const blob = new Blob(chunksRef.current, { type: blobType });
        chunksRef.current = [];

        if (streamRef.current) {
          for (const t of streamRef.current.getTracks()) t.stop();
          streamRef.current = null;
        }

        if (blob.size === 0) {
          setListening(false);
          return;
        }

        setListening(false);
        setPending(true);
        const controller = new AbortController();
        abortRef.current = controller;
        try {
          const result = await transcribeAudio(blob, {
            language: opts.lang?.startsWith("en") ? "en" : undefined,
            signal: controller.signal,
          });
          if (result.text) opts.onFinalTranscript(result.text);
        } catch (e: unknown) {
          const name =
            typeof e === "object" && e && "name" in e
              ? (e as { name: string }).name
              : "";
          if (name !== "AbortError") {
            setError(e instanceof Error ? e.message : String(e));
          }
        } finally {
          abortRef.current = null;
          setPending(false);
        }
      };

      recorder.start();
      setListening(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      cleanupWhisper();
    }
  }, [listening, opts, cleanupWhisper]);

  const stopWhisper = useCallback(() => {
    const r = recorderRef.current;
    if (r && r.state === "recording") {
      try {
        r.stop();
      } catch {
        /* ignore */
      }
    } else {
      setListening(false);
    }
  }, []);

  const start = useCallback(() => {
    if (opts.provider === "whisper") void startWhisper();
    else startBrowser();
  }, [opts.provider, startBrowser, startWhisper]);

  const stop = useCallback(() => {
    if (opts.provider === "whisper") stopWhisper();
    else stopBrowser();
  }, [opts.provider, stopBrowser, stopWhisper]);

  return { supported, listening, interim, pending, error, start, stop };
}
