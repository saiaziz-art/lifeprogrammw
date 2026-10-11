"use client";

import Link from "next/link";
import { Fragment, useState } from "react";
import { Shell } from "@/components/Shell";
import { Chip, Empty, ErrorBox, Loading } from "@/components/ui";
import { api, ago, gbp, label, useApi, when } from "@/lib/api";
import type { Run } from "@/lib/types";

const TABS = ["Health & runs", "Sources", "Integrations", "Cost assumptions", "Missing information", "Expenses", "Audit log"] as const;

export default function Admin() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Health & runs");
  return (
    <Shell title="Administrator">
      <div className="page-head">
        <div>
          <h1>Administrator</h1>
          <p>Integration status, background jobs and their real outcomes, credentials, cost assumptions, expenses and gaps in the evidence.</p>
        </div>
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>
      {tab === "Health & runs" ? <Health /> : null}
      {tab === "Sources" ? <Sources /> : null}
      {tab === "Integrations" ? <Secrets /> : null}
      {tab === "Cost assumptions" ? <Rates /> : null}
      {tab === "Missing information" ? <Missing /> : null}
      {tab === "Expenses" ? <Expenses /> : null}
      {tab === "Audit log" ? <Audit /> : null}
    </Shell>
  );
}

type Overview = {
  now: string;
  services: { name: string; last_seen: string; seconds_ago: number; alive: boolean; detail: Record<string, unknown> }[];
  schedules: { id: number; name: string; cron: string; timezone: string; enabled: boolean; job_type: string; last_enqueued_for: string | null; next_fire: string | null; next_fire_local: string | null }[];
  jobs: { id: number; type: string; status: string; attempts: number; max_attempts: number; created_at: string; started_at: string | null; finished_at: string | null; scheduled_for: string | null; last_error: string | null; result: Record<string, unknown> }[];
};

function Health() {
  const ov = useApi<Overview>("/admin/overview");
  const runs = useApi<Run[]>("/admin/runs?limit=15");
  const [open, setOpen] = useState<Run | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [cron, setCron] = useState<Record<number, string>>({});

  const runNow = async () => {
    const r = await api<{ queued_job: number; note: string }>("/admin/runs", { method: "POST" });
    setMsg(`Queued job #${r.queued_job}. ${r.note}`);
    ov.reload();
  };
  const show = async (id: number) => setOpen(await api<Run>(`/admin/runs/${id}`));

  return (
    <div className="stack" style={{ gap: 16 }}>
      {msg ? <div className="notice info">{msg}</div> : null}
      <div className="grid g2">
        <div className="card">
          <div className="card-head">
            <h3>Background services</h3>
            <button className="btn sm primary" onClick={runNow}>
              Run sourcing now
            </button>
          </div>
          {ov.data?.services.length ? (
            ov.data.services.map((s) => (
              <div key={s.name} className="row between finding">
                <span>
                  <Chip tone={s.alive ? "ok" : "failed"}>{s.alive ? "alive" : "not responding"}</Chip> <span className="mono">{s.name}</span>
                </span>
                <span className="small">last heartbeat {ago(s.last_seen)}</span>
              </div>
            ))
          ) : (
            <div className="notice warn">No scheduler or worker heartbeat recorded. Start them with <code>plot scheduler</code> and <code>plot worker</code> (or docker compose) — until then the 07:00 run cannot happen.</div>
          )}
        </div>
        <div className="card">
          <h3>Schedules</h3>
          {ov.data?.schedules.map((s) => (
            <div key={s.id} className="stack" style={{ gap: 6 }}>
              <div className="row between">
                <strong>{label(s.name)}</strong>
                <Chip tone={s.enabled ? "ok" : "unknown"}>{s.enabled ? "enabled" : "disabled"}</Chip>
              </div>
              <div className="small">
                cron <code>{s.cron}</code> in {s.timezone} · next {s.next_fire_local ? when(s.next_fire) : "—"} ({ago(s.next_fire)}) · last enqueued for {when(s.last_enqueued_for)}
              </div>
              <div className="row">
                <input style={{ width: 140 }} value={cron[s.id] ?? s.cron} onChange={(e) => setCron({ ...cron, [s.id]: e.target.value })} />
                <button className="btn sm" onClick={async () => { await api(`/admin/schedules/${s.id}`, { method: "PATCH", json: { cron: cron[s.id] ?? s.cron } }); ov.reload(); }}>
                  Update cron
                </button>
                <button className="btn sm" onClick={async () => { await api(`/admin/schedules/${s.id}`, { method: "PATCH", json: { enabled: !s.enabled } }); ov.reload(); }}>
                  {s.enabled ? "Disable" : "Enable"}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="card">
        <h3>Run history</h3>
        <ErrorBox error={runs.error} />
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>#</th>
                <th>Kind</th>
                <th>Trigger</th>
                <th>Status</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Summary</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(runs.data ?? []).map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.id}</td>
                  <td>{label(r.kind)}</td>
                  <td>{r.trigger}</td>
                  <td>
                    <Chip tone={r.status}>{r.status}</Chip>
                  </td>
                  <td className="small">{when(r.started_at)}</td>
                  <td className="small">{r.finished_at ? `${Math.round((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000)} s` : "running"}</td>
                  <td className="small" style={{ maxWidth: 420 }}>
                    {r.error ? <span className="err">{r.error}</span> : null}
                    {Array.isArray(r.summary?.failing_sources) && (r.summary.failing_sources as string[]).length ? `failing: ${(r.summary.failing_sources as string[]).join(", ")}` : r.summary?.records_fetched !== undefined ? `fetched ${r.summary.records_fetched}, new ${r.summary.records_new}` : ""}
                  </td>
                  <td>
                    <button className="btn sm" onClick={() => show(r.id)}>
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {open ? (
        <div className="card">
          <div className="card-head">
            <h3>Run #{open.id} — {open.status}</h3>
            <button className="btn sm" onClick={() => setOpen(null)}>
              Close
            </button>
          </div>
          <div className="grid g2">
            <div>
              <h4>Steps</h4>
              {open.steps.map((s) => (
                <div key={s.name} className="finding">
                  <div className="row between">
                    <span>{s.label}</span>
                    <Chip tone={s.status}>{s.status}</Chip>
                  </div>
                  <div className="small">{s.seconds !== undefined ? `${s.seconds}s` : ""}</div>
                </div>
              ))}
            </div>
            <div>
              <h4>Sources</h4>
              {(open.sources ?? []).map((s, i) => (
                <div key={i} className="finding small">
                  <div className="row between">
                    <span className="mono">{s.source_id}</span>
                    <Chip tone={s.status}>{s.status}</Chip>
                  </div>
                  {s.records_fetched ? <div>fetched {s.records_fetched} · new {s.records_new} · changed {s.records_changed}</div> : null}
                  {s.error_message ? <div className={s.status === "failed" ? "err" : "muted"}>{s.error_class ? `[${s.error_class}] ` : ""}{s.error_message}</div> : null}
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
      <div className="card">
        <h3>Recent jobs</h3>
        <table className="t">
          <thead>
            <tr>
              <th>#</th>
              <th>Type</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Scheduled for</th>
              <th>Finished</th>
              <th>Error / result</th>
            </tr>
          </thead>
          <tbody>
            {(ov.data?.jobs ?? []).map((j) => (
              <tr key={j.id}>
                <td className="mono">{j.id}</td>
                <td>{j.type}</td>
                <td>
                  <Chip tone={j.status}>{j.status}</Chip>
                </td>
                <td>
                  {j.attempts}/{j.max_attempts}
                </td>
                <td className="small">{when(j.scheduled_for)}</td>
                <td className="small">{when(j.finished_at)}</td>
                <td className="small" style={{ maxWidth: 420 }}>
                  {j.last_error ? <span className="err">{j.last_error.split("\n").slice(-2).join(" ")}</span> : JSON.stringify(j.result).slice(0, 160)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type Src = { id: string; name: string; category: string; kind: string; enabled: boolean; licence: string | null; attribution: string | null; terms_url: string | null; docs_url: string | null; notes: string | null; config: Record<string, unknown>; required_secrets: string[]; last_attempt_at: string | null; last_success_at: string | null; last_data_change_at: string | null; last_status: string | null; last_error: string | null; consecutive_failures: number; history: { run_id: number; status: string; fetched: number; error_class: string | null; at: string | null }[] };

function Sources() {
  const { data, error, reload } = useApi<Src[]>("/admin/sources");
  const [edit, setEdit] = useState<string | null>(null);
  const [cfg, setCfg] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return <ErrorBox error={error} />;
  const groups = ["on_market", "off_market", "constraints", "enrichment", "market"];
  return (
    <div className="stack" style={{ gap: 16 }}>
      {msg ? <div className="notice info">{msg}</div> : null}
      {groups.map((g) => (
        <div className="card" key={g}>
          <h3>{{ on_market: "On-market listings", off_market: "Off-market & registers", constraints: "Planning & environmental constraints", enrichment: "Site enrichment", market: "Market evidence" }[g]}</h3>
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Last success</th>
                  <th>Recent runs</th>
                  <th>Licence / terms</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.filter((s) => s.category === g).map((s) => (
                  <Fragment key={s.id}>
                    <tr>
                      <td style={{ maxWidth: 360 }}>
                        <div style={{ fontWeight: 500 }}>{s.name}</div>
                        <div className="small mono">{s.id} · {s.kind}</div>
                        {s.notes ? <div className="small">{s.notes}</div> : null}
                      </td>
                      <td style={{ maxWidth: 360 }}>
                        <Chip tone={s.last_status ?? "unknown"}>{s.last_status ?? "never run"}</Chip>
                        {s.consecutive_failures > 1 ? <Chip tone="bad">{s.consecutive_failures}× in a row</Chip> : null}
                        {s.last_error ? <div className="small" style={{ marginTop: 4 }}>{s.last_error}</div> : null}
                      </td>
                      <td className="small">
                        {when(s.last_success_at)}
                        {s.last_data_change_at ? <div>data changed {ago(s.last_data_change_at)}</div> : null}
                      </td>
                      <td>
                        <div className="row" style={{ gap: 3 }}>
                          {s.history.map((h, i) => (
                            <span key={i} title={`run ${h.run_id}: ${h.status}${h.error_class ? ` (${h.error_class})` : ""}`} className="legend-dot" style={{ background: h.status === "ok" ? "var(--good)" : h.status === "failed" ? "var(--bad)" : h.status === "degraded" ? "var(--warn)" : "#c7c9c4" }} />
                          ))}
                        </div>
                      </td>
                      <td className="small" style={{ maxWidth: 260 }}>
                        {s.licence}
                        <div>
                          {s.terms_url ? <a href={s.terms_url} target="_blank" rel="noreferrer noopener">terms</a> : null} {s.docs_url ? <a href={s.docs_url} target="_blank" rel="noreferrer noopener">docs</a> : null}
                        </div>
                      </td>
                      <td>
                        <div className="stack" style={{ gap: 4 }}>
                          <button className="btn sm" onClick={async () => { await api(`/admin/sources/${s.id}`, { method: "PATCH", json: { enabled: !s.enabled } }); reload(); }}>
                            {s.enabled ? "Disable" : "Enable"}
                          </button>
                          {s.kind !== "blocked" && Object.keys(s.config).length ? (
                            <button className="btn sm" onClick={() => { setEdit(s.id); setCfg(JSON.stringify(s.config, null, 2)); }}>
                              Configure
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                    {edit === s.id ? (
                      <tr>
                        <td colSpan={6}>
                          <textarea className="mono" style={{ width: "100%", minHeight: 180 }} value={cfg} onChange={(e) => setCfg(e.target.value)} />
                          <div className="row">
                            <button className="btn sm primary" onClick={async () => { try { await api(`/admin/sources/${s.id}`, { method: "PATCH", json: { config: JSON.parse(cfg) } }); setEdit(null); setMsg("Configuration saved; used from the next run."); reload(); } catch (e) { setMsg(`Invalid configuration: ${(e as Error).message}`); } }}>
                              Save
                            </button>
                            <button className="btn sm" onClick={() => setEdit(null)}>
                              Cancel
                            </button>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function Secrets() {
  const { data, error, reload } = useApi<{ key: string; label: string; status: string }[]>("/admin/secrets");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return <ErrorBox error={error} />;
  return (
    <div className="card">
      <h3>Integration credentials</h3>
      <p className="small">Stored encrypted (Fernet/AES) with the server&apos;s PLOT_SECRET_KEY; values are never shown again or returned by the API.</p>
      {msg ? <div className="notice">{msg}</div> : null}
      <table className="t">
        <tbody>
          {data.map((s) => (
            <tr key={s.key}>
              <td>
                <div>{s.label}</div>
                <div className="small mono">{s.key}</div>
              </td>
              <td>
                <Chip tone={s.status === "set" ? "ok" : s.status === "undecryptable" ? "failed" : "unknown"}>{s.status}</Chip>
              </td>
              <td>
                <input type="password" autoComplete="off" placeholder="new value" value={vals[s.key] ?? ""} onChange={(e) => setVals({ ...vals, [s.key]: e.target.value })} />
              </td>
              <td className="row">
                <button className="btn sm" disabled={!vals[s.key]} onClick={async () => { try { await api(`/admin/secrets/${s.key}`, { method: "PUT", json: { value: vals[s.key] } }); setVals({ ...vals, [s.key]: "" }); setMsg(`${s.key} saved.`); reload(); } catch (e) { setMsg((e as Error).message); } }}>
                  Save
                </button>
                {s.status !== "missing" ? (
                  <button className="btn sm danger" onClick={async () => { await api(`/admin/secrets/${s.key}`, { method: "DELETE" }); reload(); }}>
                    Delete
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Rate = { key: string; category: string; description: string; unit: string; low: number; base: number; high: number; basis: string; source: string; source_date: string; user_override: boolean };

function Rates() {
  const { data, error, reload } = useApi<Rate[]>("/admin/cost-rates");
  const [edit, setEdit] = useState<Rate | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return <ErrorBox error={error} />;
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="notice warn">Default rates are PLOT screening assumptions (Q3 2026, southern England) — not quotations or licensed BCIS data. Replace them with quotes as you get them; every change records its source and date and re-assesses all sites.</div>
      {msg ? <div className="notice">{msg}</div> : null}
      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th>Item</th>
              <th>Unit</th>
              <th className="num">Low</th>
              <th className="num">Base</th>
              <th className="num">High</th>
              <th>Source</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.key}>
                <td style={{ maxWidth: 380 }}>
                  <div>{r.description}</div>
                  <div className="small mono">{r.category} · {r.key}</div>
                  {r.basis ? <div className="small">{r.basis}</div> : null}
                </td>
                <td className="small">{r.unit}</td>
                {edit?.key === r.key ? (
                  <>
                    {(["low", "base", "high"] as const).map((k) => (
                      <td key={k} className="num">
                        <input style={{ width: 90 }} type="number" step="any" value={edit[k]} onChange={(e) => setEdit({ ...edit, [k]: Number(e.target.value) })} />
                      </td>
                    ))}
                    <td>
                      <input placeholder="source (quote, index…)" value={edit.source} onChange={(e) => setEdit({ ...edit, source: e.target.value })} />
                      <input type="date" value={edit.source_date} onChange={(e) => setEdit({ ...edit, source_date: e.target.value })} />
                    </td>
                    <td>
                      <button className="btn sm primary" onClick={async () => { try { await api(`/admin/cost-rates/${r.key}`, { method: "PUT", json: { low: edit.low, base: edit.base, high: edit.high, source: edit.source, source_date: edit.source_date } }); setEdit(null); setMsg("Rate saved; sites will be re-assessed by the worker."); reload(); } catch (e) { setMsg((e as Error).message); } }}>
                        Save
                      </button>
                    </td>
                  </>
                ) : (
                  <>
                    <td className="num">{r.unit.startsWith("%") ? `${(r.low * 100).toFixed(1)}%` : gbp(r.low)}</td>
                    <td className="num">{r.unit.startsWith("%") ? `${(r.base * 100).toFixed(1)}%` : gbp(r.base)}</td>
                    <td className="num">{r.unit.startsWith("%") ? `${(r.high * 100).toFixed(1)}%` : gbp(r.high)}</td>
                    <td className="small" style={{ maxWidth: 240 }}>
                      {r.user_override ? <Chip tone="info">yours</Chip> : null} {r.source} ({r.source_date})
                    </td>
                    <td>
                      <button className="btn sm" onClick={() => setEdit({ ...r })}>
                        Edit
                      </button>
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Missing() {
  const { data, error } = useApi<{ sites: { site_id: number; headline: string; gaps: string[] }[]; sources: { id: string; name: string; status: string; error: string | null }[]; inbox_unlocated: number; leads_awaiting_owner_research: number }>("/admin/missing");
  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  return (
    <div className="grid g-main">
      <div className="card">
        <h3>Sites with evidence gaps ({data.sites.length})</h3>
        <ul className="list-plain">
          {data.sites.slice(0, 200).map((s) => (
            <li key={s.site_id}>
              <Link href={`/sites/${s.site_id}`}>{s.headline}</Link>
              <div className="pill-row" style={{ marginTop: 4 }}>
                {s.gaps.map((g) => (
                  <Chip key={g}>{g}</Chip>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </div>
      <div className="stack" style={{ gap: 16 }}>
        <div className="card">
          <h3>Sources not delivering</h3>
          {data.sources.length ? data.sources.map((s) => (
            <div key={s.id} className="finding small">
              <div className="row between">
                <strong>{s.name}</strong>
                <Chip tone={s.status}>{s.status}</Chip>
              </div>
              {s.error}
            </div>
          )) : <Empty>All sources delivering.</Empty>}
        </div>
        <div className="card">
          <h3>Queues</h3>
          <div className="small">Unlocated listings in inbox: {data.inbox_unlocated}</div>
          <div className="small">Leads awaiting ownership research: {data.leads_awaiting_owner_research}</div>
        </div>
      </div>
    </div>
  );
}

function Expenses() {
  const { data, reload } = useApi<{ total_gbp: number; items: { id: number; category: string; description: string; amount_gbp: number; incurred_at: string; site_id: number | null }[] }>("/admin/expenses");
  const [f, setF] = useState({ category: "title_register", description: "", amount_gbp: "", site_id: "" });
  return (
    <div className="grid g2">
      <div className="card">
        <h3>Expenses — total {gbp(data?.total_gbp)}</h3>
        <p className="small">Track research spend: HMLR official copies (~£7), searches, API usage, surveys.</p>
        <table className="t">
          <tbody>
            {(data?.items ?? []).map((e) => (
              <tr key={e.id}>
                <td className="small">{when(e.incurred_at, false)}</td>
                <td>{e.description}</td>
                <td className="small">{e.category}</td>
                <td className="num">{gbp(e.amount_gbp)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card stack">
        <h3>Add expense</h3>
        <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
          <option value="title_register">Title register / plan</option>
          <option value="search">Searches</option>
          <option value="api">API / data</option>
          <option value="survey">Survey / report</option>
          <option value="other">Other</option>
        </select>
        <input placeholder="Description" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        <input placeholder="Amount (£)" inputMode="decimal" value={f.amount_gbp} onChange={(e) => setF({ ...f, amount_gbp: e.target.value })} />
        <input placeholder="Site # (optional)" value={f.site_id} onChange={(e) => setF({ ...f, site_id: e.target.value })} />
        <button className="btn primary" disabled={!f.description || !f.amount_gbp} onClick={async () => { await api("/admin/expenses", { method: "POST", json: { category: f.category, description: f.description, amount_gbp: Number(f.amount_gbp), site_id: f.site_id ? Number(f.site_id) : null } }); setF({ ...f, description: "", amount_gbp: "" }); reload(); }}>
          Add
        </button>
      </div>
    </div>
  );
}

function Audit() {
  const { data } = useApi<{ at: string; actor: string; action: string; entity: string | null; entity_id: string | null; detail: Record<string, unknown> }[]>("/admin/audit");
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>When</th>
            <th>Actor</th>
            <th>Action</th>
            <th>Entity</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((a, i) => (
            <tr key={i}>
              <td className="small">{when(a.at)}</td>
              <td className="small">{a.actor}</td>
              <td className="mono">{a.action}</td>
              <td className="small">{a.entity ? `${a.entity} ${a.entity_id ?? ""}` : ""}</td>
              <td className="small mono" style={{ maxWidth: 520, wordBreak: "break-word" }}>{JSON.stringify(a.detail).slice(0, 300)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
