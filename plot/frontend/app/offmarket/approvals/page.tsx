"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ConfChip, MessagePanel, StatusChip } from "@/components/offmarket";
import { ParcelPlan } from "@/components/ParcelPlan";
import { Shell } from "@/components/Shell";
import { Chip, Empty, ErrorBox, Loading, ScoreBadge } from "@/components/ui";
import { api, area, gbp, label, useApi } from "@/lib/api";
import { ACCESS_LABEL, type LeadDetail, type Outreach } from "@/lib/offmarket";

/** One message at a time. Each approval confirms that message's own recipient; there is no bulk approval. */
export default function Approvals() {
  const q = useApi<{ items: Outreach[] }>("/offmarket/approvals");
  const [selId, setSelId] = useState<number | null>(null);
  const [o, setO] = useState<Outreach | null>(null);
  const [lead, setLead] = useState<LeadDetail | null>(null);

  useEffect(() => {
    if (!q.data) return;
    if (selId === null || !q.data.items.some((x) => x.id === selId)) setSelId(q.data.items.find((x) => x.status === "awaiting_approval")?.id ?? q.data.items[0]?.id ?? null);
  }, [q.data, selId]);
  useEffect(() => {
    if (selId === null) {
      setO(null);
      setLead(null);
      return;
    }
    api<Outreach>(`/offmarket/outreach/${selId}`).then((x) => {
      setO(x);
      api<LeadDetail>(`/offmarket/leads/${x.lead_id}`).then(setLead);
    });
  }, [selId]);

  const items = q.data?.items ?? [];
  return (
    <Shell title="Off-Market CRM / Approval queue">
      <div className="page-head">
        <div>
          <h1>Approval queue</h1>
          <p>Review each message with its property and research. Approving confirms this exact content for this exact recipient — one message at a time.</p>
        </div>
        <Link className="btn" href="/offmarket">
          Back to CRM
        </Link>
      </div>
      <ErrorBox error={q.error} />
      {!q.data ? (
        <Loading />
      ) : !items.length ? (
        <Empty>Nothing waiting for approval.</Empty>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 16 }}>
          <div className="card" style={{ padding: 8, alignSelf: "start" }}>
            <ul className="list-plain">
              {items.map((x) => (
                <li key={x.id} onClick={() => setSelId(x.id)} style={{ cursor: "pointer", padding: "8px 6px", borderRadius: 6, background: x.id === selId ? "var(--accent-soft)" : undefined }}>
                  <div className="small" style={{ fontWeight: 500, color: "var(--ink)" }}>
                    {x.lead_headline}
                  </div>
                  <div className="row small" style={{ gap: 6 }}>
                    <StatusChip s={x.status} />
                    <span>
                      {x.channel === "post" ? "Letter" : "Email"} · {label(x.kind)}
                    </span>
                  </div>
                  <div className="small">To: {x.recipient_name}</div>
                </li>
              ))}
            </ul>
          </div>
          <div className="grid g-main">
            <div className="card">
              {o && lead ? (
                <MessagePanel
                  o={o}
                  mailbox={lead.mailbox}
                  postal={lead.postal}
                  onChange={(n) => {
                    setO(n);
                    if (n.id !== selId) setSelId(n.id);
                    q.reload();
                  }}
                />
              ) : (
                <Loading />
              )}
            </div>
            <div className="stack">
              {lead ? (
                <div className="card stack">
                  <div className="row between">
                    <Chip tone="info">{lead.type_label}</Chip>
                    <ScoreBadge score={lead.score} fatal={lead.fatal} />
                  </div>
                  <ParcelPlan g={lead.geometry} size={260} labels plotArea={lead.metrics?.plot_area_m2 as number} />
                  <ConfChip c={lead.confidence} />
                  <dl className="dl small" style={{ gridTemplateColumns: "120px 1fr" }}>
                    <dt>Plot</dt>
                    <dd>{area(lead.metrics?.plot_area_m2 as number)}</dd>
                    <dt>Access</dt>
                    <dd>{ACCESS_LABEL[lead.access?.type ?? "unknown"]}</dd>
                    <dt>Planning</dt>
                    <dd>{lead.assessment.planning?.classification_label ?? "—"}</dd>
                    <dt>Max viable</dt>
                    <dd>{gbp(lead.assessment.residual?.max_recommended_offer)}</dd>
                    <dt>Owner (register)</dt>
                    <dd>{lead.owner?.name ?? "—"}</dd>
                    <dt>Title</dt>
                    <dd>{lead.owner?.title_number ?? "—"}</dd>
                  </dl>
                  <Link href={`/offmarket/${lead.id}`} className="small">
                    Full research →
                  </Link>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </Shell>
  );
}
