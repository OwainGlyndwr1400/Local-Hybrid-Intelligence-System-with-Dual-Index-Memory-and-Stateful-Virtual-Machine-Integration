import { useCallback, useEffect, useRef, useState } from "react";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import type { AttachedImage, STTProvider } from "../types";

interface Props {
  onSend: (text: string, images: AttachedImage[]) => void;
  disabled: boolean;
  sttProvider: STTProvider;
}

const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024; // 10MB

async function fileToAttachment(file: File): Promise<AttachedImage | null> {
  if (!file.type.startsWith("image/")) return null;
  if (file.size > MAX_IMAGE_BYTES) return null;
  const data_url: string = await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
  return {
    data_url,
    mime: file.type,
    name: file.name || "pasted-image",
    size: file.size,
  };
}

export default function Composer({ onSend, disabled, sttProvider }: Props) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<AttachedImage[]>([]);
  const [dragging, setDragging] = useState(false);
  const [lockedTalk, setLockedTalk] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleTranscript = useCallback((transcript: string) => {
    setText((prev) => (prev ? prev.trimEnd() + " " + transcript : transcript));
    setTimeout(() => taRef.current?.focus(), 0);
  }, []);

  const stt = useSpeechRecognition({
    provider: sttProvider,
    onFinalTranscript: handleTranscript,
  });

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  }, [text]);

  const submit = () => {
    if (disabled) return;
    if (!text.trim() && attachments.length === 0) return;
    const messageText = text.trim() || "[image attached]";
    onSend(messageText, attachments);
    setText("");
    setAttachments([]);
  };

  const addFiles = useCallback(
    async (files: FileList | File[] | null | undefined) => {
      if (!files) return;
      const newOnes: AttachedImage[] = [];
      for (const f of Array.from(files)) {
        if (attachments.length + newOnes.length >= MAX_IMAGES) break;
        const att = await fileToAttachment(f);
        if (att) newOnes.push(att);
      }
      if (newOnes.length > 0) {
        setAttachments((a) => [...a, ...newOnes].slice(0, MAX_IMAGES));
      }
    },
    [attachments.length],
  );

  // Paste image from clipboard.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      if (disabled) return;
      const items = e.clipboardData?.items;
      if (!items) return;
      const files: File[] = [];
      for (const it of items) {
        if (it.kind === "file" && it.type.startsWith("image/")) {
          const f = it.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length > 0) {
        e.preventDefault();
        void addFiles(files);
      }
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [disabled, addFiles]);

  const removeAttachment = (idx: number) =>
    setAttachments((a) => a.filter((_, i) => i !== idx));

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const onTalkDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (!stt.supported || lockedTalk) return;
    stt.start();
  };
  const onTalkUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (lockedTalk) return;
    stt.stop();
  };
  const onTalkLeave = (e: React.PointerEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (lockedTalk) return;
    if (stt.listening) stt.stop();
  };
  const onTalkContext = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    if (!stt.supported) return;
    if (lockedTalk) {
      stt.stop();
      setLockedTalk(false);
    } else {
      stt.start();
      setLockedTalk(true);
    }
  };

  // If lock mode is on and recognition ended (e.g., long silence), turn lock off.
  useEffect(() => {
    if (lockedTalk && !stt.listening) {
      setLockedTalk(false);
    }
  }, [lockedTalk, stt.listening]);

  const talkLabel = stt.pending
    ? "transcribing"
    : stt.listening
      ? lockedTalk
        ? "listening · locked"
        : "listening"
      : stt.supported
        ? "hold to talk"
        : "voice unsupported";

  return (
    <div
      className={
        "shrink-0 border-t border-line px-8 py-4 " +
        (dragging ? "bg-accent/5" : "")
      }
      onDragOver={(e) => {
        if (disabled) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (disabled) return;
        void addFiles(e.dataTransfer?.files);
      }}
    >
      <div className="mx-auto max-w-3xl">
        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachments.map((a, i) => (
              <div
                key={i}
                className="group relative h-20 w-20 overflow-hidden rounded-sm border border-line bg-surface"
              >
                <img
                  src={a.data_url}
                  alt={a.name}
                  className="size-full object-cover"
                />
                <button
                  type="button"
                  onClick={() => removeAttachment(i)}
                  className="absolute right-0.5 top-0.5 rounded-full bg-bg/80 px-1.5 font-mono text-2xs text-muted hover:text-fg"
                  title="Remove"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-end gap-3 rounded-md border border-line bg-surface px-4 py-3 transition-colors focus-within:border-accent/40">
          <button
            type="button"
            onPointerDown={onTalkDown}
            onPointerUp={onTalkUp}
            onPointerLeave={onTalkLeave}
            onContextMenu={onTalkContext}
            disabled={!stt.supported || disabled || stt.pending}
            title={
              stt.supported
                ? "Hold to talk · right-click to toggle lock-on"
                : "Speech recognition not supported in this browser"
            }
            className={
              "shrink-0 select-none rounded-full border px-3 py-1.5 font-mono text-2xs uppercase tracking-widest transition-colors " +
              (stt.pending
                ? "border-signal/60 bg-signal/10 text-signal"
                : stt.listening
                  ? "border-accent/70 bg-accent/15 text-accent"
                  : stt.supported
                    ? "border-line text-muted hover:border-accent/40 hover:text-fg"
                    : "border-line/40 text-muted/60 cursor-not-allowed")
            }
          >
            <span className="mr-1.5 inline-block">
              <span
                className={
                  "inline-block size-1.5 rounded-full align-middle " +
                  (stt.pending
                    ? "bg-signal animate-pulse"
                    : stt.listening
                      ? "bg-accent animate-pulse"
                      : "bg-muted")
                }
              />
            </span>
            {talkLabel}
          </button>
          <textarea
            ref={taRef}
            value={text + (stt.interim ? (text ? " " : "") + stt.interim : "")}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder={disabled ? "…" : "Send a message"}
            disabled={disabled}
            className="max-h-48 min-h-[1.5rem] flex-1 resize-none bg-transparent text-sm leading-relaxed outline-none placeholder:text-muted disabled:opacity-50"
          />
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(e) => {
              void addFiles(e.target.files);
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || attachments.length >= MAX_IMAGES}
            title={
              attachments.length >= MAX_IMAGES
                ? `Maximum ${MAX_IMAGES} images per message`
                : "Attach image · or paste / drag-drop"
            }
            className="font-mono text-2xs uppercase tracking-widest text-muted transition-colors hover:text-fg disabled:opacity-30"
          >
            attach
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={disabled || (!text.trim() && attachments.length === 0)}
            className="font-mono text-2xs uppercase tracking-widest text-accent transition-opacity hover:text-fg disabled:cursor-not-allowed disabled:opacity-30"
          >
            send
          </button>
        </div>
        <div className="mt-2 flex items-center justify-between font-mono text-2xs text-muted">
          <span>
            enter to send · shift+enter for newline · paste / drag images
          </span>
          {stt.error && <span className="text-err">voice · {stt.error}</span>}
        </div>
      </div>
    </div>
  );
}
