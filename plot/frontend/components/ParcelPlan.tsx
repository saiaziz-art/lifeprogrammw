"use client";

import type { LeadGeometry } from "@/lib/offmarket";

type Ring = [number, number][];

function rings(g: { type: string; coordinates: unknown } | null | undefined): Ring[] {
  if (!g) return [];
  if (g.type === "Polygon") return g.coordinates as Ring[];
  if (g.type === "MultiPolygon") return (g.coordinates as Ring[][]).flat();
  return [];
}

/** Indicative site plan drawn from the lead's geometry: registered title extent (dashed),
 * existing building, the notional new plot and any access strip. Not a survey. */
export function ParcelPlan({ g, size = 120, labels = false, plotArea }: { g: LeadGeometry; size?: number; labels?: boolean; plotArea?: number | null }) {
  const title = rings(g.title);
  const plot = rings(g.plot);
  const house = rings(g.house);
  const access = rings(g.access);
  const frame = title.length ? title : plot;
  if (!frame.length) {
    return (
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="glyph" style={{ width: size, height: size }} aria-label="no geometry">
        <text x={size / 2} y={size / 2} textAnchor="middle" fontSize="10" fill="#7b878c">
          no outline
        </text>
      </svg>
    );
  }
  const all = [...frame, ...plot, ...access].flat();
  const lat0 = all.reduce((a, p) => a + p[1], 0) / all.length;
  const kx = Math.cos((lat0 * Math.PI) / 180) * 111320;
  const ky = 110540;
  const xs = all.map((p) => p[0] * kx);
  const ys = all.map((p) => p[1] * ky);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const span = Math.max(maxx - minx, maxy - miny, 10);
  const pad = size * 0.08;
  const s = (size - 2 * pad) / span;
  const ox = pad + (size - 2 * pad - (maxx - minx) * s) / 2;
  const oy = pad + (size - 2 * pad - (maxy - miny) * s) / 2;
  const pt = (p: [number, number]) => `${(ox + (p[0] * kx - minx) * s).toFixed(1)},${(size - oy - (p[1] * ky - miny) * s).toFixed(1)}`;
  const path = (rs: Ring[]) => rs.map((r) => `M${r.map(pt).join("L")}Z`).join(" ");
  const bar = span > 120 ? 50 : span > 50 ? 20 : 10;
  const centre = plot[0] ? plot[0].reduce((a, p) => [a[0] + p[0] / plot[0].length, a[1] + p[1] / plot[0].length], [0, 0]) : null;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ width: "100%", maxWidth: size, height: "auto", aspectRatio: "1 / 1", background: "#f3f5f0", borderRadius: 8, border: "1px solid var(--line)" }} role="img" aria-label="Indicative parcel plan">
      {title.length ? <path d={path(title)} fill="#e7ebe3" stroke="#6f7b73" strokeWidth={1} strokeDasharray="4 3" /> : null}
      {house.length ? <path d={path(house)} fill="#b9bfba" stroke="#5b625d" strokeWidth={0.8} /> : null}
      {access.length ? <path d={path(access)} fill="rgba(201,178,124,0.75)" stroke="#a3886b" strokeWidth={0.8} /> : null}
      {plot.length ? <path d={path(plot)} fill="rgba(47,93,80,0.28)" stroke="#2f5d50" strokeWidth={1.6} /> : null}
      {labels && centre ? (
        <text x={pt(centre as [number, number]).split(",")[0]} y={pt(centre as [number, number]).split(",")[1]} textAnchor="middle" fontSize={11} fill="#1d3d34" fontWeight={600}>
          {plotArea ? `~${Math.round(plotArea)} m²` : "new plot"}
        </text>
      ) : null}
      <g transform={`translate(${pad},${size - pad / 2 - 2})`}>
        <rect x={0} y={-3} width={bar * s} height={3} fill="#44535a" />
        {size >= 160 ? (
          <text x={bar * s + 4} y={0} fontSize={9} fill="#44535a">
            {bar} m
          </text>
        ) : null}
      </g>
      {size >= 160 ? (
        <g transform={`translate(${size - pad - 4},${pad + 6})`}>
          <path d="M0,-8 L4,4 L0,1 L-4,4 Z" fill="#44535a" />
          <text x={0} y={14} fontSize={8} textAnchor="middle" fill="#44535a">
            N
          </text>
        </g>
      ) : null}
    </svg>
  );
}

export function PlanLegend() {
  const item = (fill: string, stroke: string, text: string, dash?: string) => (
    <span className="row" style={{ gap: 5 }}>
      <svg width="16" height="12" aria-hidden>
        <rect x="1" y="1" width="14" height="10" fill={fill} stroke={stroke} strokeDasharray={dash} />
      </svg>
      {text}
    </span>
  );
  return (
    <div className="row small" style={{ gap: 14 }}>
      {item("#e7ebe3", "#6f7b73", "Registered title extent (INSPIRE, indicative)", "3 2")}
      {item("#b9bfba", "#5b625d", "Existing building (inferred)")}
      {item("rgba(47,93,80,0.28)", "#2f5d50", "Indicative new plot")}
      {item("rgba(201,178,124,0.75)", "#a3886b", "Access strip")}
    </div>
  );
}
