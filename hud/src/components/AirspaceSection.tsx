import { useEffect, useRef, useState } from "react";

interface Aircraft {
  icao24?: string;
  callsign?: string | null;
  origin_country?: string;
  lat?: number | null;
  lon?: number | null;
  altitude_ft?: number | null;
  velocity_kts?: number | null;
  heading_deg?: number | null;
  on_ground?: boolean | null;
}

interface AirspaceData {
  ok: boolean;
  authenticated?: boolean;
  count?: number;
  center?: { lat: number; lon: number; radius_km: number };
  aircraft?: Aircraft[];
  error?: string;
}

// 2 min refresh balances "feels live" against OpenSky's 4000/day quota:
// 720 calls/day even if HUD stays open all day. Plus the call is paused
// when the tab is hidden via document.visibilityState.
const REFRESH_MS = 2 * 60 * 1000;

// Compass directions for heading display. 16-point rose gets too noisy for
// the side panel; 8-point reads cleanly in 1ch of horizontal space.
const COMPASS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
function compass(deg: number | null | undefined): string {
  if (deg == null) return "—";
  const idx = Math.round((((deg % 360) + 360) % 360) / 45) % 8;
  return COMPASS_8[idx];
}

export default function AirspaceSection({
  RowEl,
}: {
  RowEl: (props: { k: string; v: React.ReactNode }) => JSX.Element;
}) {
  const [data, setData] = useState<AirspaceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const inflight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (inflight.current) return;
      if (document.visibilityState === "hidden") return;
      inflight.current = true;
      try {
        const r = await fetch("/api/airspace/local");
        if (!r.ok) {
          if (r.status === 400) {
            // Operator location unset — silently mark unavailable, don't spam.
            if (!cancelled) {
              setError("operator location unset");
              setLoading(false);
            }
            return;
          }
          throw new Error(`HTTP ${r.status}`);
        }
        const payload = (await r.json()) as AirspaceData;
        if (!cancelled) {
          setData(payload);
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

  if (loading && !data) {
    return <div className="text-muted">loading…</div>;
  }
  if (error && !data) {
    return <div className="text-muted">{error}</div>;
  }
  if (!data?.ok) {
    return <div className="text-muted">unavailable</div>;
  }

  const planes = data.aircraft ?? [];
  const airborne = planes.filter((p) => !p.on_ground);
  const sorted = [...airborne].sort(
    (a, b) => (b.altitude_ft ?? 0) - (a.altitude_ft ?? 0),
  );

  return (
    <>
      <RowEl
        k="planes"
        v={
          <span className="text-fg">
            {airborne.length} <span className="text-muted">airborne</span>
          </span>
        }
      />
      {data.center && (
        <RowEl
          k="radius"
          v={
            <span className="text-muted">
              {data.center.radius_km.toFixed(0)} km{" "}
              {data.authenticated ? "·  auth" : "·  anon"}
            </span>
          }
        />
      )}
      {airborne.length > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="mt-1 w-full text-left text-[10px] text-muted hover:text-fg"
        >
          {expanded ? "hide list ▾" : `show list ▸  (${airborne.length})`}
        </button>
      )}
      {expanded &&
        sorted.slice(0, 20).map((p) => {
          const cs = (p.callsign ?? "").trim() || (p.icao24 ?? "?");
          const alt = p.altitude_ft != null ? `${(p.altitude_ft / 1000).toFixed(0)}k ft` : "—";
          const vel = p.velocity_kts != null ? `${p.velocity_kts}kts` : "—";
          return (
            <div
              key={p.icao24}
              className="flex justify-between gap-2 text-[10px]"
              title={p.origin_country ?? ""}
            >
              <span className="truncate text-fg">{cs}</span>
              <span className="shrink-0 text-muted">
                {alt} · {vel} · {compass(p.heading_deg)}
              </span>
            </div>
          );
        })}
    </>
  );
}
