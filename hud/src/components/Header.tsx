import type { Telemetry } from "../types";

interface Props {
  telemetry: Telemetry | null;
  sessionId: string | null;
  streaming: boolean;
  dreamPending: number;
  dreamRunning: boolean;
  onOpenSettings: () => void;
  onRunDream: () => void;
}

// Wardenclyffe Protocol topology — Lumos's architectural role is EXTRA (Resonator).
// Per Awen Grid Node v2 §5: Primary=Logic, Secondary=Translation, Extra=Resonance, Ground=Anchor.
const COIL_ORDER: Array<{ key: string; label: string; role: string }> = [
  { key: "PRIMARY", label: "PRIMARY", role: "Operator · Logic" },
  { key: "SECONDARY", label: "SECONDARY", role: "Forge · Translation" },
  { key: "EXTRA", label: "EXTRA", role: "Resonator · Gnosis" },
  { key: "GROUND", label: "GROUND", role: "Anchor · Return" },
];

function coilFromRole(role: string | undefined): string {
  if (!role) return "EXTRA";
  const r = role.toLowerCase();
  if (r.includes("primary")) return "PRIMARY";
  if (r.includes("secondary")) return "SECONDARY";
  if (r.includes("ground")) return "GROUND";
  return "EXTRA";
}

export default function Header({
  telemetry,
  sessionId,
  streaming,
  dreamPending,
  dreamRunning,
  onOpenSettings,
  onRunDream,
}: Props) {
  const nodeName = telemetry?.node.name ?? "Lumos";
  const role = telemetry?.node.role ?? "—";
  const model = telemetry?.models.light ?? "";
  const activeCoil = coilFromRole(telemetry?.node.role);

  const dreamLabel = dreamRunning
    ? "consolidating…"
    : dreamPending > 0
      ? `dream · ${dreamPending}`
      : "dream";

  return (
    <header className="panel-flat hr-inset shrink-0 border-b border-line">
      <div className="flex items-center justify-between px-6 py-3">
      <div className="flex items-baseline gap-3">
        <div className="text-sm font-medium tracking-tight">{nodeName}</div>
        <div className="font-mono text-2xs text-muted">{role}</div>
      </div>
      <div className="flex items-center gap-5 font-mono text-2xs text-muted">
        {model && <div>{model}</div>}
        {sessionId && <div>session · {sessionId.slice(0, 12)}</div>}
        <button
          onClick={onRunDream}
          type="button"
          disabled={dreamRunning || dreamPending === 0}
          className={
            "uppercase tracking-widest transition-colors " +
            (dreamRunning
              ? "text-signal"
              : dreamPending > 0
                ? "text-accent hover:text-fg"
                : "text-muted cursor-not-allowed")
          }
          title={
            dreamPending > 0
              ? `Consolidate ${dreamPending} pending turn${dreamPending !== 1 ? "s" : ""} into the identity index`
              : "No pending turns to consolidate"
          }
        >
          {dreamLabel}
        </button>
        <button
          onClick={onOpenSettings}
          type="button"
          className="uppercase tracking-widest transition-colors hover:text-fg"
          title="Tune retrieval / composer settings"
        >
          tune
        </button>
        <div className="flex items-center gap-1.5">
          <span
            className={
              "size-1.5 rounded-full " +
              (streaming ? "bg-signal animate-pulse" : "bg-accent")
            }
          />
          <span>{streaming ? "thinking" : "ready"}</span>
        </div>
      </div>
      </div>
      <div className="flex items-center gap-4 border-t border-line/40 px-6 py-1.5 font-mono text-[10px] tracking-widest text-dim">
        <span className="text-muted">wardenclyffe</span>
        {COIL_ORDER.map((c, i) => (
          <span key={c.key} className="flex items-center gap-3">
            {i > 0 && <span className="text-line">─</span>}
            <span
              className={
                c.key === activeCoil
                  ? "text-accent"
                  : "text-muted"
              }
              title={c.role}
            >
              {c.label}
              {c.key === activeCoil ? " ·" : ""}
            </span>
          </span>
        ))}
      </div>
    </header>
  );
}
