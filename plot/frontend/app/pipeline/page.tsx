"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { Shell } from "@/components/Shell";
import { Chip, Empty, ErrorBox, Loading, ScoreBadge } from "@/components/ui";
import { api, gbp, label, useApi } from "@/lib/api";
import type { SiteRow } from "@/lib/types";

type PItem = { id: number; site_id: number; stage: string; notes: string | null; checklist: Record<string, { label: string; done: boolean; note: string; from?: string }>; updated_at: string; site: SiteRow | null };

function PipelineInner() {
  return (
    <Shell title="Acquisition Pipeline">
      <div className="page-head">
        <div>
          <h1>Acquisition pipeline</h1>
          <p>Shortlisted sites move through research, contact, inspection, negotiation, survey and offer.</p>
        </div>
        <Link className="btn" href="/offmarket">
          Off-market prospects &amp; owner outreach →
        </Link>
      </div>
      <PipelineBoard />
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

export default function PipelinePage() {
  return (
    <Suspense fallback={<Loading />}>
      <PipelineInner />
    </Suspense>
  );
}
