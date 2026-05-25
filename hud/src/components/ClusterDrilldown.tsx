import { useEffect, useState } from "react";
import { fetchClusterContents } from "../api";
import type { ClusterContents, ClusterMember } from "../types";

interface Props {
  clusterId: string;
  onClose: () => void;
}

export default function ClusterDrilldown({ clusterId, onClose }: Props) {
  const [data, setData] = useState<ClusterContents | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    setData(null);
    setError(null);
    setExpanded(new Set());
    fetchClusterContents(clusterId)
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, [clusterId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="panel hr-inset absolute inset-x-0 bottom-0 top-[40%] flex flex-col border-t border-line">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="min-w-0 flex-1">
          {data?.cluster ? (
            <div className="flex items-center gap-3">
              <span
                className="size-1.5 shrink-0 rounded-full"
                style={{
                  background:
                    data.cluster.lane === "identity" ? "#7ab8b0" : "#c8b886",
                }}
              />
              <div className="truncate text-sm font-medium tracking-tight">
                {data.cluster.label}
              </div>
              <div className="shrink-0 font-mono text-2xs text-muted">
                {data.cluster.lane} · {data.total_members.toLocaleString()} chunks
              </div>
            </div>
          ) : (
            <div className="font-mono text-2xs uppercase tracking-widest text-muted">
              loading…
            </div>
          )}
        </div>
        <button
          onClick={onClose}
          type="button"
          className="ml-3 font-mono text-2xs text-muted transition-colors hover:text-fg"
          title="Close (Esc)"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {error && (
          <div className="font-mono text-2xs text-err">{error}</div>
        )}
        {!data && !error && (
          <div className="font-mono text-2xs text-muted">loading members…</div>
        )}
        {data && (
          <div className="space-y-2">
            {data.members.map((m) => (
              <MemberRow
                key={m.chunk_id}
                m={m}
                lane={data.cluster.lane}
                expanded={expanded.has(m.chunk_id)}
                onToggle={() => toggle(m.chunk_id)}
              />
            ))}
            {data.total_members > data.shown && (
              <div className="pt-2 font-mono text-2xs text-muted">
                showing {data.shown.toLocaleString()} of{" "}
                {data.total_members.toLocaleString()}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function MemberRow({
  m,
  lane,
  expanded,
  onToggle,
}: {
  m: ClusterMember;
  lane: "identity" | "knowledge";
  expanded: boolean;
  onToggle: () => void;
}) {
  const title =
    lane === "identity"
      ? m.conversation_title || "untitled"
      : m.subject || m.sigil || "ping";
  const date =
    typeof m.create_time_first === "number" && m.create_time_first > 0
      ? new Date(m.create_time_first * 1000).toISOString().slice(0, 10)
      : "";
  const text = (m.text || "").trim();
  const snippet = text.split(/\r?\n/)[0]?.slice(0, 140) ?? "";

  const meta: string[] = [];
  if (lane === "knowledge" && m.agent) meta.push(m.agent);
  if (lane === "knowledge" && m.source) meta.push(m.source);
  if (lane === "knowledge" && typeof m.urgency_score === "number")
    meta.push(`urg ${m.urgency_score}/${m.urgency_weight ?? 0}`);
  if (m.dream_consolidated) meta.push("dream");
  if (m.pendinium_anchor) meta.push(`p${m.pendinium_anchor}`);

  return (
    <div className="rounded-sm border border-line bg-surface/40 px-3 py-2">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-baseline gap-3 text-left"
      >
        <span className="min-w-0 flex-1 truncate text-sm">{title}</span>
        <span className="shrink-0 font-mono text-2xs text-muted">
          {date}
        </span>
        <span className="shrink-0 font-mono text-[10px] text-dim">
          {expanded ? "−" : "+"}
        </span>
      </button>
      {meta.length > 0 && (
        <div className="mt-0.5 font-mono text-2xs text-muted">
          {meta.join(" · ")}
        </div>
      )}
      {!expanded && snippet && (
        <div className="mt-1 truncate font-mono text-2xs text-dim">
          {snippet}
        </div>
      )}
      {expanded && text && (
        <pre className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap font-mono text-2xs leading-relaxed text-fg">
          {text}
        </pre>
      )}
    </div>
  );
}
