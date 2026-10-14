"use client";

import Link from "next/link";
import { useState } from "react";
import { AcqBar, ConfChip } from "@/components/offmarket";
import { ParcelPlan } from "@/components/ParcelPlan";
import { Shell } from "@/components/Shell";
import { Chip, Empty, ErrorBox, Loading, ScoreBadge } from "@/components/ui";
import { api, area, gbp, label, useApi } from "@/lib/api";
import { ACCESS_LABEL, CONTACT_METHOD_LABEL, DRAFT_STATUS, type LeadSummary, OWNERSHIP_LABEL, STAGE_LABEL, STAGES } from "@/lib/offmarket";

type Dashboard = {
  kpis: Record<string, number | null>;
  stages: { stage: string; count: number }[];
  strongest: LeadSummary[];
  land_values: { lead_id: number; max_viable_gbp: number; prudent_gbp: number; stage: string }[];
  declines: { by_reason: Record<string, number>; by_type: Record<string, Record<string, number>>; tips: string[] };
  briefing: { headline: string; new_prospects: number; awaiting_official_copy: number; drafts_awaiting_approval: number; replies_to_handle: number; downgraded: string[] };
  sandbox: boolean;
};

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

function LeadCard({ l }: { l: LeadSummary }) {
  const m = l.metrics ?? {};
  const own = OWNERSHIP_LABEL[l.ownership_status] ?? [label(l.ownership_status), ""];
  const d = l.draft ? DRAFT_STATUS[l.draft.status] ?? [label(l.draft.status), ""] : null;
  return (
    <div className="card" style={{ display: "grid", gridTemplateColumns: "132px 1fr 170px", gap: 16, alignItems: "start" }}>
      <ParcelPlan g={l.geometry} size={132} />
      <div style={{ minWidth: 0 }}>
        <Link href={`/offmarket/${l.id}`} className="title" style={{ fontWeight: 600, fontSize: 15, color: "var(--ink)" }}>
          {l.site?.headline ?? `Prospect #${l.id}`}
        </Link>
        <div className="row small" style={{ gap: 6, marginTop: 4 }}>
          <Chip tone="info">{l.type_label}</Chip>
          <Chip tone={own[1]}>{own[0]}</Chip>
          <Chip>{STAGE_LABEL[l.stage]}</Chip>
          {l.reply_waiting ? <Chip tone="good">reply waiting</Chip> : null}
        </div>
        <div className="dl small" style={{ gridTemplateColumns: "130px 1fr", marginTop: 8, gap: "3px 10px" }}>
          <span className="muted">Indicative plot</span>
          <span>
            {area(m.plot_area_m2 as number)} {m.parcel_area_m2 ? <span className="muted">of {area(m.parcel_area_m2 as number)} title extent</span> : null}
          </span>
          <span className="muted">Access</span>
          <span>
            {ACCESS_LABEL[l.access?.type ?? "unknown"] ?? label(l.access?.type)}
            {l.access?.frontage_m ? ` · ${l.access.frontage_m} m frontage` : ""}
            {l.access?.road_name ? ` · ${l.access.road_name}` : ""}
          </span>
          <span className="muted">Planning</span>
          <span>{l.site?.planning_label ?? "Not yet screened"}</span>
          {l.constraints.length ? (
            <>
              <span className="muted">Constraints</span>
              <span>{[...new Set(l.constraints.map((c) => label(c.dataset)))].join(", ")}</span>
            </>
          ) : null}
          <span className="muted">Max viable land price</span>
          <span>{l.site?.max_offer !== undefined && l.site?.max_offer !== null ? gbp(l.site.max_offer) : "Not yet costed"}</span>
          <span className="muted">Owner benefit</span>
          <span>{l.benefits?.[0] ?? "—"}</span>
          <span className="muted">Contact method</span>
          <span>{CONTACT_METHOD_LABEL[l.contact_method ?? ""] ?? "—"}</span>
          <span className="muted">Draft</span>
          <span>{d ? <Chip tone={d[1]}>{d[0]}</Chip> : "None"}</span>
        </div>
        {l.flags.filter((f) => f.severity !== "info").length ? (
          <div className="pill-row" style={{ marginTop: 8 }}>
            {l.flags.map((f) => (
              <Chip key={f.code} tone={f.severity === "fatal" ? "fatal" : f.severity === "major" ? "bad" : "warn"}>
                ⚑ {f.title}
              </Chip>
            ))}
          </div>
        ) : null}
      </div>
      <div style={{ textAlign: "right" }} className="stack">
        <div>
          <ScoreBadge score={l.score} fatal={l.fatal} size="lg" />
          <div className="small" style={{ marginTop: 4 }}>
            acquisition score
          </div>
        </div>
        <ConfChip c={l.confidence} />
        <AcqBar c={l.components} />
        <div className="small ink2" style={{ textAlign: "left", borderTop: "1px dashed var(--line)", paddingTop: 6 }}>
          <b>Next:</b> {l.next_action ?? "—"}
        </div>
      </div>
    </div>
  );
}

function Board({ items }: { items: LeadSummary[] }) {
  return (
    <div className="kanban">
      {STAGES.map((s) => {
        const col = items.filter((l) => l.stage === s);
        return (
          <div className="kcol" key={s}>
            <h4>
              <span>{STAGE_LABEL[s]}</span> <span>{col.length}</span>
            </h4>
            {col.map((l) => (
              <Link key={l.id} href={`/offmarket/${l.id}`} className="kcard" style={{ display: "block", color: "var(--ink)" }}>
                <div className="row between">
                  <span style={{ fontWeight: 500 }}>{l.type_label}</span>
                  <ScoreBadge score={l.score} fatal={l.fatal} />
                </div>
                <div className="small">{l.site?.headline}</div>
                <div className="small ink2" style={{ marginTop: 4 }}>
                  {l.next_action}
                </div>
              </Link>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export default function OffMarket() {
  const dash = useApi<Dashboard>("/offmarket/dashboard");
  const [tab, setTab] = useState<"shortlist" | "board" | "feedback" | "dnc">("shortlist");
  const [type, setType] = useState("");
  const [closed, setClosed] = useState(false);
  const leads = useApi<{ items: LeadSummary[]; types: Record<string, string> }>(`/offmarket/leads?include_closed=${closed}${type ? `&type=${type}` : ""}`, [type, closed]);
  const supp = useApi<{ items: { id: number; kind: string; label: string; reason: string; source: string; created_at: string }[] }>(tab === "dnc" ? "/offmarket/suppressions" : null, [tab]);
  const [msg, setMsg] = useState<string | null>(null);

  async function act(path: string, done: string) {
    setMsg(null);
    try {
      const r = await api<Record<string, unknown>>(path, { method: "POST" });
      setMsg(`${done} ${path.endsWith("sync-replies") ? `(${r.new ?? 0} new repl${r.new === 1 ? "y" : "ies"}${(r.errors as string[])?.length ? `; errors: ${(r.errors as string[]).join("; ")}` : ""})` : ""}`);
      dash.reload();
      leads.reload();
    } catch (e) {
      setMsg(`Failed: ${(e as Error).message}`);
    }
  }

  const k = dash.data?.kpis ?? {};
  return (
    <Shell title="Off-Market CRM">
      <div className="page-head">
        <div>
          <h1>Off-market acquisition</h1>
          <p>A small number of exceptionally suitable owners, researched properly and approached respectfully. PLOT researches, qualifies and drafts; nothing is sent without your individual approval.</p>
        </div>
        <div className="row">
          <Link className="btn primary" href="/offmarket/approvals">
            Approval queue{k.drafts_awaiting_approval ? ` (${k.drafts_awaiting_approval})` : ""}
          </Link>
          <button className="btn" onClick={() => act("/offmarket/sync-replies", "Mailbox checked.")}>
            Check replies
          </button>
          <button className="btn" onClick={() => act("/offmarket/discover", "Discovery queued for the worker.")}>
            Run discovery now
          </button>
        </div>
      </div>
      {msg ? <div className={`notice ${msg.startsWith("Failed") ? "bad" : "info"}`} style={{ marginBottom: 12 }}>{msg}</div> : null}
      <ErrorBox error={dash.error} />
      {dash.data ? (
        <>
          <div className="card" style={{ marginBottom: 14, borderLeft: "4px solid var(--accent)" }}>
            <h4>Today&apos;s acquisition briefing</h4>
            <div style={{ fontFamily: "var(--serif)", fontSize: 18 }}>{dash.data.briefing.headline}</div>
            <div className="small">Real activity only — counts come from recorded discoveries, drafts, provider-confirmed sends, letters you marked as posted and replies received.</div>
          </div>
          <div className="grid g4" style={{ marginBottom: 14 }}>
            <div className="card kpi">
              <div className="label">Viable prospects</div>
              <div className="value">{k.viable_prospects}</div>
              <div className="sub">{k.new_this_week} new this week</div>
            </div>
            <div className="card kpi">
              <div className="label">Drafts awaiting approval</div>
              <div className="value">{k.drafts_awaiting_approval}</div>
              <div className="sub">{k.drafts_needing_review} need changes or compliance review</div>
            </div>
            <div className="card kpi">
              <div className="label">Actually sent</div>
              <div className="value">
                {k.letters_posted} <span className="small">letters</span> · {k.emails_sent} <span className="small">emails</span>
              </div>
              <div className="sub">{k.owners_contacted} owners contacted</div>
            </div>
            <div className="card kpi">
              <div className="label">Response · positive interest</div>
              <div className="value">
                {pct(k.response_rate)} · {pct(k.positive_rate)}
              </div>
              <div className="sub">
                {k.replies_to_handle} repl{k.replies_to_handle === 1 ? "y" : "ies"} to handle · {k.follow_ups_due} follow-up{k.follow_ups_due === 1 ? "" : "s"} due · {k.active_negotiations} negotiating
              </div>
            </div>
          </div>
        </>
      ) : (
        <Loading />
      )}
      <div className="tabs">
        {(
          [
            ["shortlist", "Shortlist"],
            ["board", "Pipeline board"],
            ["feedback", "Values & feedback"],
            ["dnc", "Do-not-contact"],
          ] as const
        ).map(([t, l]) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {l}
          </button>
        ))}
      </div>
      {tab === "shortlist" || tab === "board" ? (
        <div className="row" style={{ marginBottom: 12 }}>
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">All opportunity types</option>
            {Object.entries(leads.data?.types ?? {}).map(([k2, v]) => (
              <option key={k2} value={k2}>
                {v}
              </option>
            ))}
          </select>
          <label className="row small" style={{ gap: 6 }}>
            <input type="checkbox" checked={closed} onChange={(e) => setClosed(e.target.checked)} /> include closed
          </label>
          <span className="small">Ranked by acquisition score; anything with a fatal flag sinks to the bottom.</span>
        </div>
      ) : null}
      <ErrorBox error={leads.error} />
      {tab === "shortlist" ? (
        leads.data ? (
          leads.data.items.length ? (
            <div className="stack">
              {leads.data.items.map((l) => (
                <LeadCard key={l.id} l={l} />
              ))}
            </div>
          ) : (
            <Empty>No off-market prospects yet. The 07:00 run analyses your prospecting areas (Preferences → Off-market) using HM Land Registry title extents, building footprints and planning constraints.</Empty>
          )
        ) : (
          <Loading />
        )
      ) : null}
      {tab === "board" && leads.data ? <Board items={leads.data.items} /> : null}
      {tab === "feedback" && dash.data ? (
        <div className="grid g2">
          <div className="card">
            <h3>Estimated land acquisition values</h3>
            <div className="small">Maximum viable land price from the residual valuation against your all-in budget (base case). Private — never sent.</div>
            <table className="t" style={{ marginTop: 8 }}>
              <thead>
                <tr>
                  <th>Prospect</th>
                  <th>Stage</th>
                  <th className="num">Prudent</th>
                  <th className="num">Max viable</th>
                </tr>
              </thead>
              <tbody>
                {dash.data.land_values.map((v) => (
                  <tr key={v.lead_id}>
                    <td>
                      <Link href={`/offmarket/${v.lead_id}`}>#{v.lead_id}</Link>
                    </td>
                    <td>{STAGE_LABEL[v.stage]}</td>
                    <td className="num">{gbp(v.prudent_gbp)}</td>
                    <td className="num">{gbp(v.max_viable_gbp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <h3>Why owners decline</h3>
            {Object.keys(dash.data.declines.by_reason).length ? (
              <ul className="list-plain">
                {Object.entries(dash.data.declines.by_reason).map(([r, n]) => (
                  <li key={r} className="row between">
                    <span>{r}</span> <b>{n}</b>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No declines recorded yet.</Empty>
            )}
            {dash.data.declines.tips.map((t) => (
              <div key={t} className="notice info small" style={{ marginTop: 8 }}>
                {t}
              </div>
            ))}
            <div className="small" style={{ marginTop: 8 }}>
              Reasons describe the deal (price, privacy, planning, timing) — owners are never profiled by personal characteristics.
            </div>
          </div>
        </div>
      ) : null}
      {tab === "dnc" ? (
        <div className="card">
          <h3>Do-not-contact list</h3>
          <div className="small">Permanent. Stored as one-way hashes with a minimal label; checked before every approval and every send.</div>
          {supp.data ? (
            <table className="t" style={{ marginTop: 8 }}>
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Match</th>
                  <th>Reason</th>
                  <th>Source</th>
                  <th>Added</th>
                </tr>
              </thead>
              <tbody>
                {supp.data.items.map((s) => (
                  <tr key={s.id}>
                    <td>{s.label}</td>
                    <td>{label(s.kind)}</td>
                    <td>{s.reason}</td>
                    <td>{s.source}</td>
                    <td>{s.created_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Loading />
          )}
        </div>
      ) : null}
    </Shell>
  );
}
