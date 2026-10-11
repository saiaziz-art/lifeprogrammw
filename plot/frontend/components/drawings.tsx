"use client";

import type { DesignOption, Fit, GeoJSON } from "@/lib/types";

const ROOM_FILL: Record<string, string> = { living: "#e6efe9", bedroom: "#eef0f6", wet: "#e4eef6", service: "#f3efe6", circulation: "#f5f3ee" };

function rings(g: GeoJSON | undefined): number[][][] {
  if (!g) return [];
  if (g.type === "Polygon") return [(g.coordinates as number[][][])[0]];
  if (g.type === "MultiPolygon") return (g.coordinates as number[][][][]).map((p) => p[0]);
  return [];
}

/** Site layout in local metres (north up): boundary, setback zone, house footprint, scale bar. */
export function SitePlan({ fit, width = 420, height = 320 }: { fit: Fit; width?: number; height?: number }) {
  const geom = fit.geometry;
  if (!geom) {
    return (
      <div className="notice small" style={{ width, maxWidth: "100%" }}>
        No boundary geometry — fit tested on area only ({fit.method?.replace("_", " ")}). Draw the boundary on the dossier map to test the layout properly.
      </div>
    );
  }
  const all = [...rings(geom.site), ...rings(geom.buildable), ...rings(geom.house)].flat();
  const xs = all.map((p) => p[0]);
  const ys = all.map((p) => p[1]);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const pad = 24;
  const s = Math.min((width - 2 * pad) / (maxx - minx || 1), (height - 2 * pad) / (maxy - miny || 1));
  const tx = (x: number) => pad + (x - minx) * s;
  const ty = (y: number) => height - pad - (y - miny) * s;
  const path = (r: number[][]) => r.map((p, i) => `${i ? "L" : "M"}${tx(p[0]).toFixed(1)},${ty(p[1]).toFixed(1)}`).join(" ") + "Z";
  const bar = [5, 10, 20, 50].find((m) => m * s > 50) ?? 50;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ background: "#f7f8f4", borderRadius: 8, border: "1px solid var(--line)", maxWidth: "100%" }}>
      {rings(geom.site).map((r, i) => (
        <path key={`s${i}`} d={path(r)} fill="#e7eee3" stroke="#2f5d50" strokeWidth={1.5} />
      ))}
      {rings(geom.buildable).map((r, i) => (
        <path key={`b${i}`} d={path(r)} fill="none" stroke="#7a8f55" strokeDasharray="4 3" />
      ))}
      {rings(geom.house).map((r, i) => (
        <path key={`h${i}`} d={path(r)} fill="#c9b27c" stroke="#6b5a2f" strokeWidth={1.2} />
      ))}
      <g transform={`translate(${width - 30},${30})`}>
        <path d="M0,-14 L6,6 L0,2 L-6,6 Z" fill="#1c2629" />
        <text y={20} fontSize={10} textAnchor="middle" fill="#1c2629">
          N
        </text>
      </g>
      <g transform={`translate(${pad},${height - 10})`}>
        <rect width={bar * s} height={4} fill="#1c2629" />
        <text x={bar * s + 6} y={5} fontSize={10} fill="#44535a">
          {bar} m
        </text>
      </g>
      <text x={pad} y={16} fontSize={10.5} fill="#44535a">
        Boundary · {fit.setback_m ?? 2.5} m setback zone (dashed) · house footprint
      </text>
    </svg>
  );
}

/** Concept floor plan(s) drawn from parametric room rectangles. */
export function FloorPlan({ option, width = 420 }: { option: DesignOption; width?: number }) {
  const L = option.dims.internal_length_m;
  const W = option.dims.internal_width_m;
  const floors = Array.from(new Set(option.rooms.map((r) => r.floor))).sort();
  const pad = 14;
  const s = (width - 2 * pad) / L;
  const h = W * s + 2 * pad + 18;
  return (
    <div className="stack" style={{ gap: 8 }}>
      {floors.map((fl) => (
        <svg key={fl} width={width} height={h} viewBox={`0 0 ${width} ${h}`} style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 8, maxWidth: "100%" }}>
          <text x={pad} y={12} fontSize={10.5} fill="#44535a">
            {fl === 0 ? "Ground floor" : "First floor"} · internal {L.toFixed(1)} × {W.toFixed(1)} m · south at bottom
          </text>
          <rect x={pad - 3} y={18 + pad - 3} width={L * s + 6} height={W * s + 6} fill="none" stroke="#1c2629" strokeWidth={4} />
          {option.rooms
            .filter((r) => r.floor === fl)
            .map((r) => {
              const x = pad + r.x * s;
              const y = 18 + pad + (W - r.y - r.h) * s;
              return (
                <g key={r.name}>
                  <rect x={x} y={y} width={r.w * s} height={r.h * s} fill={ROOM_FILL[r.kind] ?? "#fff"} stroke="#7b878c" strokeWidth={1} />
                  <text x={x + 5} y={y + 14} fontSize={10.5} fill="#1c2629">
                    {r.name}
                  </text>
                  <text x={x + 5} y={y + 26} fontSize={9.5} fill="#7b878c">
                    {r.area_m2.toFixed(1)} m²
                  </text>
                </g>
              );
            })}
        </svg>
      ))}
      <div className="disclaimer">{option.disclaimer}</div>
    </div>
  );
}

/** Massing: simple axonometric box with pitched roof, to compare concepts at a glance. */
export function Massing({ option, width = 220, height = 150 }: { option: DesignOption; width?: number; height?: number }) {
  const L = option.dims.external_length_m;
  const W = option.dims.external_width_m;
  const H = option.storeys >= 2 ? 5.4 : option.storeys > 1 ? 4.2 : 2.7;
  const ridge = Math.min(3.2, W * 0.42);
  const k = Math.min(width / (L + W * 0.7 + 2), height / (H + ridge + W * 0.35 + 2));
  const ox = 10, oy = height - 10;
  const P = (x: number, y: number, z: number) => [ox + (x + y * 0.7) * k, oy - (z + y * 0.35) * k];
  const poly = (pts: number[][], fill: string) => <polygon points={pts.map((p) => p.join(",")).join(" ")} fill={fill} stroke="#44535a" strokeWidth={0.8} />;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      {poly([P(0, 0, 0), P(L, 0, 0), P(L, 0, H), P(0, 0, H)], "#e9e2d0")}
      {poly([P(L, 0, 0), P(L, W, 0), P(L, W, H), P(L, 0, H)], "#d8cfb8")}
      {poly([P(0, 0, H), P(L, 0, H), P(L, W / 2, H + ridge), P(0, W / 2, H + ridge)], "#55615f")}
      {poly([P(L, 0, H), P(L, W, H), P(L, W / 2, H + ridge)], "#c9bfa5")}
    </svg>
  );
}
