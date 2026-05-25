import { useEffect, useRef } from "react";
import MessageBubble from "./MessageBubble";
import type { ChatMessage } from "../types";

interface Props {
  messages: ChatMessage[];
}

export default function ChatPanel({ messages }: Props) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <div ref={scrollerRef} className="flex-1 overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-3xl space-y-7">
        {messages.length === 0 && (
          <div className="py-24 text-center">
            <div className="font-mono text-2xs uppercase tracking-widest text-muted">
              The Lion watches the Lion
            </div>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
      </div>
    </div>
  );
}
