import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import { fetchAtlas } from "../api";
import type { AtlasData, DoneEvent } from "../types";
import ClusterDrilldown from "./ClusterDrilldown";

interface Props {
  lastDone: DoneEvent | null;
  collapsed: boolean;
  onToggle: () => void;
  width: number;
}

const COLOR_IDENTITY_HEX = "#7ab8b0";
const COLOR_KNOWLEDGE_HEX = "#c8b886";
const COLOR_DIM_HEX = "#1a1a20";
const COLOR_FLASH_HEX = "#ffffff";
const INACTIVE_BLEND = 0.72;
const ACTIVATION_DURATION_MS = 8000;
const FLASH_DURATION_MS = 500;
const FLASH_AMOUNT = 0.4;
const BREATH_PERIOD_MS = 4200;       // slightly slower for a more organic feel
const BREATH_AMPLITUDE = 0.10;        // node-size pulse
const GROUP_BREATH_AMPLITUDE = 0.05;  // whole-graph scale (nodes + edges)
const TWO_PI = Math.PI * 2;

const ESCAPE_MAP: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ESCAPE_MAP[c] ?? c);
}

// Per-node phase offset retained but tiny — gives an almost-synchronized
// organic shimmer rather than perfectly-mechanical lock-step.
const NODE_PHASE_JITTER = 0.18;

function phaseFromId(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = (h * 31 + id.charCodeAt(i)) | 0;
  }
  return ((h & 0xffff) / 0xffff) * NODE_PHASE_JITTER;
}

interface AtlasNodeData {
  id: string;
  label: string;
  lane: "identity" | "knowledge";
  size: number;
  text: string;
}

export default function AtlasPanel({ lastDone, collapsed, onToggle, width }: Props) {
  const [data, setData] = useState<AtlasData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeMap, setActiveMap] = useState<Map<string, number>>(new Map());
  const activeMapRef = useRef(activeMap);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 420, height: 600 });
  const nodeMeshes = useRef<Map<string, THREE.Mesh>>(new Map());
  const fgRef = useRef<unknown>(null);
  const [selectedCluster, setSelectedCluster] = useState<string | null>(null);

  useEffect(() => {
    activeMapRef.current = activeMap;
  }, [activeMap]);

  useEffect(() => {
    fetchAtlas().then(setData).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || collapsed) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0];
      if (r) {
        setDims({
          width: Math.max(200, Math.floor(r.contentRect.width)),
          height: Math.max(200, Math.floor(r.contentRect.height)),
        });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [collapsed]);

  useEffect(() => {
    if (!lastDone) return;
    const ids = new Set<string>();
    for (const h of lastDone.retrieved.identity) {
      if (h.cluster_id) ids.add(h.cluster_id);
    }
    for (const h of lastDone.retrieved.knowledge) {
      if (h.cluster_id) ids.add(h.cluster_id);
    }
    if (ids.size === 0) return;
    const now = Date.now();
    setActiveMap((prev) => {
      const next = new Map(prev);
      ids.forEach((id) => next.set(id, now));
      return next;
    });
  }, [lastDone]);

  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();
      setActiveMap((prev) => {
        let changed = false;
        const next = new Map(prev);
        for (const [id, ts] of next) {
          if (now - ts > ACTIVATION_DURATION_MS) {
            next.delete(id);
            changed = true;
          }
        }
        return changed ? next : prev;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  // Per-frame animation loop: breathing + activation flash + fade applied directly
  // to each node's Three.js mesh (scale + material color), plus a synchronized
  // group-level scale on the entire force-graph scene so edges breathe with nodes.
  //
  // Phase 31 — conditional framerate to drop idle CPU usage:
  //   - Full 60fps WHEN active flashes are in flight (need smooth visuals)
  //   - Throttled to ~5fps when idle (breathing still pulses visually but
  //     1/12th the CPU cost of full 60fps over 240+ node meshes)
  // Net effect: idle CPU drops from ~10-15% to ~1-3%; flashes remain smooth.
  useEffect(() => {
    let animId = 0;
    let lastUpdateMs = 0;
    const IDLE_FRAME_INTERVAL_MS = 200; // 5fps when idle
    const tmp = new THREE.Color();
    const identity = new THREE.Color(COLOR_IDENTITY_HEX);
    const knowledge = new THREE.Color(COLOR_KNOWLEDGE_HEX);
    const dim = new THREE.Color(COLOR_DIM_HEX);
    const flash = new THREE.Color(COLOR_FLASH_HEX);

    const tick = () => {
      const now = Date.now();
      const active = activeMapRef.current;
      // Idle check: no active flashes AND recent update → skip this frame.
      // Keeps requestAnimationFrame scheduled (cheap) but bails out of the
      // expensive per-mesh iteration most of the time.
      if (active.size === 0 && now - lastUpdateMs < IDLE_FRAME_INTERVAL_MS) {
        animId = requestAnimationFrame(tick);
        return;
      }
      lastUpdateMs = now;
      const breathPhase = (now / BREATH_PERIOD_MS) * TWO_PI;
      const groupBreath = 1 + GROUP_BREATH_AMPLITUDE * Math.sin(breathPhase);

      // Scale the whole graph group (nodes + edges) in unison.
      const fg = fgRef.current as { scene?: () => THREE.Scene } | null;
      if (fg && typeof fg.scene === "function") {
        const scene = fg.scene();
        if (scene) {
          for (const child of scene.children) {
            if ((child as THREE.Object3D).type === "Group") {
              (child as THREE.Object3D).scale.setScalar(groupBreath);
              break;
            }
          }
        }
      }

      nodeMeshes.current.forEach((mesh, id) => {
        const n = mesh.userData.nodeData as AtlasNodeData | undefined;
        if (!n) return;

        const baseSize = Math.cbrt(Math.log2(n.size + 1) + 1) * 3.2;

        const breath =
          1 + BREATH_AMPLITUDE * Math.sin(breathPhase + phaseFromId(id));

        const ts = active.get(id);
        let activeFactor = 1;
        if (ts) {
          const age = now - ts;
          if (age < FLASH_DURATION_MS) {
            const flashT = 1 - age / FLASH_DURATION_MS;
            activeFactor = 1.6 + 1.4 * flashT;
          } else {
            const fadeT = Math.min(
              1,
              (age - FLASH_DURATION_MS) /
                (ACTIVATION_DURATION_MS - FLASH_DURATION_MS),
            );
            activeFactor = 1 + 1.4 * (1 - fadeT);
          }
        }
        mesh.scale.setScalar(baseSize * activeFactor * breath);

        const base = n.lane === "identity" ? identity : knowledge;
        if (!ts) {
          tmp.copy(base).lerp(dim, INACTIVE_BLEND);
        } else {
          const age = now - ts;
          if (age < FLASH_DURATION_MS) {
            const flashT = 1 - age / FLASH_DURATION_MS;
            tmp.copy(base).lerp(flash, FLASH_AMOUNT * flashT);
          } else {
            const fadeT = Math.min(
              1,
              (age - FLASH_DURATION_MS) /
                (ACTIVATION_DURATION_MS - FLASH_DURATION_MS),
            );
            tmp.copy(base).lerp(dim, fadeT * INACTIVE_BLEND);
          }
        }
        (mesh.material as THREE.MeshBasicMaterial).color.copy(tmp);
      });

      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animId);
  }, []);

  // Reset mesh registry whenever underlying graph data changes
  useEffect(() => {
    nodeMeshes.current = new Map();
  }, [data]);

  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] };
    return {
      nodes: data.clusters.map((c) => ({
        id: c.id,
        label: c.label,
        lane: c.lane,
        size: c.size,
        text: c.representative_text,
      })),
      links: data.edges.map((e) => ({
        source: e.a,
        target: e.b,
        weight: e.weight,
      })),
    };
  }, [data]);

  if (collapsed) {
    return (
      <button
        onClick={onToggle}
        type="button"
        className="flex w-9 shrink-0 cursor-pointer items-center justify-center border-r border-line bg-bg text-muted transition-colors hover:text-fg"
        title="Expand atlas"
      >
        <span
          className="font-mono text-2xs uppercase"
          style={{
            writingMode: "vertical-rl",
            transform: "rotate(180deg)",
            letterSpacing: "0.25em",
          }}
        >
          atlas
        </span>
      </button>
    );
  }

  /* eslint-disable @typescript-eslint/no-explicit-any */
  const buildNodeObject = (n: any): THREE.Object3D => {
    const geometry = new THREE.SphereGeometry(1, 18, 18);
    const material = new THREE.MeshBasicMaterial({
      color: 0x808080,
      transparent: true,
      opacity: 0.88,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.userData.nodeData = n as AtlasNodeData;
    nodeMeshes.current.set(String(n.id), mesh);
    return mesh;
  };

  const getNodeLabel = (n: any): string => {
    return `<div style="background:rgba(19,19,22,0.96);border:1px solid #1f1f24;padding:8px 10px;font-family:'IBM Plex Mono',monospace;font-size:11px;color:#ebebef;max-width:300px;border-radius:3px">
      <div style="color:#8a8a92;text-transform:uppercase;letter-spacing:0.08em;font-size:9px;margin-bottom:4px">${n.lane} · ${(n.size as number).toLocaleString()} chunks</div>
      <div style="font-weight:500;line-height:1.4">${escapeHtml(String(n.label ?? ""))}</div>
    </div>`;
  };
  /* eslint-enable @typescript-eslint/no-explicit-any */

  return (
    <aside
      className="flex shrink-0 flex-col border-r border-line bg-bg"
      style={{ width: `${width}px` }}
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="font-mono text-2xs uppercase tracking-widest text-muted">
          atlas
        </div>
        <button
          onClick={onToggle}
          type="button"
          className="font-mono text-2xs text-muted transition-colors hover:text-fg"
          title="Collapse"
        >
          ←
        </button>
      </div>
      <div ref={containerRef} className="relative min-h-0 flex-1">
        {error ? (
          <div className="p-6 font-mono text-2xs leading-relaxed text-muted">
            {error}
          </div>
        ) : !data ? (
          <div className="p-6 font-mono text-2xs text-muted">loading…</div>
        ) : (
          <ForceGraph3D
            ref={(r: unknown) => {
              fgRef.current = r;
            }}
            graphData={graphData}
            width={dims.width}
            height={dims.height}
            backgroundColor="#0c0c0e"
            showNavInfo={false}
            nodeThreeObject={buildNodeObject}
            nodeLabel={getNodeLabel}
            linkColor={() => "rgba(122,184,176,0.18)"}
            linkWidth={0.4}
            linkOpacity={0.4}
            enableNodeDrag={false}
            cooldownTicks={150}
            warmupTicks={20}
            onNodeClick={(n: { id?: string | number } & Record<string, unknown>) => {
              const id = n.id;
              if (typeof id === "string") setSelectedCluster(id);
            }}
          />
        )}
        {selectedCluster && (
          <ClusterDrilldown
            clusterId={selectedCluster}
            onClose={() => setSelectedCluster(null)}
          />
        )}
      </div>
      <div className="border-t border-line px-4 py-2.5 font-mono text-2xs text-muted">
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5">
            <span
              className="size-1.5 rounded-full"
              style={{ background: COLOR_IDENTITY_HEX }}
            />
            memory
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="size-1.5 rounded-full"
              style={{ background: COLOR_KNOWLEDGE_HEX }}
            />
            knowledge
          </span>
          <span className="ml-auto">
            {data?.clusters.length ?? 0} clusters
          </span>
        </div>
      </div>
    </aside>
  );
}
