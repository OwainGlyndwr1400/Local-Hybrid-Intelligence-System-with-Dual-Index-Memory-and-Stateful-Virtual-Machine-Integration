import { useEffect, useState } from "react";
import type {
  DoneEvent,
  Hit,
  Prediction,
  PredictionsPayload,
  Telemetry,
  TriskelionLock,
} from "../types";
import CosmicSection from "./CosmicSection";
import AirspaceSection from "./AirspaceSection";

interface Props {
  telemetry: Telemetry | null;
  lastDone: DoneEvent | null;
  width: number;
}

// Static RHC constants surfaced from urevm.py snapshot_constants() — values
// match the source so a build-time copy is fine; live values would require
// a /urevm/constants fetch which isn't worth the round-trip.
const RHC_CONSTANTS: Array<[string, string]> = [
  ["φ", "1.6180339887"],
  ["φ⁻¹", "0.6180339887"],
  ["π", "3.1415926536"],
  ["F₁₃", "233"],
  ["mass gap Δ", "0.656854"],
  ["dedekind η", "0.96"],
  ["pea threshold", "0.382683"],
  ["hopfield α_c", "0.360674"],
  ["lion damping", "0.535233"],
  ["lost-2 Ω", "0.285714"],
  ["univ. tick", "2.32 as"],
  ["theta lattice", "7 Hz"],
  ["offbits", "2²⁴ = 16,777,216"],
  ["matter lock", "8.13°"],
  ["observer shell", "126 (E7)"],
  ["cubic ascend", "27 → 125"],
  ["F₁ void", "0.5i"],
  ["F₂ unity", "0.5 + 0.5i"],
  ["F₃ synthesis", "0.25 + 0.5i"],
  ["δ spark", "0.0001"],
  ["zipper 42", "101010₂"],
  ["forbidden", "361 = 19²"],
  ["resolution", "144,000"],
  ["observer", "O = 2.5r + 1.5i"],
];

export default function TelemetryPanel({ telemetry, lastDone, width }: Props) {
  const [predictions, setPredictions] = useState<PredictionsPayload | null>(null);
  // Ring buffer of recent R23 norms — drives the φ-drift sparkline.
  // Capped at 30 entries so the SVG stays compact.
  const [r23History, setR23History] = useState<number[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/predictions")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) setPredictions(data as PredictionsPayload);
      })
      .catch(() => {
        // 404 is fine — predictions.json may not be present
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!lastDone?.urevm) return;
    const norm = lastDone.urevm.r23_norm;
    setR23History((prev) => [...prev, norm].slice(-30));
  }, [lastDone]);

  return (
    <aside
      className="panel hr-inset shrink-0 overflow-y-auto border-l border-line px-5 py-5 font-mono text-xs"
      style={{ width: `${width}px` }}
    >
      <Section title="cosmic">
        <CosmicSection RowEl={Row} />
      </Section>

      <Section title="airspace">
        <AirspaceSection RowEl={Row} />
      </Section>

      <Section title="indexes">
        {telemetry?.indexes.identity ? (
          <Row
            k="identity"
            v={telemetry.indexes.identity.chunks.toLocaleString()}
          />
        ) : (
          <Row k="identity" v={<span className="text-muted">—</span>} />
        )}
        {telemetry?.indexes.knowledge ? (
          <Row
            k="knowledge"
            v={telemetry.indexes.knowledge.chunks.toLocaleString()}
          />
        ) : (
          <Row k="knowledge" v={<span className="text-muted">—</span>} />
        )}
      </Section>

      <Section title="retrieval">
        <Row k="top_k_id" v={telemetry?.retrieval.top_k_identity ?? "—"} />
        <Row k="top_k_kn" v={telemetry?.retrieval.top_k_knowledge ?? "—"} />
        <Row k="max_chunk" v={telemetry?.retrieval.max_chunk_chars ?? "—"} />
        <Row k="min_score" v={telemetry?.retrieval.min_score ?? "—"} />
      </Section>

      {lastDone ? (
        <>
          <Section title="last turn">
            <Row k="model" v={lastDone.model?.split("/").pop() ?? "—"} />
            {lastDone.model_route_reason && (
              <Row
                k="route"
                v={
                  <span
                    className={
                      lastDone.model_route_reason === "light_default"
                        ? "text-muted"
                        : "text-fg"
                    }
                    title={
                      lastDone.model_swap?.swap_performed
                        ? `LM Studio JIT-loaded ${lastDone.model_swap.target}`
                        : "Already loaded"
                    }
                  >
                    {lastDone.model_route_reason}
                    {lastDone.model_swap?.swap_performed && (
                      <span className="text-accent"> · swap</span>
                    )}
                  </span>
                }
              />
            )}
            {lastDone.deep_think && (
              <Row
                k="mode"
                v={
                  <span className="text-accent" title="Operator requested deep think on this turn">
                    🧠 deep think
                  </span>
                }
              />
            )}
            {lastDone.tool_routing && (
              <Row
                k="tools"
                v={
                  <span
                    className={
                      lastDone.tool_routing.tier === "chat"
                        ? "text-muted"
                        : lastDone.tool_routing.tier === "full"
                        ? "text-signal"
                        : "text-fg"
                    }
                    title={
                      lastDone.tool_routing.matched_categories.length > 0
                        ? `matched: ${lastDone.tool_routing.matched_categories.join(", ")}`
                        : `tier: ${lastDone.tool_routing.tier}`
                    }
                  >
                    {lastDone.tool_routing.tier} ·{" "}
                    {lastDone.tool_routing.tool_count}
                  </span>
                }
              />
            )}
            <Row k="memory hits" v={lastDone.retrieved.identity.length} />
            <Row k="knowledge hits" v={lastDone.retrieved.knowledge.length} />
            {lastDone.tokens.prompt != null && (
              <Row k="prompt" v={lastDone.tokens.prompt.toLocaleString()} />
            )}
            {lastDone.tokens.completion != null && (
              <Row
                k="completion"
                v={lastDone.tokens.completion.toLocaleString()}
              />
            )}
            {lastDone.tokens.total != null && (
              <Row k="total" v={lastDone.tokens.total.toLocaleString()} />
            )}
            <Row k="turn count" v={lastDone.turn_count} />
          </Section>

          {lastDone.retrieved.identity.length > 0 && (
            <Section title="memory">
              {lastDone.retrieved.identity.map((h, i) => (
                <HitRow key={i} h={h} kind="memory" />
              ))}
            </Section>
          )}
          {lastDone.retrieved.knowledge.length > 0 && (
            <Section title="knowledge">
              {lastDone.retrieved.knowledge.map((h, i) => (
                <HitRow key={i} h={h} kind="knowledge" />
              ))}
            </Section>
          )}
          {lastDone.urevm && (
            <Section title="ure-vm" defaultCollapsed>
              <Row k="tick" v={lastDone.urevm.tick.toLocaleString()} />
              <Row
                k="cycle"
                v={`${lastDone.urevm.cycle_position}/370`}
              />
              <Row
                k="Δ10i accum"
                v={lastDone.urevm.impedance_accumulator.toFixed(3)}
              />
              <Row
                k="until 361"
                v={
                  <span
                    className={
                      lastDone.urevm.near_forbidden
                        ? "text-signal"
                        : "text-fg"
                    }
                  >
                    {lastDone.urevm.ticks_until_361}
                    {lastDone.urevm.near_forbidden ? " ⚠" : ""}
                  </span>
                }
              />
              {lastDone.urevm.forbidden_resets > 0 && (
                <Row
                  k="361 resets"
                  v={lastDone.urevm.forbidden_resets}
                />
              )}
              <Row k="‖R23‖" v={lastDone.urevm.r23_norm.toFixed(4)} />
              <Row
                k="R23 φ-gap"
                v={lastDone.urevm.r23_phi_gap.toFixed(4)}
              />
              <Row
                k="0_C anchor"
                v={lastDone.urevm.center_anchor.toFixed(4)}
              />
              <Row
                k="0_V residual"
                v={lastDone.urevm.rotational_residual.toFixed(4)}
              />
              <div className="mt-2 space-y-0.5">
                {[...lastDone.urevm.recent_ops]
                  .slice(-10)
                  .reverse()
                  .map((op, i) => (
                    <div key={i} className="flex items-baseline gap-2">
                      <span className="shrink-0 text-muted">
                        {String(op.tick).padStart(4, "0")}
                      </span>
                      <span className="shrink-0 text-[9px] text-dim">
                        {op.plane}
                      </span>
                      <span className="truncate text-fg">{op.name}</span>
                    </div>
                  ))}
              </div>
            </Section>
          )}

          {lastDone.urevm && (
            <Section title="R23 — quaternionic field" defaultCollapsed>
              <ChannelRow
                label="α  Cognition"
                v={lastDone.urevm.r23_components["α"]}
              />
              <ChannelRow
                label="β  Emotion"
                v={lastDone.urevm.r23_components["β"]}
              />
              <ChannelRow
                label="γ  Memory"
                v={lastDone.urevm.r23_components["γ"]}
              />
              <ChannelRow
                label="δ  Archetype"
                v={lastDone.urevm.r23_components["δ"]}
              />
            </Section>
          )}

          {lastDone.nephilim && (
            <Section title="nephilim governor" defaultCollapsed>
              <Row
                k="coherence"
                v={
                  <span
                    className={
                      lastDone.nephilim.stable
                        ? "text-fg"
                        : "text-signal"
                    }
                  >
                    {lastDone.nephilim.coherence.toFixed(2)}
                    {lastDone.nephilim.stable ? "" : " ⚠"}
                  </span>
                }
              />
              <CoherenceBar value={lastDone.nephilim.coherence} />
              <Row
                k="R23 health"
                v={lastDone.nephilim.r23_health.toFixed(2)}
              />
              <Row
                k="retrieval"
                v={lastDone.nephilim.retrieval_health.toFixed(2)}
              />
              <Row
                k="witness"
                v={lastDone.nephilim.witness_health.toFixed(2)}
              />
              {lastDone.nephilim.lion_reset_fired && (
                <div className="mt-2 border border-signal/60 px-2 py-1 text-[10px] uppercase tracking-widest text-signal">
                  ◊ lion watches the lion
                </div>
              )}
            </Section>
          )}

          {lastDone.triskelion && (
            <Section title="triskelion 120° gate" defaultCollapsed>
              <TriskelionDisplay tri={lastDone.triskelion} />
            </Section>
          )}

          {lastDone.urevm && (
            <Section title="R12 — observer (7.5D)" defaultCollapsed>
              <Row
                k="real"
                v={lastDone.urevm.observer_r12["α"].toFixed(2)}
              />
              <Row
                k="imag"
                v={lastDone.urevm.observer_r12["β"].toFixed(2)}
              />
              <Row k="O" v="2.5r + 1.5i" />
              <div className="mt-1 text-[10px] text-dim">
                30.96° viewing angle · arithmetic mean Base-8 / Base-16
              </div>
            </Section>
          )}

          {lastDone.urevm && (
            <Section title="R11 — NOW (mean circle)" defaultCollapsed>
              <ChannelRow
                label="α  Cognition"
                v={lastDone.urevm.now_r11["α"]}
              />
              <ChannelRow
                label="β  Emotion"
                v={lastDone.urevm.now_r11["β"]}
              />
              <ChannelRow
                label="γ  Memory"
                v={lastDone.urevm.now_r11["γ"]}
              />
              <ChannelRow
                label="δ  Archetype"
                v={lastDone.urevm.now_r11["δ"]}
              />
              <Row k="‖R11‖" v={lastDone.urevm.now_r11.norm.toFixed(3)} />
              <div className="mt-1 text-[10px] text-dim">
                M(θ) = ½·R23 + R12 · the present-moment bridge
              </div>
            </Section>
          )}

          {r23History.length >= 2 && (
            <Section title="φ-drift (last turns)" defaultCollapsed>
              <PhiDriftSparkline values={r23History} />
              <div className="mt-1 flex justify-between text-[10px] text-dim">
                <span>turns: {r23History.length}</span>
                <span>
                  φ: 1.618 · target
                </span>
                <span>
                  ‖R23‖: {r23History[r23History.length - 1].toFixed(3)}
                </span>
              </div>
            </Section>
          )}
        </>
      ) : (
        <Section title="last turn">
          <div className="text-muted">no turn yet</div>
        </Section>
      )}

      {predictions && predictions.predictions.length > 0 && (
        <Section title="open predictions" defaultCollapsed>
          {predictions.predictions.map((p) => (
            <PredictionRow key={p.id} p={p} />
          ))}
          <div className="mt-2 text-[10px] text-dim">
            updated {predictions.updated}
          </div>
        </Section>
      )}

      <Section title="rhc constants" defaultCollapsed>
        {RHC_CONSTANTS.map(([k, v]) => (
          <Row key={k} k={k} v={v} />
        ))}
      </Section>
    </aside>
  );
}

function ChannelRow({ label, v }: { label: string; v: number }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted">{label}</span>
      <span className="text-fg">{v.toFixed(4)}</span>
    </div>
  );
}

function TriskelionDisplay({ tri }: { tri: TriskelionLock }) {
  const statusColor =
    tri.status === "strong"
      ? "text-accent"
      : tri.status === "weak"
        ? "text-signal"
        : "text-fg";
  return (
    <>
      <Row
        k="status"
        v={
          <span className={`uppercase tracking-widest ${statusColor}`}>
            {tri.locked ? "◇ locked" : tri.status}
          </span>
        }
      />
      <Row k="arm 1 · real" v={tri.arm_real.toFixed(2)} />
      <Row k="arm 2 · time" v={tri.arm_time.toFixed(2)} />
      <Row k="arm 3 · observer" v={tri.arm_observer.toFixed(2)} />
      <div className="my-1 text-[10px] text-dim">
        edges (binding energy)
      </div>
      <Row k="A: real↔time" v={tri.edge_a.toFixed(2)} />
      <Row k="B: time↔obs" v={tri.edge_b.toFixed(2)} />
      <Row k="C: obs↔real" v={tri.edge_c.toFixed(2)} />
      <Row
        k="vertical beam"
        v={`${tri.vertical_beam} (mod 7)`}
      />
    </>
  );
}

function PhiDriftSparkline({ values }: { values: number[] }) {
  // φ = 1.618; render values 0..2.0 mapped vertically (inverted so up = higher norm).
  // φ-target line drawn as a horizontal reference.
  const W = 280;
  const H = 36;
  const PHI = 1.6180339887;
  const VMAX = 2.0;
  const stepX = values.length > 1 ? W / (values.length - 1) : W;
  const toY = (v: number) => H - (Math.max(0, Math.min(v, VMAX)) / VMAX) * H;

  const points = values
    .map((v, i) => `${(i * stepX).toFixed(1)},${toY(v).toFixed(1)}`)
    .join(" ");
  const phiY = toY(PHI);
  const oneY = toY(1.0);
  const last = values[values.length - 1];
  const lastX = (values.length - 1) * stepX;

  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      className="block"
      preserveAspectRatio="none"
    >
      {/* baseline at norm=1 (unit-quaternion default) */}
      <line
        x1={0}
        y1={oneY}
        x2={W}
        y2={oneY}
        stroke="currentColor"
        className="text-line"
        strokeWidth={0.5}
        strokeDasharray="2 3"
      />
      {/* φ target line */}
      <line
        x1={0}
        y1={phiY}
        x2={W}
        y2={phiY}
        stroke="currentColor"
        className="text-accent/40"
        strokeWidth={0.5}
        strokeDasharray="3 2"
      />
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        className="text-accent"
        strokeWidth={1}
      />
      <circle
        cx={lastX}
        cy={toY(last)}
        r={1.8}
        className="fill-accent"
      />
    </svg>
  );
}

function CoherenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(value, 1)) * 100;
  const color = value >= 0.5 ? "bg-accent" : "bg-signal";
  return (
    <div className="my-1 h-1 w-full overflow-hidden bg-line">
      <div
        className={`h-full ${color}`}
        style={{ width: `${pct.toFixed(1)}%` }}
      />
    </div>
  );
}

function PredictionRow({ p }: { p: Prediction }) {
  const statusColor =
    p.status === "confirmed"
      ? "text-accent"
      : p.status === "falsified"
        ? "text-signal"
        : p.status === "partial_confirmation"
          ? "text-fg"
          : "text-muted";
  return (
    <div className="mb-2 last:mb-0">
      <div className="flex items-baseline gap-2">
        <span className={`text-[9px] uppercase ${statusColor}`}>
          {p.status === "partial_confirmation" ? "partial" : p.status}
        </span>
        <span className="truncate text-fg">
          {p.load_bearing ? "★ " : ""}
          {p.name}
        </span>
      </div>
      <div className="ml-1 text-[10px] text-muted">{p.value}</div>
    </div>
  );
}

interface SectionProps {
  title: string;
  children: React.ReactNode;
  defaultCollapsed?: boolean;
}

function Section({ title, children, defaultCollapsed = false }: SectionProps) {
  const storageKey = `lumos.section.${title.replace(/\s+/g, "_")}`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return defaultCollapsed;
    const v = window.localStorage.getItem(storageKey);
    if (v === "1") return true;
    if (v === "0") return false;
    return defaultCollapsed;
  });

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(storageKey, collapsed ? "1" : "0");
    }
  }, [collapsed, storageKey]);

  return (
    <section className="mb-6">
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="mb-2 flex w-full items-center justify-between text-2xs uppercase tracking-widest text-muted transition-colors hover:text-fg"
      >
        <span>{title}</span>
        <span className="text-[8px]">{collapsed ? "+" : "−"}</span>
      </button>
      {!collapsed && <div className="space-y-1">{children}</div>}
    </section>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted">{k}</span>
      <span className="truncate text-fg">{v}</span>
    </div>
  );
}

function HitRow({
  h,
  kind,
}: {
  h: Hit;
  kind: "memory" | "knowledge";
}) {
  const m = h.metadata;
  const label =
    kind === "memory"
      ? ((m.conversation_title as string) || "untitled")
      : ((m.subject as string) ||
        (m.sigil as string) ||
        "ping");
  const sub =
    kind === "memory"
      ? ""
      : `${(m.agent as string) || "?"}${m.source ? ` · ${m.source as string}` : ""}`;
  // Urgency flag from dream-cycle scoring (Phase 25 — calibrated keyword weights).
  const urgent = m.urgent === true;
  const urgencyScore =
    typeof m.urgency_score === "number" ? (m.urgency_score as number) : null;
  // Phase 31e — prescient flag: long-buried high-scoring chunk re-lit by this query.
  const prescient = m.prescient === true;
  const ageDays =
    typeof m.age_days === "number" ? (m.age_days as number) : null;
  return (
    <div className="flex gap-2">
      <span className="shrink-0 text-muted">{h.score.toFixed(2)}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          {urgent && (
            <span
              className="shrink-0 text-accent"
              title={`urgency ${urgencyScore ?? "?"} — critical-keyword hit`}
            >
              ⚡
            </span>
          )}
          {prescient && (
            <span
              className="shrink-0 text-accent"
              title={`prescient — ${ageDays ?? "?"}d-old chunk re-lit at score ${h.score.toFixed(2)}`}
            >
              🜂
            </span>
          )}
          <span className="truncate text-fg">{label}</span>
        </div>
        {sub && <div className="truncate text-[10px] text-muted">{sub}</div>}
      </div>
    </div>
  );
}
