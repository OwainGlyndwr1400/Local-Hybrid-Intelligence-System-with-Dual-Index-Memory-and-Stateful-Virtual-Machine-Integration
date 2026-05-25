import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ComponentPropsWithoutRef } from "react";
import type { ChatMessage } from "../types";

interface Props {
  message: ChatMessage;
}

export default function MessageBubble({ message }: Props) {
  const isUser = message.role === "user";
  const label = isUser ? "You" : "Lumos";

  return (
    <div className={isUser ? "ml-auto max-w-[80%]" : "max-w-full"}>
      <div className="mb-1.5 font-mono text-2xs uppercase tracking-widest text-muted">
        {label}
      </div>
      <div
        className={
          isUser
            ? "rounded-md border border-line bg-surface px-4 py-2.5 text-sm leading-relaxed"
            : "text-sm leading-relaxed"
        }
      >
        {isUser && message.images && message.images.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {message.images.map((img, i) => (
              <img
                key={i}
                src={img.data_url}
                alt={img.name}
                className="max-h-48 max-w-[12rem] rounded-sm border border-line object-contain"
              />
            ))}
          </div>
        )}
        {isUser ? (
          <div className="whitespace-pre-wrap">{message.content}</div>
        ) : message.content ? (
          // While streaming (doneInfo not yet set), render as plain pre-wrap
          // text. ReactMarkdown's AST re-parse cost on every delta scales O(N)
          // with message length → O(N²) total work over the stream. Paragraph
          // boundaries restructure the AST and surface as visible stalls.
          // Switch to markdown rendering once the stream completes.
          message.doneInfo ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
              {message.content}
            </ReactMarkdown>
          ) : (
            <div className="whitespace-pre-wrap">{message.content}</div>
          )
        ) : message.modelSwapPending ? (
          // Phase 36 — JIT model swap in progress before any content arrives.
          // Render the target + reason instead of a bare ellipsis so the
          // operator knows what's happening (vs feeling like the app froze).
          <span
            className="font-mono text-2xs text-accent"
            title={`Reason: ${message.modelSwapPending.reason}`}
          >
            🔄 loading {message.modelSwapPending.target.split("/").pop()}… (~10-15s)
          </span>
        ) : (
          <span className="text-muted">…</span>
        )}
      </div>
      {message.error && (
        <div className="mt-2 font-mono text-2xs text-err">error · {message.error}</div>
      )}
      {message.doneInfo && (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-2xs text-muted">
          <span>
            retrieved {message.doneInfo.retrieved.identity.length} memory ·{" "}
            {message.doneInfo.retrieved.knowledge.length} knowledge
          </span>
          {message.doneInfo.tokens.total != null && (
            <span>
              tokens {message.doneInfo.tokens.total.toLocaleString()}
              {message.doneInfo.tokens.prompt != null &&
                message.doneInfo.tokens.completion != null && (
                  <>
                    {" "}
                    ({message.doneInfo.tokens.prompt.toLocaleString()} +{" "}
                    {message.doneInfo.tokens.completion.toLocaleString()})
                  </>
                )}
            </span>
          )}
          {message.doneInfo.tool_calls && message.doneInfo.tool_calls.length > 0 && (
            <span className="text-accent">
              tools · {message.doneInfo.tool_calls.map((t) => t.name).join(", ")}
            </span>
          )}
        </div>
      )}
      {message.doneInfo?.tool_calls && message.doneInfo.tool_calls.length > 0 && (
        <details className="mt-2 rounded-sm border border-line bg-surface/40">
          <summary className="cursor-pointer px-3 py-1.5 font-mono text-2xs uppercase tracking-widest text-muted hover:text-fg">
            tool calls ({message.doneInfo.tool_calls.length})
          </summary>
          <div className="space-y-2 border-t border-line px-3 py-2">
            {message.doneInfo.tool_calls.map((tc, i) => (
              <div key={i} className="font-mono text-2xs">
                <div className="text-accent">
                  {tc.name}({Object.keys(tc.arguments).join(", ")})
                </div>
                <div className="mt-0.5 text-muted">
                  args: {JSON.stringify(tc.arguments).slice(0, 200)}
                </div>
                <div className="mt-0.5 text-dim">
                  {tc.result_preview.slice(0, 240)}
                  {tc.result_preview.length >= 240 ? "…" : ""}
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

type MdProps = ComponentPropsWithoutRef<"div">;

const mdComponents = {
  h1: (p: MdProps) => (
    <h1 className="mt-4 mb-2 text-base font-medium text-fg" {...p} />
  ),
  h2: (p: MdProps) => (
    <h2 className="mt-3 mb-2 text-sm font-medium text-fg" {...p} />
  ),
  h3: (p: MdProps) => (
    <h3 className="mt-2 mb-1 text-sm font-medium text-fg" {...p} />
  ),
  p: (p: MdProps) => <p className="my-2 leading-relaxed" {...p} />,
  ul: (p: MdProps) => <ul className="my-2 list-disc space-y-1 pl-5" {...p} />,
  ol: (p: MdProps) => <ol className="my-2 list-decimal space-y-1 pl-5" {...p} />,
  li: (p: MdProps) => <li className="leading-relaxed" {...p} />,
  code: (p: ComponentPropsWithoutRef<"code">) => (
    <code
      className="rounded bg-surface px-1 py-0.5 font-mono text-[0.85em]"
      {...p}
    />
  ),
  pre: (p: MdProps) => (
    <pre
      className="my-2 overflow-x-auto rounded-md border border-line bg-surface p-3 font-mono text-xs"
      {...p}
    />
  ),
  table: (p: ComponentPropsWithoutRef<"table">) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs" {...p} />
    </div>
  ),
  thead: (p: ComponentPropsWithoutRef<"thead">) => (
    <thead className="bg-surface" {...p} />
  ),
  th: (p: ComponentPropsWithoutRef<"th">) => (
    <th
      className="border border-line px-2 py-1 text-left font-medium"
      {...p}
    />
  ),
  td: (p: ComponentPropsWithoutRef<"td">) => (
    <td className="border border-line px-2 py-1" {...p} />
  ),
  hr: () => <hr className="my-3 border-line" />,
  strong: (p: MdProps) => <strong className="font-medium text-fg" {...p} />,
  em: (p: MdProps) => <em className="italic" {...p} />,
  blockquote: (p: MdProps) => (
    <blockquote
      className="my-2 border-l-2 border-line pl-4 italic text-dim"
      {...p}
    />
  ),
  a: (p: ComponentPropsWithoutRef<"a">) => (
    <a
      className="text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
      target="_blank"
      rel="noreferrer"
      {...p}
    />
  ),
};
