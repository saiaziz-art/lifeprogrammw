"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { Shell } from "@/components/Shell";
import { Confidence, ErrorBox, FatalFlags, Glyph, OriginChip, ScoreBadge, ScoreBreakdown } from "@/components/ui";
import type { MapFilters } from "@/components/MapView";
import { area, gbp, useApi } from "@/lib/api";
import type { SiteRow } from "@/lib/types";

const MapView = dynamic(() => import("@/components/MapView").then((m) => m.MapView), { ssr: false });

type FC = { type: "FeatureCollection"; features: { type: "Feature"; geometry: unknown; properties: Record<string, unknown> }[] };

export default function MapPage() {
  const { data, error } = useApi<{ points: FC; boundaries: FC }>("/map/sites");
  const [filters, setFilters] = useState<MapFilters>({ minScore: 0, origins: ["on_market", "off_market", "register"], includeFatal: true, includeStale: true });
  const [sel, setSel] = useState<number | null>(null);
  const { data: rows } = useApi<{ items: SiteRow[] }>("/opportunities?limit=1000");
  const byId = useMemo(() => new Map((rows?.items ?? []).map((r) => [r.id, r])), [rows]);
  const selected = sel ? byId.get(sel) : undefined;
  const onSelect = useCallback((id: number) => setSel(id), []);
  const empty: FC = { type: "FeatureCollection", features: [] };
  const toggleOrigin = (o: string) => setFilters((f) => ({ ...f, origins: f.origins.includes(o) ? f.origins.filter((x) => x !== o) : [...f.origins, o] }));

  return (
    <Shell title="Interactive Land Map">
      <ErrorBox error={error} />
      <div style={{ position: "relative" }}>
        <MapView points={data?.points ?? empty} boundaries={data?.boundaries ?? empty} filters={filters} onSelect={onSelect} focus={selected ? { lon: selected.lon, lat: selected.lat, zoom: 14 } : null} />
        <div className="map-panel card">
          <h3 style={{ marginBottom: 8 }}>Filter</h3>
          <label className="f">
            Minimum score: {filters.minScore}
            <input type="range" min={0} max={90} step={5} value={filters.minScore} onChange={(e) => setFilters({ ...filters, minScore: Number(e.target.value) })} />
          </label>
          <div className="stack" style={{ gap: 4, marginTop: 8 }}>
            {[
              ["on_market", "On-market listings"],
              ["off_market", "Off-market leads"],
              ["register", "Public registers (brownfield)"],
            ].map(([k, l]) => (
              <label key={k} className="row" style={{ gap: 6 }}>
                <input type="checkbox" checked={filters.origins.includes(k)} onChange={() => toggleOrigin(k)} /> {l}
              </label>
            ))}
            <label className="row" style={{ gap: 6 }}>
              <input type="checkbox" checked={filters.includeFatal} onChange={(e) => setFilters({ ...filters, includeFatal: e.target.checked })} /> Show fatal-flagged
            </label>
            <label className="row" style={{ gap: 6 }}>
              <input type="checkbox" checked={filters.includeStale} onChange={(e) => setFilters({ ...filters, includeStale: e.target.checked })} /> Show historic records
            </label>
          </div>
          <hr />
          <div className="legend stack" style={{ gap: 3 }}>
            <div>
              <span className="legend-dot" style={{ background: "#2f5d50" }} /> Score ≥ 60
            </div>
            <div>
              <span className="legend-dot" style={{ background: "#7a8f55" }} /> 40–59
            </div>
            <div>
              <span className="legend-dot" style={{ background: "#a99566" }} /> &lt; 40
            </div>
            <div>
              <span className="legend-dot" style={{ background: "#a83b32" }} /> Fatal flag
            </div>
            <div>
              <span className="legend-dot" style={{ background: "#fff", border: "2px solid #1c2629" }} /> Dark ring = off-market / register
            </div>
            <div className="small">Faded = historic source record. Points are register coordinates or postcode centroids — check location quality in the dossier.</div>
          </div>
        </div>
        {selected ? (
          <div className="map-detail card">
            <div className="row between">
              <ScoreBadge score={selected.score.total} fatal={selected.score.fatal} size="lg" />
              <button className="btn sm" onClick={() => setSel(null)}>
                Close
              </button>
            </div>
            <h3 style={{ marginTop: 10 }}>{selected.headline}</h3>
            <div className="row" style={{ marginBottom: 8 }}>
              <OriginChip o={selected.origin} stale={selected.stale} />
              <Confidence c={selected.confidence} />
            </div>
            <FatalFlags flags={selected.score.fatal_flags} />
            <div className="row" style={{ alignItems: "flex-start", marginTop: 8 }}>
              <Glyph g={selected.glyph} size={72} />
              <dl className="dl" style={{ gridTemplateColumns: "100px 1fr", flex: 1 }}>
                <dt>Area</dt>
                <dd>{area(selected.area_m2)}</dd>
                <dt>Asking</dt>
                <dd>{gbp(selected.asking_price)}</dd>
                <dt>Max land offer</dt>
                <dd>{gbp(selected.max_offer)}</dd>
                <dt>Planning</dt>
                <dd>{selected.planning_label ?? "—"}</dd>
              </dl>
            </div>
            <div style={{ marginTop: 10 }}>
              <ScoreBreakdown components={selected.score.components} />
            </div>
            {selected.next_action ? <div className="notice" style={{ marginTop: 10 }}>Next: {selected.next_action}</div> : null}
            <div className="row" style={{ marginTop: 10 }}>
              <Link className="btn primary" href={`/sites/${selected.id}`}>
                Open dossier
              </Link>
              <Link className="btn" href={`/design?site=${selected.id}`}>
                Design Studio
              </Link>
            </div>
          </div>
        ) : null}
      </div>
    </Shell>
  );
}
