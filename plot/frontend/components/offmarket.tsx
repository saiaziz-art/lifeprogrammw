"use client";

import { useEffect, useMemo, useState } from "react";
import { Chip } from "@/components/ui";
import { api, label, when } from "@/lib/api";
import { COMP_COLOR, COMP_LABEL, COMP_ORDER, type ComplianceCheck, DRAFT_STATUS, type LeadSummary, type Outreach } from "@/lib/offmarket";

export function AcqBar({ c }: { c: LeadSummary["components"] }) {
  return (
    <div className="bar" title="Acquisition score composition">
      {COMP_ORDER.filter((k) => c[k]).map((k) => (
        <span key={k} style={{ width: `${c[k].score}%`, background: COMP_COLOR[k] }} title={`${COMP_LABEL[k]} ${c[k].score}/${c[k].max}`} />
      ))}
    </div>
  );
}

export function ConfChip({ c }: { c: LeadSummary["confidence"] }) {
  if (!c) return null;
  const tone = c.label === "high" ? "good" : c.label === "medium" ? "warn" : "unknown";
  return (
    <Chip tone={tone} title={`Evidence confidence ${Math.round(c.score * 100)}%${c.cap_reason ? ` — capped: ${c.cap_reason}` : ""}. Separate from how attractive the site looks.`}>
      evidence {c.label}
      {c.cap_reason ? " · capped" : ""}
    </Chip>
  );
}

export function StatusChip({ s }: { s: string }) {
  const [t, tone] = DRAFT_STATUS[s] ?? [label(s), ""];
  return <Chip tone={tone}>{t}</Chip>;
}

const ICON: Record<string, [string, string]> = { pass: ["✓", "var(--good)"], fail: ["✕", "var(--bad)"], review: ["?", "var(--warn)"], warn: ["!", "var(--warn)"] };

export function ComplianceList({ checks }: { checks: ComplianceCheck[] }) {
  return (
    <ul className="list-plain small">
      {checks.map((c) => (
        <li key={c.code + c.label} style={{ display: "grid", gridTemplateColumns: "18px 1fr", gap: 6, padding: "5px 0" }}>
          <b style={{ color: ICON[c.status][1] }}>{ICON[c.status][0]}</b>
          <span>
            <span className="ink2">{c.label}</span>
            {c.detail ? <span className="muted"> — {c.detail}</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

function words(body: string): number {
  const lines = body.trim().split("\n");
  if (lines.length && /^(Dear|Hi|Hello)\b/.test(lines[0].trim())) lines.shift();
  const text = lines.join("\n").split(/\n\s*(Best wishes|Kind regards|All the best|With best wishes|Many thanks|Yours sincerely|Yours faithfully|Warm regards|Best regards),?\s*\n/)[0];
  return (text.match(/[A-Za-z0-9£'’-]+/g) ?? []).length;
}

const LIMITS: Record<string, [number, number]> = { initial: [80, 140], follow_up: [45, 120], reply: [35, 180] };
const EDITABLE = ["draft", "needs_changes", "needs_compliance_review", "awaiting_approval", "approved"];

/** One outbound message: edit, regenerate, reject, approve (with recipient confirmation) and deliver. */
export function MessagePanel({ o, onChange, mailbox, postal }: { o: Outreach; onChange: (o: Outreach) => void; mailbox: { provider: string; email: string } | null; postal: { provider: string; test_mode: boolean; estimate_gbp: number } | null }) {
  const [subject, setSubject] = useState(o.subject ?? "");
  const [body, setBody] = useState(o.body ?? "");
  const [instr, setInstr] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [priceOk, setPriceOk] = useState(false);
  const [note, setNote] = useState("");
  const [postedOn, setPostedOn] = useState(new Date().toISOString().slice(0, 10));
  const [postage, setPostage] = useState<number>(postal?.estimate_gbp ?? 0.87);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    setSubject(o.subject ?? "");
    setBody(o.body ?? "");
    setConfirm(false);
    setPriceOk(false);
    setMsg(null);
  }, [o.id, o.subject, o.body]);
  const dirty = subject !== (o.subject ?? "") || body !== (o.body ?? "");
  const editable = EDITABLE.includes(o.status);
  const n = useMemo(() => words(body), [body]);
  const [lo, hi] = LIMITS[o.kind] ?? [80, 140];
  const hasPrice = /£\s?\d/.test(`${subject}\n${body}`);
  const what = o.channel === "post" ? "letter" : "email";

  async function run(fn: () => Promise<Outreach>, done: string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      onChange(r);
      setMsg(done);
    } catch (e) {
      setMsg(`Not done: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }
  const post = (path: string, json?: unknown) => api<Outreach>(`/offmarket/outreach/${o.id}${path}`, { method: "POST", json: json ?? {} });

  return (
    <div className="stack">
      <div className="row between">
        <div className="row" style={{ gap: 6 }}>
          <StatusChip s={o.status} />
          <Chip>{o.channel === "post" ? "Letter" : "Email"}</Chip>
          <Chip>{label(o.kind)}</Chip>
          {o.generator ? <Chip tone="outline" title={String(o.generator_meta?.note ?? "")}>{o.generator === "ai" ? "written by Claude" : o.generator === "composer" ? "composed by PLOT" : "edited by you"}</Chip> : null}
          <span className="small mono">{o.reference}</span>
        </div>
        <span className="small">created {when(o.created_at)}</span>
      </div>
      {o.generator_meta?.note ? <div className="notice small">{String(o.generator_meta.note)}</div> : null}

      <div className="card flat" style={{ background: "var(--surface-2)" }}>
        <div className="small muted">Recipient ({label(o.recipient_type)})</div>
        <div style={{ whiteSpace: "pre-line", fontWeight: 500 }}>
          {o.recipient_name}
          {"\n"}
          {o.channel === "email" ? o.recipient_email : o.recipient_address}
        </div>
      </div>

      <label className="f">
        {o.channel === "email" ? "Subject" : "Heading"}
        <input value={subject} onChange={(e) => setSubject(e.target.value)} readOnly={!editable} />
      </label>
      <label className="f">
        <span className="row between">
          <span>Message</span>
          <span className={n < lo || n > hi ? "err" : "muted"}>
            {n} words (aim {lo}–{hi})
          </span>
        </span>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} readOnly={!editable} rows={Math.max(12, body.split("\n").reduce((a, l) => a + Math.max(1, Math.ceil(l.length / 78)), 0) + 1)} style={{ fontFamily: "var(--serif)", fontSize: 15, lineHeight: 1.55 }} />
      </label>
      {o.status === "approved" && dirty ? <div className="notice warn small">Saving an edit clears your approval; you will need to approve the new version.</div> : null}
      <div className="small" style={{ whiteSpace: "pre-line", borderLeft: "3px solid var(--line)", paddingLeft: 10 }}>
        {o.footer}
      </div>

      {o.compliance?.checks?.length ? (
        <div>
          <h4>Checks</h4>
          <ComplianceList checks={o.compliance.checks} />
          {(o.compliance.issues ?? []).filter((i) => i.severity === "warn").map((i) => (
            <div key={i.code + i.message} className="small muted">
              Suggestion: {i.message}
            </div>
          ))}
          {o.compliance.review_cleared ? <div className="small">Compliance review cleared by {o.compliance.review_cleared.by}: “{o.compliance.review_cleared.note}”</div> : null}
        </div>
      ) : null}

      {msg ? <div className={`notice ${msg.startsWith("Not") ? "bad" : "info"}`}>{msg}</div> : null}

      {editable ? (
        <div className="row">
          <button className="btn" disabled={!dirty || busy} onClick={() => run(() => api<Outreach>(`/offmarket/outreach/${o.id}`, { method: "PATCH", json: { subject, body } }), "Saved; checks re-run.")}>
            Save edits
          </button>
          {o.channel === "post" ? (
            <a className="btn" href={`/api/offmarket/outreach/${o.id}/pdf`} target="_blank" rel="noreferrer">
              Preview PDF (draft)
            </a>
          ) : null}
          <input placeholder="Instructions for a new version (optional), e.g. shorter, more formal" value={instr} onChange={(e) => setInstr(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
          <button className="btn" disabled={busy} onClick={() => run(() => post("/regenerate", { instructions: instr || null }), "New version generated.")}>
            Regenerate
          </button>
          <button className="btn danger" disabled={busy} onClick={() => run(() => post("/reject", { instructions: window.prompt("Reason for rejecting (optional)") || null }), "Rejected.")}>
            Reject
          </button>
        </div>
      ) : null}

      {o.status === "needs_compliance_review" ? (
        <div className="card flat stack" style={{ borderColor: "#eedcb4" }}>
          <b>Manual compliance review</b>
          <div className="small">{o.review_reason}</div>
          <textarea placeholder="Record why contacting this recipient is appropriate (e.g. you checked the probate position, timing is suitable)…" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn" disabled={note.trim().length < 15 || busy} onClick={() => run(() => post("/clear-review", { note }), "Review recorded; checks re-run.")}>
            Record review and re-check
          </button>
        </div>
      ) : null}

      {o.status === "needs_changes" ? <div className="notice bad small">Cannot be approved yet: {o.review_reason}</div> : null}

      {o.status === "awaiting_approval" && !dirty ? (
        <div className="card flat stack" style={{ borderColor: "var(--accent)" }}>
          <b>Approve this {what}</b>
          <label className="checkline">
            <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
            <span>
              I have read this exact {what} and confirm it should go to <b>{o.recipient_name}</b>
              {o.channel === "email" ? ` at ${o.recipient_email}` : `, ${o.recipient_address?.replace(/\n/g, ", ")}`}. Approval covers only this content and this recipient.
            </span>
          </label>
          {hasPrice ? (
            <label className="checkline">
              <input type="checkbox" checked={priceOk} onChange={(e) => setPriceOk(e.target.checked)} /> <span>I approve including the figure in this message.</span>
            </label>
          ) : null}
          <button className="btn primary" disabled={!confirm || (hasPrice && !priceOk) || busy} onClick={() => run(() => post("/approve", { confirm_recipient_hash: o.recipient_hash, confirm_price: priceOk }), o.channel === "post" ? "Approved. Download the printable letter below." : "Approved. You can now send it.")}>
            Approve this {what}
          </button>
        </div>
      ) : null}

      {o.status === "approved" && !dirty ? (
        <div className="card flat stack" style={{ borderColor: "var(--good)" }}>
          <b>Approved by {o.approved_by} · {when(o.approved_at)}</b>
          {o.channel === "post" ? (
            <>
              <div className="row">
                <a className="btn primary" href={`/api/offmarket/outreach/${o.id}/pdf?final=1`} target="_blank" rel="noreferrer">
                  Download printable letter (PDF)
                </a>
                <span className="small">A4, for a DL window envelope. Postage estimate £{(o.postage_estimate_gbp ?? postal?.estimate_gbp ?? 0.87).toFixed(2)} — check the current Royal Mail price.</span>
              </div>
              <div className="row" style={{ alignItems: "flex-end" }}>
                <label className="f">
                  Posted on
                  <input type="date" value={postedOn} onChange={(e) => setPostedOn(e.target.value)} />
                </label>
                <label className="f">
                  Postage paid (£)
                  <input type="number" step="0.01" value={postage} onChange={(e) => setPostage(Number(e.target.value))} style={{ width: 100 }} />
                </label>
                <button className="btn" disabled={busy} onClick={() => run(() => post("/mark-posted", { posted_on: postedOn, postage_paid_gbp: postage }), "Recorded as posted by you.")}>
                  I have posted it
                </button>
              </div>
              {postal?.provider === "stannp" ? (
                <div className="row">
                  <button className="btn" disabled={busy} onClick={() => window.confirm(`Submit to Stannp${postal.test_mode ? " in TEST mode (proof only, nothing is printed or posted)" : " — this will print and post the letter"} at about £${postage.toFixed(2)}?`) && run(() => post("/stannp", { confirm_cost_gbp: postage }), postal.test_mode ? "Stannp test submission recorded (nothing posted)." : "Submitted to Stannp.")}>
                    Submit to Stannp{postal.test_mode ? " (test mode)" : ""}
                  </button>
                  {o.delivery_detail?.stannp_test ? <span className="small">Last test: {JSON.stringify(o.delivery_detail.stannp_test)}</span> : null}
                </div>
              ) : null}
            </>
          ) : mailbox ? (
            <div className="row">
              <button className="btn" disabled={busy} onClick={() => run(() => post("/mailbox-draft"), `Copied to your ${mailbox.provider === "google" ? "Gmail" : "Outlook"} drafts (not sent).`)}>
                Copy to my mailbox drafts
              </button>
              <button className="btn primary" disabled={busy} onClick={() => window.confirm(`Send this approved email to ${o.recipient_email} from ${mailbox.email}?`) && run(() => post("/send"), "Sent — accepted by your mail provider.")}>
                Send now from {mailbox.email}
              </button>
            </div>
          ) : (
            <div className="notice small">Connect Gmail or Outlook in Admin → Mail to send this email.</div>
          )}
        </div>
      ) : null}

      {o.status === "delivery_unknown" ? (
        <div className="card flat stack" style={{ borderColor: "var(--bad)" }}>
          <b>Delivery unconfirmed</b>
          <div className="small">{String(o.delivery_detail?.error ?? "")}. PLOT will not resend automatically.</div>
          <div className="row">
            <button className="btn" disabled={busy} onClick={() => run(() => post("/reconcile"), "Mailbox checked.")}>
              Check mailbox for this message
            </button>
          </div>
          <textarea placeholder="If you checked your Sent folder / provider dashboard and it did not go, say how you confirmed that…" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn danger" disabled={note.trim().length < 10 || busy} onClick={() => run(() => post("/release", { note }), "Released: it needs approving again before any new attempt.")}>
            I confirmed it was not sent
          </button>
        </div>
      ) : null}

      {["sent", "posted"].includes(o.status) ? (
        <div className="notice small">
          {o.status === "sent" ? `Accepted by ${o.provider} on ${when(o.sent_at)} · message id ${o.provider_message_id}` : `Posted ${when(o.sent_at, false)} (${o.provider === "manual_post" ? "self-declared by you; Royal Mail delivery is not tracked" : `via ${o.provider}, id ${o.provider_message_id}`})`}
          {o.delivery_status === "bounced" ? " · BOUNCED" : ""}
          {o.channel === "post" ? (
            <>
              {" "}
              ·{" "}
              <a href={`/api/offmarket/outreach/${o.id}/pdf?final=1`} target="_blank" rel="noreferrer">
                letter as sent (PDF)
              </a>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
