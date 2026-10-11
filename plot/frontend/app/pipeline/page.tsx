"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Shell } from "@/components/Shell";
import { Chip, Empty, ErrorBox, Loading, ScoreBadge } from "@/components/ui";
import { ApiError, api, gbp, label, useApi } from "@/lib/api";
import type { SiteRow } from "@/lib/types";

type PItem = { id: number; site_id: number; stage: string; notes: string | null; checklist: Record<string, { label: string; done: boolean; note: string; from?: string }>; updated_at: string; site: SiteRow | null };
type Lead = { id: number; site_id: number; stage: string; signals: string[]; owner: Record<string, string>; availability_evidence: { kind: string; summary: string; date: string }[]; notes: string | null; outreach: { id: number; status: string; channel: string; created_at: string }[]; site: SiteRow | null };
type Outreach = { id: number; body: string; status: string; channel: string; recipient_name: string | null; compliance: { checks: { code: string; ok: boolean; blocking: boolean; text: string }[]; passed: boolean; lia_confirmed?: boolean } };

function PipelineInner() {
  const params = useSearchParams();
  const [tab, setTab] = useState(params.get("tab") === "leads" ? "leads" : "pipeline");
  return (
    <Shell title="Acquisition Pipeline">
      <div className="page-head">
        <div>
          <h1>Acquisition pipeline</h1>
          <p>Shortlisted sites move through research, contact, inspection, negotiation, survey and offer. Off-market leads advance only on recorded evidence.</p>
        </div>
      </div>
      <div className="tabs">
        <button className={tab === "pipeline" ? "on" : ""} onClick={() => setTab("pipeline")}>
          Shortlist pipeline
        </button>
        <button className={tab === "leads" ? "on" : ""} onClick={() => setTab("leads")}>
          Off-market leads
        </button>
      </div>
      {tab === "pipeline" ? <PipelineBoard /> : <LeadsBoard />}
    </Shell>
  );
}

function PipelineBoard() {
  const { data, error, loading, reload } = useApi<{ stages: string[]; items: PItem[] }>("/pipeline");
  const [open, setOpen] = useState<PItem | null>(null);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;
  if (!data.items.length) return <Empty>Nothing shortlisted yet. Use “Shortlist” on a site dossier.</Empty>;
  const move = async (it: PItem, stage: string) => {
    await api(`/pipeline/${it.id}`, { method: "PATCH", json: { stage } });
    reload();
  };
  return (
    <>
      <div className="kanban">
        {data.stages.map((st) => {
          const items = data.items.filter((i) => i.stage === st);
          return (
            <div className="kcol" key={st}>
              <h4>
                {label(st)} <span>{items.length}</span>
              </h4>
              {items.map((it) => {
                const done = Object.values(it.checklist).filter((c) => c.done).length;
                const total = Object.keys(it.checklist).length;
                return (
                  <div className="kcard" key={it.id}>
                    <div className="row between">
                      <Link href={`/sites/${it.site_id}`} style={{ fontWeight: 500 }}>
                        {it.site?.headline?.slice(0, 60) ?? `Site #${it.site_id}`}
                      </Link>
                      <ScoreBadge score={it.site?.score.total} fatal={it.site?.score.fatal} />
                    </div>
                    <div className="small" style={{ margin: "4px 0" }}>
                      Max offer {gbp(it.site?.max_offer)} · due diligence {done}/{total}
                    </div>
                    <div className="bar" style={{ marginBottom: 6 }}>
                      <span style={{ width: `${(done / Math.max(1, total)) * 100}%`, background: "var(--accent)" }} />
                    </div>
                    <div className="row" style={{ gap: 4 }}>
                      <select value={it.stage} onChange={(e) => move(it, e.target.value)} style={{ fontSize: 12, padding: "3px 6px" }}>
                        {data.stages.map((s) => (
                          <option key={s} value={s}>
                            {label(s)}
                          </option>
                        ))}
                      </select>
                      <button className="btn sm" onClick={() => setOpen(it)}>
                        Checklist
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
      {open ? <Checklist item={open} onClose={() => { setOpen(null); reload(); }} /> : null}
    </>
  );
}

function Checklist({ item, onClose }: { item: PItem; onClose: () => void }) {
  const [cl, setCl] = useState(item.checklist);
  const [notes, setNotes] = useState(item.notes ?? "");
  const save = async () => {
    await api(`/pipeline/${item.id}`, { method: "PATCH", json: { checklist: cl, notes } });
    onClose();
  };
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h3>Due-diligence checklist — {item.site?.headline}</h3>
        <div className="row">
          <button className="btn sm" onClick={onClose}>
            Cancel
          </button>
          <button className="btn sm primary" onClick={save}>
            Save
          </button>
        </div>
      </div>
      <div className="grid g2">
        <div>
          {Object.entries(cl).map(([k, c]) => (
            <label key={k} className="row" style={{ gap: 8, alignItems: "flex-start", padding: "4px 0" }}>
              <input type="checkbox" checked={c.done} onChange={(e) => setCl({ ...cl, [k]: { ...c, done: e.target.checked } })} />
              <span>
                {c.label}
                {c.from ? <span className="small"> ({c.from})</span> : null}
              </span>
            </label>
          ))}
        </div>
        <label className="f">
          Notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} style={{ minHeight: 260 }} />
        </label>
      </div>
    </div>
  );
}

function LeadsBoard() {
  const { data, error, loading, reload } = useApi<{ stages: string[]; items: Lead[] }>("/leads");
  const [sel, setSel] = useState<Lead | null>(null);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;
  if (!data.items.length) return <Empty>No off-market leads yet. They come from public registers and sites you pin manually.</Empty>;
  return (
    <>
      <div className="notice" style={{ marginBottom: 12 }}>
        An off-market lead is never treated as available for sale. Stages: potential site → parcel/title matched → ownership research → owner verified (needs a recorded source) → outreach approved (needs your approval of a compliant letter) → owner contacted → interest confirmed (needs recorded evidence) → negotiation → under offer.
      </div>
      <div className="kanban">
        {data.stages.map((st) => {
          const items = data.items.filter((i) => i.stage === st);
          return (
            <div className="kcol" key={st}>
              <h4>
                {label(st)} <span>{items.length}</span>
              </h4>
              {items.slice(0, 40).map((l) => (
                <div className="kcard" key={l.id} onClick={() => setSel(l)} style={{ cursor: "pointer", outline: sel?.id === l.id ? "2px solid var(--accent)" : undefined }}>
                  <div className="row between">
                    <span style={{ fontWeight: 500 }}>{l.site?.headline?.replace("Brownfield register: ", "").slice(0, 55)}</span>
                    <ScoreBadge score={l.site?.score.total} fatal={l.site?.score.fatal} />
                  </div>
                  <div className="small">{l.site?.lpa ?? ""} {l.site?.stale ? "· historic record" : ""}</div>
                </div>
              ))}
              {items.length > 40 ? <div className="small">+{items.length - 40} more</div> : null}
            </div>
          );
        })}
      </div>
      {sel ? <LeadPanel lead={sel} stages={data.stages} onChange={() => { reload(); }} onClose={() => setSel(null)} /> : null}
    </>
  );
}

function LeadPanel({ lead, stages, onChange, onClose }: { lead: Lead; stages: string[]; onChange: () => void; onClose: () => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [owner, setOwner] = useState({ name: lead.owner?.name ?? "", address: lead.owner?.address ?? "", owner_type: lead.owner?.owner_type ?? "individual", title_number: lead.owner?.title_number ?? "", source: lead.owner?.source ?? "", source_date: lead.owner?.source_date ?? "" });
  const [ev, setEv] = useState({ kind: "owner_reply", summary: "", date: "" });
  const [sender, setSender] = useState({ name: "", contact: "" });
  const [outreach, setOutreach] = useState<Outreach | null>(null);
  const [lia, setLia] = useState(false);
  const idx = stages.indexOf(lead.stage);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      onChange();
    } catch (e) {
      const d = (e as ApiError).detail as { requirements?: string[]; message?: string } | string;
      setMsg(typeof d === "object" && d?.requirements ? `Not yet: ${d.requirements.join("; ")}` : `Failed: ${(e as Error).message}`);
    }
  };
  const openOutreach = async (id: number) => setOutreach(await api<Outreach>(`/outreach/${id}`));

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h3>
          Lead #{lead.id}: <Link href={`/sites/${lead.site_id}`}>{lead.site?.headline}</Link>
        </h3>
        <button className="btn sm" onClick={onClose}>
          Close
        </button>
      </div>
      {msg ? <div className={`notice ${msg.startsWith("Not") || msg.startsWith("Failed") ? "warn" : "info"}`} style={{ marginBottom: 10 }}>{msg}</div> : null}
      <div className="row" style={{ marginBottom: 12 }}>
        <Chip tone="info">{label(lead.stage)}</Chip>
        {idx > 0 ? (
          <button className="btn sm" onClick={() => run(() => api(`/leads/${lead.id}`, { method: "PATCH", json: { stage: stages[idx - 1], note: "moved back" } }), "Moved back.")}>
            ← {label(stages[idx - 1])}
          </button>
        ) : null}
        {idx < stages.length - 1 ? (
          <button className="btn sm primary" onClick={() => run(() => api(`/leads/${lead.id}`, { method: "PATCH", json: { stage: stages[idx + 1] } }), `Advanced to ${label(stages[idx + 1])}.`)}>
            Advance → {label(stages[idx + 1])}
          </button>
        ) : null}
      </div>
      <div className="grid g3">
        <div className="stack">
          <h4>Verified owner</h4>
          <div className="small">Record only what an authoritative document shows (e.g. HM Land Registry official copy, ~£7). Never guessed.</div>
          <input placeholder="Owner name (as on register)" value={owner.name} onChange={(e) => setOwner({ ...owner, name: e.target.value })} />
          <input placeholder="Owner address for service" value={owner.address} onChange={(e) => setOwner({ ...owner, address: e.target.value })} />
          <select value={owner.owner_type} onChange={(e) => setOwner({ ...owner, owner_type: e.target.value })}>
            <option value="individual">Individual</option>
            <option value="company">Company</option>
            <option value="public_body">Public body</option>
            <option value="charity">Charity</option>
            <option value="unknown">Unknown</option>
          </select>
          <input placeholder="Title number" value={owner.title_number} onChange={(e) => setOwner({ ...owner, title_number: e.target.value })} />
          <input placeholder="Source document (required)" value={owner.source} onChange={(e) => setOwner({ ...owner, source: e.target.value })} />
          <input type="date" value={owner.source_date} onChange={(e) => setOwner({ ...owner, source_date: e.target.value })} />
          <button className="btn sm" disabled={!owner.name || owner.source.length < 5 || !owner.source_date} onClick={() => run(() => api(`/leads/${lead.id}`, { method: "PATCH", json: { owner } }), "Owner record saved.")}>
            Save owner record
          </button>
        </div>
        <div className="stack">
          <h4>Owner outreach</h4>
          {lead.outreach.map((o) => (
            <button key={o.id} className="btn sm" onClick={() => openOutreach(o.id)}>
              Letter #{o.id} — {o.status}
            </button>
          ))}
          <input placeholder="Your name" value={sender.name} onChange={(e) => setSender({ ...sender, name: e.target.value })} />
          <input placeholder="Your contact address / email" value={sender.contact} onChange={(e) => setSender({ ...sender, contact: e.target.value })} />
          <button
            className="btn sm"
            disabled={!sender.name || sender.contact.length < 5}
            onClick={() =>
              run(async () => {
                const o = await api<Outreach>(`/leads/${lead.id}/outreach`, { method: "POST", json: { channel: "post", sender_name: sender.name, sender_contact: sender.contact } });
                setOutreach(o);
              }, "Draft letter created — review and approve below.")
            }
          >
            Draft a postal letter
          </button>
          <div className="small">Letters are drafted only after the owner is verified, and can be marked sent only after you approve them. PLOT never sends them for you.</div>
        </div>
        <div className="stack">
          <h4>Evidence of willingness to sell</h4>
          {lead.availability_evidence.map((e, i) => (
            <div key={i} className="small">
              {e.date} · {label(e.kind)}: {e.summary}
            </div>
          ))}
          <select value={ev.kind} onChange={(e) => setEv({ ...ev, kind: e.target.value })}>
            <option value="owner_reply">Owner reply</option>
            <option value="agent_confirmation">Agent confirmation</option>
            <option value="meeting_note">Meeting note</option>
            <option value="written_offer">Written offer/terms</option>
            <option value="other">Other</option>
          </select>
          <textarea placeholder="What exactly was said/written" value={ev.summary} onChange={(e) => setEv({ ...ev, summary: e.target.value })} />
          <input type="date" value={ev.date} onChange={(e) => setEv({ ...ev, date: e.target.value })} />
          <button className="btn sm" disabled={ev.summary.length < 5 || !ev.date} onClick={() => run(() => api(`/leads/${lead.id}`, { method: "PATCH", json: { add_evidence: ev } }), "Evidence recorded.")}>
            Record evidence
          </button>
        </div>
      </div>
      {outreach ? (
        <div className="card flat" style={{ marginTop: 16 }}>
          <div className="card-head">
            <h3>
              Letter #{outreach.id} <Chip>{outreach.status}</Chip>
            </h3>
          </div>
          <div className="grid g2">
            <textarea value={outreach.body} readOnly={outreach.status !== "draft"} onChange={(e) => setOutreach({ ...outreach, body: e.target.value })} style={{ minHeight: 380, fontFamily: "var(--serif)", fontSize: 14 }} />
            <div className="stack">
              <h4>Compliance checks</h4>
              {outreach.compliance.checks.map((c) => (
                <div key={c.code} className="small">
                  <span className={`chip ${c.ok ? "ok" : c.blocking ? "failed" : "warn"}`}>{c.ok ? "pass" : c.blocking ? "blocking" : "warning"}</span> {c.text}
                </div>
              ))}
              <label className="row small" style={{ gap: 6 }}>
                <input type="checkbox" checked={lia} onChange={(e) => setLia(e.target.checked)} /> I confirm a legitimate-interests assessment for this one-off approach
              </label>
              <div className="row">
                {outreach.status === "draft" ? (
                  <>
                    <button className="btn sm" onClick={() => run(async () => setOutreach(await api<Outreach>(`/outreach/${outreach.id}`, { method: "PATCH", json: { body: outreach.body, action: "save", lia_confirmed: lia } })), "Saved; checks re-run.")}>
                      Save & re-check
                    </button>
                    <button className="btn sm primary" onClick={() => run(async () => setOutreach(await api<Outreach>(`/outreach/${outreach.id}`, { method: "PATCH", json: { body: outreach.body, action: "approve", lia_confirmed: lia } })), "Approved. Print and post it, then mark as sent.")}>
                      Approve letter
                    </button>
                  </>
                ) : null}
                {outreach.status === "approved" ? (
                  <button className="btn sm primary" onClick={() => run(async () => setOutreach(await api<Outreach>(`/outreach/${outreach.id}`, { method: "PATCH", json: { action: "mark_sent" } })), "Marked as sent.")}>
                    Mark as posted
                  </button>
                ) : null}
                {outreach.status !== "withdrawn" && outreach.status !== "sent" ? (
                  <button className="btn sm danger" onClick={() => run(async () => setOutreach(await api<Outreach>(`/outreach/${outreach.id}`, { method: "PATCH", json: { action: "withdraw" } })), "Withdrawn.")}>
                    Withdraw
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function PipelinePage() {
  return (
    <Suspense fallback={<Loading />}>
      <PipelineInner />
    </Suspense>
  );
}
