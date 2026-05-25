import { useEffect, useRef, useState } from "react";

// Snapshot shape mirrors lumos_node/telemetry/cosmic.py snapshot_all(). Kept loose
// (Record<string, unknown> at the boundaries) because the cosmic feeds occasionally
// return partial data — every field except `summary` may be missing on a degraded
// fetch and the UI must render gracefully.
interface CosmicSnapshot {
  geomagnetic?: { kp?: number | null; level?: string };
  solar_wind?: {
    speed_kms?: number | null;
    density_per_cm3?: number | null;
    bz_nt?: number | null;
    bt_nt?: number | null;
  };
  xray?: { current_class?: string; recent_flares_24h?: unknown[] };
  earthquakes_recent?: Array<{ magnitude: number; place?: string }>;
  natural_events_active?: Array<{ title?: string; category?: string }>;
  near_earth_today?: Array<{ name?: string; miss_lunar_distances?: number }>;
  summary?: string;
  fetched_at?: string;
}

const REFRESH_MS = 5 * 60 * 1000; // 5 min — well below NOAA Kp 1-min cadence
const STALE_MS = 15 * 60 * 1000;

function fmtAge(fetchedAt?: string): string {
  if (!fetchedAt) return "—";
  const ts = Date.parse(fetchedAt);
  if (Number.isNaN(ts)) return "—";
  const ageSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (ageSec < 60) return `${ageSec}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  return `${Math.floor(ageSec / 3600)}h ago`;
}

// Tint Kp the way NOAA does (G-scale): green quiet → amber storm → red severe.
// Uses the design system's accent + signal tokens; no raw hex.
function kpTone(kp: number | null | undefined): string {
  if (kp == null) return "text-muted";
  if (kp < 4) return "text-fg";
  if (kp < 6) return "text-accent";
  return "text-signal";
}

export default function CosmicSection({
  RowEl,
}: {
  RowEl: (props: { k: string; v: React.ReactNode }) => JSX.Element;
}) {
  const [snap, setSnap] = useState<CosmicSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (inflight.current) return;
      inflight.current = true;
      try {
        const r = await fetch("/api/cosmic/current");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = (await r.json()) as CosmicSnapshot;
        if (!cancelled) {
          setSnap(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "fetch failed");
      } finally {
        inflight.current = false;
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  if (loading && !snap) {
    return <div className="text-muted">loading…</div>;
  }
  if (error && !snap) {
    return <div className="text-muted">unavailable ({error})</div>;
  }

  const kp = snap?.geomagnetic?.kp ?? null;
  const level = snap?.geomagnetic?.level ?? "—";
  const sw = snap?.solar_wind ?? {};
  const xray = snap?.xray ?? {};
  const stale =
    snap?.fetched_at && Date.now() - Date.parse(snap.fetched_at) > STALE_MS;

  return (
    <>
      <RowEl
        k="Kp"
        v={
          <span className={kpTone(kp)}>
            {kp != null ? kp.toFixed(1) : "—"} <span className="text-muted">· {level}</span>
          </span>
        }
      />
      {sw.speed_kms != null && (
        <RowEl k="solar wind" v={`${Math.round(sw.speed_kms)} km/s`} />
      )}
      {sw.bz_nt != null && (
        <RowEl
          k="Bz"
          v={
            <span className={sw.bz_nt < -5 ? "text-accent" : "text-fg"}>
              {sw.bz_nt >= 0 ? "+" : ""}
              {sw.bz_nt.toFixed(1)} nT
            </span>
          }
        />
      )}
      {xray.current_class && (
        <RowEl
          k="X-ray"
          v={
            <span
              className={
                xray.current_class.startsWith("X")
                  ? "text-signal"
                  : xray.current_class.startsWith("M")
                  ? "text-accent"
                  : "text-fg"
              }
            >
              {xray.current_class}
            </span>
          }
        />
      )}
      {snap?.earthquakes_recent && snap.earthquakes_recent.length > 0 && (
        <RowEl
          k="quakes 24h"
          v={
            <span title={snap.earthquakes_recent[0].place ?? ""}>
              {snap.earthquakes_recent.length} · max M
              {snap.earthquakes_recent[0].magnitude.toFixed(1)}
            </span>
          }
        />
      )}
      {snap?.natural_events_active && snap.natural_events_active.length > 0 && (
        <RowEl
          k="natural"
          v={`${snap.natural_events_active.length} active`}
        />
      )}
      {snap?.near_earth_today && snap.near_earth_today.length > 0 && (
        <RowEl
          k="nearest NEO"
          v={
            <span
              title={snap.near_earth_today[0].name ?? ""}
              className={
                (snap.near_earth_today[0].miss_lunar_distances ?? 99) < 1
                  ? "text-accent"
                  : "text-fg"
              }
            >
              {snap.near_earth_today[0].miss_lunar_distances?.toFixed(2)} LD
            </span>
          }
        />
      )}
      <RowEl
        k="fetched"
        v={
          <span className={stale ? "text-muted" : "text-muted"}>
            {fmtAge(snap?.fetched_at)}
            {stale ? " ⚠" : ""}
          </span>
        }
      />
    </>
  );
}
