"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { gbp, label } from "@/lib/api";
import type { SiteRow } from "@/lib/types";

export function ScoreBadge({ score, fatal, size }: { score: number | null | undefined; fatal?: boolean; size?: "lg" }) {
  const cls = fatal ? "s-fatal" : score === null || score === undefined ? "s-none" : score >= 60 ? "s-high" : score >= 40 ? "s-mid" : "s-low";
  return (
    <span className={`score ${cls} ${size === "lg" ? "lg" : ""}`} title={fatal ? "Fatal flag overrides the numeric score" : "Opportunity score /100"}>
      {fatal ? "✕" : score === null || score === undefined ? "–" : Math.round(score)}
    </span>
  );
}

const STATUS_LABEL: Record<string, string> = {
  confirmed: "Confirmed",
  estimated: "Estimated",
  unknown: "Unknown",
  requires_investigation: "Requires investigation",
};

export function Status({ s }: { s: string }) {
  return <span className={`chip ${s}`}>{STATUS_LABEL[s] ?? label(s)}</span>;
}

export function Chip({ children, tone, title }: { children: ReactNode; tone?: string; title?: string }) {
  return (
    <span className={`chip ${tone ?? ""}`} title={title}>
      {children}
    </span>
  );
}

export function Confidence({ c }: { c: { score: number; label: string } | null | undefined }) {
  if (!c) return null;
  const tone = c.label === "high" ? "good" : c.label === "medium" ? "warn" : "unknown";
  return (
    <span className={`chip ${tone}`} title={`Evidence confidence ${(c.score * 100).toFixed(0)}% — separate from attractiveness`}>
      data {c.label}
    </span>
  );
}

export function Glyph({ g, size = 56 }: { g: SiteRow["glyph"]; size?: number }) {
  if (g.type === "polygon") {
    const pts = g.points.map(([x, y]) => `${4 + x * (size - 8)},${4 + y * (size - 8)}`).join(" ");
    return (
      <svg className="glyph" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-label="site outline">
        <polygon points={pts} fill="rgba(47,93,80,0.18)" stroke="#2f5d50" strokeWidth="1.5" />
        <text x={size - 4} y={size - 4} fontSize="8" textAnchor="end" fill="#7b878c">
          {g.span_m}m
        </text>
      </svg>
    );
  }
  const r = g.area_m2 ? Math.max(4, Math.min(size / 2 - 6, Math.sqrt(g.area_m2) / 4)) : 5;
  return (
    <svg className="glyph" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-label="site point">
      {g.area_m2 ? <circle cx={size / 2} cy={size / 2} r={r} fill="rgba(47,93,80,0.12)" stroke="#2f5d50" strokeDasharray="3 2" /> : null}
      <circle cx={size / 2} cy={size / 2} r="3.5" fill="#2f5d50" />
    </svg>
  );
}

const COMP_LABEL: Record<string, string> = { planning: "Planning", financial: "Financial", physical: "Physical & servicing", location: "Location & lifestyle", discount: "Discount & potential" };
const COMP_COLOR: Record<string, string> = { planning: "#2f5d50", financial: "#5d7f4a", physical: "#8a9a5b", location: "#c9b27c", discount: "#a3886b" };

export function ScoreStack({ components }: { components: Record<string, { score: number; max: number }> }) {
  const keys = ["planning", "financial", "physical", "location", "discount"].filter((k) => components[k]);
  return (
    <div className="bar" title="Score composition">
      {keys.map((k) => (
        <span key={k} style={{ width: `${components[k].score}%`, background: COMP_COLOR[k] }} title={`${COMP_LABEL[k]} ${components[k].score.toFixed(1)}/${components[k].max}`} />
      ))}
    </div>
  );
}

export function ScoreBreakdown({ components }: { components: Record<string, { score: number; max: number; notes?: string[]; why?: string[]; overridden_from?: number; override_reason?: string }> }) {
  return (
    <div>
      {["planning", "financial", "physical", "location", "discount"].map((k) =>
        components[k] ? (
          <div className="compbar" key={k}>
            <span className="ink2">{COMP_LABEL[k]}</span>
            <div className="bar">
              <span style={{ width: `${(components[k].score / components[k].max) * 100}%`, background: COMP_COLOR[k] }} />
            </div>
            <span className="num mono" style={{ textAlign: "right" }}>
              {components[k].score.toFixed(1)}/{components[k].max}
              {components[k].overridden_from !== undefined ? " *" : ""}
            </span>
          </div>
        ) : null,
      )}
    </div>
  );
}

export function FatalFlags({ flags }: { flags: { code: string; title: string }[] }) {
  if (!flags?.length) return null;
  return (
    <div className="pill-row">
      {flags.map((f) => (
        <span key={f.code} className="chip fatal" title="Non-negotiable flag: overrides the numeric score">
          ⚑ {f.title}
        </span>
      ))}
    </div>
  );
}

export function OriginChip({ o, stale }: { o: string; stale?: boolean }) {
  const map: Record<string, [string, string]> = { on_market: ["On market", "good"], off_market: ["Off-market lead", "info"], register: ["Public register", "info"] };
  const [t, tone] = map[o] ?? [label(o), ""];
  return (
    <span className="row" style={{ gap: 4 }}>
      <Chip tone={tone}>{t}</Chip>
      {stale ? (
        <Chip tone="warn" title="Source record is historic; status may have changed">
          historic record
        </Chip>
      ) : null}
    </span>
  );
}

export function OppRow({ r, right }: { r: SiteRow; right?: ReactNode }) {
  return (
    <div className="opp">
      <Glyph g={r.glyph} />
      <div style={{ minWidth: 0 }}>
        <Link href={`/sites/${r.id}`} className="title">
          {r.headline ?? `Site #${r.id}`}
        </Link>
        <div className="row small" style={{ gap: 6, marginTop: 3 }}>
          <OriginChip o={r.origin} stale={r.stale} />
          {r.lpa ? <span>{r.lpa}</span> : null}
          {r.planning_label ? <span>· {r.planning_label}</span> : null}
        </div>
        <FatalFlags flags={r.score.fatal_flags} />
      </div>
      <div style={{ textAlign: "right" }}>
        {right ?? (
          <>
            <ScoreBadge score={r.score.total} fatal={r.score.fatal} />
            <div className="small" style={{ marginTop: 4 }}>
              {r.asking_price ? gbp(r.asking_price) : r.residual_land_value !== null ? `land ≤ ${gbp(r.max_offer, { compact: true })}` : ""}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <div className="muted" style={{ padding: 20 }}>
      <span className="spinner" /> {what}…
    </div>
  );
}

export function ErrorBox({ error }: { error: { message: string; status?: number } | null }) {
  if (!error) return null;
  return <div className="notice bad">Error{error.status ? ` ${error.status}` : ""}: {error.message}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="notice">{children}</div>;
}

export function SourceLinks({ sources }: { sources: { title: string; url: string | null; date: string | null; source_id?: string | null; status?: string }[] }) {
  if (!sources?.length) return null;
  return (
    <div className="small" style={{ marginTop: 4 }}>
      {sources.map((s, i) => (
        <div key={i}>
          Source:{" "}
          {s.url ? (
            <a href={s.url} target="_blank" rel="noreferrer noopener">
              {s.title}
            </a>
          ) : (
            s.title
          )}
          {s.date ? ` · data dated ${s.date}` : ""}
          {s.source_id ? ` · ${s.source_id}` : ""}
        </div>
      ))}
    </div>
  );
}
