"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ConfChip, MessagePanel, StatusChip } from "@/components/offmarket";
import { ParcelPlan, PlanLegend } from "@/components/ParcelPlan";
import { Shell } from "@/components/Shell";
import { Chip, Empty, ErrorBox, Loading, ScoreBadge } from "@/components/ui";
import { api, area, gbp, label, useApi, when } from "@/lib/api";
import { ACCESS_LABEL, COMP_COLOR, COMP_LABEL, COMP_ORDER, CONTACT_METHOD_LABEL, type Contact, DECLINE_LABEL, type Inbound, type LeadDetail, type Outreach, OWNERSHIP_LABEL, REPLY_CATEGORY, STAGE_LABEL, type TitleDoc } from "@/lib/offmarket";

const HMLR_SEARCH = "https://search-property-information.service.gov.uk/";

function Entries({ title, items }: { title: string; items?: { n: number; date: string | null; text: string }[] }) {
  if (!items?.length) return null;
  return (
    <div style={{ marginTop: 6 }}>
      <div className="small muted">{title}</div>
      {items.map((e) => (
        <div key={`${title}${e.n}`} className="small" style={{ borderLeft: "2px solid var(--line)", paddingLeft: 8, margin: "3px 0" }}>
          <span className="mono">{e.n}</span> {e.date ? <span className="muted">({e.date})</span> : null} “{e.text}”
        </div>
      ))}
    </div>
  );
}

function TitleCard({ d, onDone }: { d: TitleDoc; onDone: () => void }) {
  const ex = d.extracted;
  const [matches, setMatches] = useState(false);
  const [checked, setChecked] = useState(false);
  const [name, setName] = useState((ex.proprietors ?? []).map((p) => p.name).join(" and "));
  const [salutation, setSalutation] = useState<string | null>(null);
  const [lines, setLines] = useState(((ex.proprietor_address ?? "").split(",").map((x) => x.trim()).filter(Boolean)).join("\n"));
  const [msg, setMsg] = useState<string | null>(null);
  async function verify() {
    setMsg(null);
    try {
      const addr = lines.split("\n").map((x) => x.trim()).filter(Boolean);
      const pc = addr.length && /[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$/i.test(addr[addr.length - 1]) ? addr[addr.length - 1].match(/[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$/i)![0] : null;
      const lastClean = pc ? addr[addr.length - 1].replace(pc, "").trim() : null;
      const addressLines = pc ? [...addr.slice(0, -1), ...(lastClean ? [lastClean] : [])] : addr;
      await api(`/offmarket/titles/${d.id}/verify`, { method: "POST", json: { confirm_matches_land: matches, confirm_details_checked: checked, name, salutation, address_lines: addressLines, postcode: pc?.toUpperCase() } });
      onDone();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  return (
    <div className="card flat stack" style={{ background: "var(--surface-2)" }}>
      <div className="row between">
        <b>
          {d.title_number ?? "Title ?"} · {label(d.tenure)}
        </b>
        <span className="row" style={{ gap: 6 }}>
          <Chip tone={d.status === "verified" ? "good" : d.status === "rejected" ? "bad" : "info"}>{label(d.status)}</Chip>
          <a className="small" href={`/api/offmarket/titles/${d.id}/file`} target="_blank" rel="noreferrer">
            open document
          </a>
        </span>
      </div>
      <dl className="dl small stacked">
        <dt>Property</dt>
        <dd>{ex.property_address ?? ex.property_description ?? "—"}</dd>
        <dt>Registered proprietor(s)</dt>
        <dd>
          {(ex.proprietors ?? []).map((p) => `${p.name}${p.company_number ? ` (Co. ${p.company_number})` : ""}`).join("; ") || "—"}
          {ex.proprietor_entry ? <div className="muted">Register: “{ex.proprietor_entry}”</div> : null}
        </dd>
        <dt>Class / edition</dt>
        <dd>
          {ex.class_of_title ?? "—"} · edition {ex.edition_date ?? "—"} · copy dated {ex.official_copy_date ?? "—"}
        </dd>
        <dt>Price paid</dt>
        <dd>{ex.price_paid ? `${gbp(ex.price_paid.amount)}${ex.price_paid.date ? ` on ${ex.price_paid.date}` : ""}` : "—"}</dd>
        <dt>Mortgage</dt>
        <dd>{ex.charges?.length ? ex.charges.map((c) => c.lender ?? "registered charge").join("; ") + " — lender consent needed to release part" : "None registered"}</dd>
      </dl>
      <Entries title="Restrictions" items={ex.restrictions} />
      <Entries title="Covenants" items={ex.covenants} />
      <Entries title="Rights / easements" items={ex.easements} />
      <Entries title="Notices / options" items={ex.notices} />
      {(ex.warnings ?? []).map((w) => (
        <div key={w} className="notice warn small">
          {w}
        </div>
      ))}
      {d.status === "parsed" || d.status === "uploaded" ? (
        <div className="stack" style={{ borderTop: "1px dashed var(--line)", paddingTop: 10 }}>
          <b>Verify the owner</b>
          <div className="small">Check these against the official copy itself. Correct anything the parser misread; enter names and the address exactly as registered.</div>
          <label className="f">
            Proprietor name(s)
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="f">
            Greeting (first names only; never an assumed Mr/Mrs/Ms) — leave blank to use the default
            <input placeholder="e.g. Alex and Sam" value={salutation ?? ""} onChange={(e) => setSalutation(e.target.value || null)} />
          </label>
          <label className="f">
            Postal address (one line each, postcode last)
            <textarea value={lines} onChange={(e) => setLines(e.target.value)} style={{ minHeight: 80 }} />
          </label>
          <label className="checkline">
            <input type="checkbox" checked={matches} onChange={(e) => setMatches(e.target.checked)} /> <span>The title plan covers the land in question</span>
          </label>
          <label className="checkline">
            <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} /> <span>I have checked the owner details against the official copy</span>
          </label>
          {msg ? <div className="notice bad small">{msg}</div> : null}
          <div className="row">
            <button className="btn primary" disabled={!matches || !checked} onClick={verify}>
              Verify owner
            </button>
            <button
              className="btn danger"
              onClick={async () => {
                const r = window.prompt("Why is this document not usable?");
                if (r) {
                  await api(`/offmarket/titles/${d.id}/reject`, { method: "POST", json: { reason: r } });
                  onDone();
                }
              }}
            >
              Reject document
            </button>
          </div>
        </div>
      ) : d.verified_by ? (
        <div className="small">
          Verified by {d.verified_by} · {when(d.verified_at)}
        </div>
      ) : null}
    </div>
  );
}

function ContactCard({ c, onDone }: { c: Contact; onDone: () => void }) {
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ salutation: c.salutation ?? "", kind: c.kind, email: c.email ?? "", email_basis: c.email_basis ?? "none", basis_note: "" });
  const [msg, setMsg] = useState<string | null>(null);
  async function save() {
    setMsg(null);
    try {
      await api(`/offmarket/contacts/${c.id}`, { method: "PATCH", json: { salutation: f.salutation, kind: f.kind, email: f.email || null, email_basis: f.email_basis, basis_note: f.basis_note || null } });
      setEdit(false);
      onDone();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  async function dnc() {
    const reason = window.prompt("Add to the permanent do-not-contact list. Reason:");
    if (!reason) return;
    await api("/offmarket/suppressions", { method: "POST", json: { name: c.name, address_lines: c.address_lines, postcode: c.postcode, email: c.email, reason } });
    onDone();
  }
  return (
    <div className="stack">
      <div className="row between">
        <b>{c.name}</b>
        <Chip tone={c.verified ? "good" : "unknown"}>{c.verified ? "verified" : "not verified"}</Chip>
      </div>
      <div className="small" style={{ whiteSpace: "pre-line" }}>
        {[...(c.address_lines ?? []), c.postcode].filter(Boolean).join("\n")}
      </div>
      <dl className="dl small">
        <dt>Type</dt>
        <dd>{label(c.kind)}</dd>
        <dt>Greeting</dt>
        <dd>{c.salutation || "—"}</dd>
        <dt>Email</dt>
        <dd>{c.email ? `${c.email} (${c.email_basis ? label(c.email_basis) : "no basis — letters only"})` : "—"}</dd>
        <dt>Source</dt>
        <dd>
          {label(c.source)} {c.source_ref ? <span className="muted">· {c.source_ref}</span> : null}
        </dd>
        <dt>Keep data until</dt>
        <dd>{c.retention_until ?? "—"}</dd>
      </dl>
      {c.notes ? <div className="small muted">{c.notes}</div> : null}
      {edit ? (
        <div className="stack">
          <label className="f">
            Greeting
            <input value={f.salutation} onChange={(e) => setF({ ...f, salutation: e.target.value })} />
          </label>
          <label className="f">
            Recipient type
            <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              {["individual", "company", "public_body", "charity", "trustees", "personal_representatives", "partnership", "unknown"].map((k) => (
                <option key={k} value={k}>
                  {label(k)}
                </option>
              ))}
            </select>
          </label>
          <label className="f">
            Email address
            <input value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
          </label>
          <label className="f">
            Basis for emailing
            <select value={f.email_basis} onChange={(e) => setF({ ...f, email_basis: e.target.value })}>
              <option value="none">None — letters only</option>
              <option value="owner_initiated">The owner emailed me first</option>
              <option value="consent_recorded">The owner gave consent (record how)</option>
              <option value="corporate_subscriber">Company / public body address (corporate subscriber)</option>
            </select>
          </label>
          {["owner_initiated", "consent_recorded"].includes(f.email_basis) ? (
            <label className="f">
              How and when (kept as evidence)
              <input value={f.basis_note} onChange={(e) => setF({ ...f, basis_note: e.target.value })} />
            </label>
          ) : null}
          <div className="small">A published or found email address is never permission to send marketing to an individual.</div>
          {msg ? <div className="notice bad small">{msg}</div> : null}
          <div className="row">
            <button className="btn primary sm" onClick={save}>
              Save (re-checks any drafts)
            </button>
            <button className="btn sm" onClick={() => setEdit(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="row">
          <button className="btn sm" onClick={() => setEdit(true)}>
            Edit
          </button>
          <button className="btn sm danger" onClick={dnc}>
            Do not contact
          </button>
        </div>
      )}
    </div>
  );
}

function InboundCard({ m, onDone, onOpen }: { m: Inbound; onDone: () => void; onOpen: (id: number) => void }) {
  const [cat, tone] = REPLY_CATEGORY[m.category] ?? [label(m.category), ""];
  async function patch(json: Record<string, unknown>) {
    try {
      await api(`/offmarket/replies/${m.id}`, { method: "PATCH", json });
      onDone();
    } catch (e) {
      window.alert((e as Error).message);
    }
  }
  return (
    <div className="card flat stack" style={{ borderLeft: `4px solid ${m.category === "requests_no_contact" ? "var(--bad)" : "var(--good)"}` }}>
      <div className="row between">
        <span className="row" style={{ gap: 6 }}>
          <Chip tone={tone}>{cat}</Chip>
          <span className="small">
            {label(m.channel)} · {when(m.received_at, false)} · {m.category_source} {Math.round(m.category_confidence * 100)}%
          </span>
        </span>
        {m.handled ? <Chip>handled</Chip> : <Chip tone="warn">needs attention</Chip>}
      </div>
      <div style={{ whiteSpace: "pre-line", fontFamily: "var(--serif)" }}>{m.body}</div>
      {m.evidence ? <div className="small muted">Why: {m.evidence}</div> : null}
      {m.category === "requests_no_contact" ? <div className="notice bad small">Opt-out respected: added to the do-not-contact list, all pending drafts withdrawn, no reply drafted.</div> : null}
      <div className="row small">
        <select value={m.category} onChange={(e) => patch({ category: e.target.value })} disabled={m.category === "requests_no_contact"}>
          {Object.entries(REPLY_CATEGORY).map(([k, [t]]) => (
            <option key={k} value={k}>
              {t}
            </option>
          ))}
        </select>
        {["not_interested", "requests_no_contact"].includes(m.category) ? (
          <select value={m.decline_reason ?? ""} onChange={(e) => patch({ decline_reason: e.target.value })}>
            <option value="">Decline reason…</option>
            {Object.entries(DECLINE_LABEL).map(([k, t]) => (
              <option key={k} value={k}>
                {t}
              </option>
            ))}
          </select>
        ) : null}
        {m.suggested_reply_id ? (
          <button className="btn sm" onClick={() => onOpen(m.suggested_reply_id!)}>
            Open suggested reply
          </button>
        ) : m.category !== "requests_no_contact" && m.category !== "not_interested" ? (
          <button className="btn sm" onClick={async () => { await api(`/offmarket/replies/${m.id}/draft`, { method: "POST" }); onDone(); }}>
            Draft a reply
          </button>
        ) : null}
        {!m.handled ? (
          <button className="btn sm" onClick={() => patch({ handled: true })}>
            Mark handled
          </button>
        ) : null}
      </div>
    </div>
  );
}

export default function LeadPage() {
  const { id } = useParams<{ id: string }>();
  const { data: d, error, reload } = useApi<LeadDetail>(`/offmarket/leads/${id}`, [id]);
  const [sel, setSel] = useState<Outreach | null>(null);
  const [draftOpts, setDraftOpts] = useState({ channel: "", kind: "initial", instructions: "" });
  const [reply, setReply] = useState({ channel: "post", received_on: new Date().toISOString().slice(0, 10), body: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [upload, setUpload] = useState<File | null>(null);

  useEffect(() => {
    if (!d) return;
    if (sel) {
      const fresh = d.outreach.find((o) => o.id === sel.id);
      if (fresh) setSel(fresh);
    } else {
      const open = d.outreach.find((o) => ["awaiting_approval", "approved", "needs_changes", "needs_compliance_review", "delivery_unknown"].includes(o.status));
      if (open) setSel(open);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d]);

  async function run(fn: () => Promise<unknown>, done: string) {
    setMsg(null);
    try {
      const r = await fn();
      setMsg(done);
      reload();
      return r;
    } catch (e) {
      setMsg(`Not done: ${(e as Error).message}`);
    }
  }
  async function openOutreach(oid: number) {
    setSel(await api<Outreach>(`/offmarket/outreach/${oid}`));
    window.scrollTo({ top: document.getElementById("message")?.offsetTop ?? 0, behavior: "smooth" });
  }

  if (error) return <Shell title="Prospect"><ErrorBox error={error} /></Shell>;
  if (!d) return <Shell title="Prospect"><Loading /></Shell>;
  const m = d.metrics ?? {};
  const own = OWNERSHIP_LABEL[d.ownership_status] ?? [label(d.ownership_status), ""];
  const comps = d.acq_score?.components ?? {};
  const verified = d.contacts.find((c) => c.verified);
  const nextManual = { interested: "feasibility_discussion", feasibility_discussion: "negotiation", negotiation: "under_offer", under_offer: "completed" }[d.stage];

  return (
    <Shell title={`Off-Market CRM / ${d.site?.headline ?? `#${d.id}`}`}>
      <div className="page-head">
        <div>
          <div className="row" style={{ gap: 6, marginBottom: 6 }}>
            <Chip tone="info">{d.type_label}</Chip>
            <Chip>{STAGE_LABEL[d.stage]}</Chip>
            <Chip tone={own[1]}>{own[0]}</Chip>
            <ConfChip c={d.confidence} />
          </div>
          <h1>{d.site?.headline ?? `Prospect #${d.id}`}</h1>
          <p>
            <b>Next:</b> {d.next_action}
          </p>
        </div>
        <div className="row">
          <ScoreBadge score={d.score} fatal={d.fatal} size="lg" />
          <Link className="btn" href={`/sites/${d.site_id}`}>
            Site dossier
          </Link>
          {nextManual ? (
            <button className="btn" onClick={() => run(() => api(`/offmarket/leads/${d.id}`, { method: "PATCH", json: { stage: nextManual } }), `Moved to ${STAGE_LABEL[nextManual]}.`)}>
              Move to {STAGE_LABEL[nextManual]}
            </button>
          ) : null}
          {d.stage !== "closed" ? (
            <button
              className="btn danger"
              onClick={() => {
                const r = window.prompt("Close this prospect — reason:");
                if (r) run(() => api(`/offmarket/leads/${d.id}`, { method: "PATCH", json: { stage: "closed", closed_reason: r } }), "Closed.");
              }}
            >
              Close
            </button>
          ) : null}
        </div>
      </div>
      {msg ? <div className={`notice ${msg.startsWith("Not") ? "bad" : "info"}`} style={{ marginBottom: 12 }}>{msg}</div> : null}
      {!d.buyer_confirmed ? (
        <div className="notice warn" style={{ marginBottom: 12 }}>
          Your buyer profile is not confirmed, so no draft can be approved. <Link href="/settings#offmarket">Confirm it in Preferences → Off-market</Link>.
        </div>
      ) : null}

      <div className="grid g-main">
        <div className="stack">
          <div className="card">
            <div className="card-head">
              <h3>The opportunity</h3>
              <span className="small">{d.area}</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 18 }}>
              <div>
                <ParcelPlan g={d.geometry} size={320} labels plotArea={m.plot_area_m2 as number} />
                <PlanLegend />
              </div>
              <dl className="dl small" style={{ alignContent: "start" }}>
                <dt>Indicative new plot</dt>
                <dd>
                  {area(m.plot_area_m2 as number)}
                  {m.plot_width_m ? ` (${m.plot_width_m} × ${m.plot_depth_m} m)` : ""}
                </dd>
                <dt>Registered title extent</dt>
                <dd>
                  {area(m.parcel_area_m2 as number)} · INSPIRE {d.title_id ?? "—"}
                </dd>
                <dt>Access</dt>
                <dd>
                  {ACCESS_LABEL[d.access?.type ?? "unknown"]}
                  {d.access?.frontage_m ? ` · ${d.access.frontage_m} m frontage` : ""}
                  {d.access?.road_name ? ` to ${d.access.road_name}` : ""} {d.access?.road_class ? `(${d.access.road_class})` : ""}
                  {d.access?.corner ? " · corner plot" : ""}
                  {d.access?.strip_length_m ? ` · ${d.access.strip_length_m} m drive` : ""}
                </dd>
                {m.retained_garden_m2 ? (
                  <>
                    <dt>Owner would keep</dt>
                    <dd>
                      ~{area(m.retained_garden_m2 as number)} of garden ({Math.round((m.retained_garden_ratio as number) * 100)}%)
                    </dd>
                    <dt>Separation</dt>
                    <dd>~{m.separation_to_house_m} m from the existing house</dd>
                  </>
                ) : null}
                {m.neighbour_median_plot_m2 ? (
                  <>
                    <dt>Neighbouring plots</dt>
                    <dd>
                      median {area(m.neighbour_median_plot_m2 as number)}; this title {m.parcel_vs_neighbours}× · new plot {m.plot_vs_neighbours}×
                    </dd>
                  </>
                ) : null}
                <dt>Built-up area</dt>
                <dd>{m.in_built_up_area ? "Inside" : `Outside (${m.distance_to_built_up_m ?? "?"} m away)`}</dd>
                <dt>Constraints on the plot</dt>
                <dd>{d.constraints.length ? d.constraints.map((c) => `${label(c.dataset)}${c.name ? ` (${c.name})` : ""}`).join("; ") : "None found in the layers checked"}</dd>
                <dt>Signals</dt>
                <dd>{((d.analysis?.signals as string[]) ?? []).map(label).join(", ") || "—"}</dd>
              </dl>
            </div>
            <div className="disclaimer">
              {(d.analysis?.caveats ?? []).map((c) => (
                <div key={c}>• {c}</div>
              ))}
              {d.analysis?.sources ? <div>Sources: {Object.values(d.analysis.sources).join(" · ")}</div> : null}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Off-Market Acquisition Score</h3>
              <span className="small">{d.acq_score?.confidence?.cap_reason ? `Confidence capped: ${d.acq_score.confidence.cap_reason}` : ""}</span>
            </div>
            {COMP_ORDER.map((k) =>
              comps[k] ? (
                <div key={k} style={{ marginBottom: 6 }}>
                  <div className="compbar" style={{ gridTemplateColumns: "250px 1fr 54px" }}>
                    <span className="ink2">{COMP_LABEL[k]}</span>
                    <div className="bar">
                      <span style={{ width: `${(comps[k].score / comps[k].max) * 100}%`, background: COMP_COLOR[k] }} />
                    </div>
                    <span className="num mono" style={{ textAlign: "right" }}>
                      {comps[k].score}/{comps[k].max}
                    </span>
                  </div>
                  {(comps[k].notes ?? []).length ? <div className="small" style={{ marginLeft: 4 }}>{comps[k].notes!.join(" · ")}</div> : null}
                </div>
              ) : null,
            )}
            {d.flags.length ? (
              <div className="pill-row" style={{ marginTop: 8 }}>
                {d.flags.map((f) => (
                  <Chip key={f.code} tone={f.severity === "fatal" ? "fatal" : f.severity === "major" ? "bad" : "warn"}>
                    ⚑ {f.title}
                  </Chip>
                ))}
              </div>
            ) : null}
          </div>

          <div className="grid g2">
            <div className="card">
              <h3>Feasibility</h3>
              {d.assessment.planning ? (
                <dl className="dl small">
                  <dt>Planning screen</dt>
                  <dd>{d.assessment.planning.classification_label}</dd>
                  <dt>Maximum viable land price</dt>
                  <dd>{gbp(d.assessment.residual?.max_recommended_offer)}</dd>
                  <dt>Prudent figure</dt>
                  <dd>{gbp(d.assessment.residual?.prudent_offer)}</dd>
                  <dt>House concept fits</dt>
                  <dd>{d.assessment.fit?.fits === true ? "Yes" : d.assessment.fit?.fits === false ? "No" : "Unknown"}</dd>
                </dl>
              ) : (
                <Empty>Not yet assessed.</Empty>
              )}
              {d.assessment.fatal_flags.map((f) => (
                <Chip key={f.code} tone="fatal">
                  ⚑ {f.title}
                </Chip>
              ))}
              <div className="small" style={{ marginTop: 6 }}>
                Values are private, against your all-in budget. Planning permission is never guaranteed.
              </div>
            </div>
            <div className="card">
              <h3>Why an owner might consider it</h3>
              <ul style={{ margin: 0, paddingLeft: 18 }} className="small ink2">
                {d.benefits.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
              <div className="small" style={{ marginTop: 6 }}>
                Possibilities only — nothing here assumes anything about the owner&apos;s circumstances or motives.
              </div>
            </div>
          </div>
        </div>

        <div className="stack">
          <div className="card stack">
            <div className="card-head">
              <h3>Ownership</h3>
              <Chip tone={own[1]}>{own[0]}</Chip>
            </div>
            {d.ownership_status !== "verified" ? (
              <div className="small ink2">
                Ownership is never assumed from a map or because land adjoins a house. Buy the official copy of the register (about £7) from{" "}
                <a href={HMLR_SEARCH} target="_blank" rel="noreferrer">
                  HM Land Registry
                </a>
                {d.title_id ? (
                  <>
                    {" "}
                    using INSPIRE ID <b className="mono">{d.title_id}</b> on the map search
                  </>
                ) : null}
                , then upload the PDF here.
              </div>
            ) : null}
            <div className="row">
              <input type="file" accept="application/pdf,text/plain" onChange={(e) => setUpload(e.target.files?.[0] ?? null)} />
              <button
                className="btn sm"
                disabled={!upload}
                onClick={() =>
                  run(async () => {
                    const fd = new FormData();
                    fd.append("file", upload!);
                    const res = await fetch(`/api/offmarket/leads/${d.id}/titles`, { method: "POST", body: fd, headers: { "X-Requested-With": "plot" }, credentials: "same-origin" });
                    if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
                  }, "Register uploaded and parsed — check and verify it below.")
                }
              >
                Upload official copy
              </button>
            </div>
            {d.titles.map((t) => (
              <TitleCard key={t.id} d={t} onDone={reload} />
            ))}
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Recipient</h3>
              <span className="small">{CONTACT_METHOD_LABEL[d.contact_method ?? ""]}</span>
            </div>
            {verified ? <ContactCard c={verified} onDone={reload} /> : <Empty>No verified owner yet.</Empty>}
          </div>

          {d.brief ? (
            <div className="card">
              <h3>Negotiation brief (private)</h3>
              {d.brief.available ? (
                <div className="small stack">
                  <dl className="dl">
                    <dt>Walk-away (max viable)</dt>
                    <dd>{gbp(d.brief.walk_away_gbp)}</dd>
                    <dt>Prudent figure</dt>
                    <dd>{gbp(d.brief.prudent_offer_gbp)}</dd>
                    <dt>Opening range</dt>
                    <dd>{d.brief.suggested_opening_range_gbp ? `${gbp(d.brief.suggested_opening_range_gbp[0])} – ${gbp(d.brief.suggested_opening_range_gbp[1])}` : "—"}</dd>
                    <dt>Comparables</dt>
                    <dd>{d.brief.comparables?.available ? `median ${gbp(d.brief.comparables.comparables_median)} (n=${d.brief.comparables.n})` : d.brief.comparables?.note}</dd>
                  </dl>
                  <div>{d.brief.basis}</div>
                  {d.brief.biggest_risks?.length ? <div>Biggest risks: {d.brief.biggest_risks.map((r) => `${r.factor} (−${gbp(r.reduces_value_by_gbp, { compact: true })})`).join("; ")}</div> : null}
                  {d.brief.unknowns_to_resolve?.length ? <div>Unknowns: {d.brief.unknowns_to_resolve.join("; ")}</div> : null}
                  <div>
                    <b>Structures:</b>
                    <ul style={{ margin: "2px 0", paddingLeft: 18 }}>
                      {d.brief.structures?.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <b>Questions for the owner:</b>
                    <ul style={{ margin: "2px 0", paddingLeft: 18 }}>
                      {d.brief.questions_for_owner?.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="notice warn">{d.brief.rules}</div>
                </div>
              ) : (
                <div className="small">{d.brief.note}</div>
              )}
            </div>
          ) : null}
        </div>
      </div>

      <div className="grid g-main" style={{ marginTop: 16 }}>
        <div className="card" id="message">
          <div className="card-head">
            <h3>{sel ? `Message ${sel.reference ?? `#${sel.id}`}` : "Draft a personalised approach"}</h3>
            {sel ? (
              <button className="btn sm" onClick={() => setSel(null)}>
                New draft…
              </button>
            ) : null}
          </div>
          {sel ? (
            <MessagePanel
              o={sel}
              mailbox={d.mailbox}
              postal={d.postal}
              onChange={(o) => {
                setSel(o);
                reload();
              }}
            />
          ) : verified && d.stage !== "closed" ? (
            <div className="stack">
              <div className="small">PLOT writes from verified facts only, in your chosen persona. It will be checked against the writing, honesty and UK GDPR/PECR rules, then wait for your approval.</div>
              <div className="row">
                <select value={draftOpts.channel} onChange={(e) => setDraftOpts({ ...draftOpts, channel: e.target.value })}>
                  <option value="">Channel: recommended ({d.contact_method === "email_or_post" ? "email" : "letter"})</option>
                  <option value="post">Letter</option>
                  <option value="email">Email</option>
                </select>
                <select value={draftOpts.kind} onChange={(e) => setDraftOpts({ ...draftOpts, kind: e.target.value })}>
                  <option value="initial">First approach</option>
                  <option value="follow_up">One polite follow-up</option>
                </select>
              </div>
              <input placeholder="Anything to emphasise or avoid? (optional)" value={draftOpts.instructions} onChange={(e) => setDraftOpts({ ...draftOpts, instructions: e.target.value })} />
              <button
                className="btn primary"
                onClick={async () => {
                  const o = (await run(() => api<Outreach>(`/offmarket/leads/${d.id}/drafts`, { method: "POST", json: { channel: draftOpts.channel || null, kind: draftOpts.kind, instructions: draftOpts.instructions || null } }), "Draft prepared.")) as Outreach | undefined;
                  if (o) setSel(o);
                }}
              >
                Generate draft
              </button>
            </div>
          ) : (
            <Empty>{d.stage === "closed" ? "This prospect is closed." : "Verify the owner from an official copy before drafting."}</Empty>
          )}
        </div>

        <div className="stack">
          <div className="card">
            <h3>Correspondence</h3>
            {d.outreach.length || d.inbound.length ? (
              <ul className="list-plain small">
                {[...d.outreach.map((o) => ({ t: o.sent_at ?? o.created_at, o })), ...d.inbound.map((i) => ({ t: i.received_at, i }))]
                  .sort((a, b) => b.t.localeCompare(a.t))
                  .map((x) =>
                    "o" in x && x.o ? (
                      <li key={`o${x.o.id}`} className="row between" style={{ cursor: "pointer" }} onClick={() => openOutreach(x.o!.id)}>
                        <span>
                          ↗ {x.o.channel === "post" ? "Letter" : "Email"} · {label(x.o.kind)} <span className="mono">{x.o.reference}</span>
                        </span>
                        <StatusChip s={x.o.status} />
                      </li>
                    ) : "i" in x && x.i ? (
                      <li key={`i${x.i.id}`} className="row between">
                        <span>
                          ↙ Reply ({label(x.i.channel)}) {when(x.i.received_at, false)}
                        </span>
                        <Chip tone={(REPLY_CATEGORY[x.i.category] ?? ["", ""])[1]}>{(REPLY_CATEGORY[x.i.category] ?? [x.i.category])[0]}</Chip>
                      </li>
                    ) : null,
                  )}
              </ul>
            ) : (
              <Empty>No correspondence yet.</Empty>
            )}
          </div>
          {d.inbound.map((m2) => (
            <InboundCard key={m2.id} m={m2} onDone={reload} onOpen={openOutreach} />
          ))}
          {["contacted", "awaiting_response", "interested", "feasibility_discussion", "negotiation", "under_offer"].includes(d.stage) ? (
            <div className="card stack">
              <h3>Log a reply (letter, call or meeting)</h3>
              <div className="row">
                <select value={reply.channel} onChange={(e) => setReply({ ...reply, channel: e.target.value })}>
                  <option value="post">Letter</option>
                  <option value="phone_note">Phone call (your note)</option>
                  <option value="in_person">In person (your note)</option>
                  <option value="email">Email (pasted)</option>
                </select>
                <input type="date" value={reply.received_on} onChange={(e) => setReply({ ...reply, received_on: e.target.value })} />
              </div>
              <textarea placeholder="What the owner said…" value={reply.body} onChange={(e) => setReply({ ...reply, body: e.target.value })} />
              <button className="btn" disabled={reply.body.trim().length < 2} onClick={() =>
                  run(() => api<Inbound>(`/offmarket/leads/${d.id}/replies`, { method: "POST", json: reply }), "Reply recorded and categorised.").then((r) => {
                    setReply({ ...reply, body: "" });
                    const sid = (r as Inbound | undefined)?.suggested_reply_id;
                    if (sid) openOutreach(sid);
                  })
                }>
                Record reply
              </button>
              <div className="small">A request for no further contact is honoured immediately: do-not-contact entry, drafts withdrawn, lead closed.</div>
            </div>
          ) : null}
          <div className="card">
            <h3>History</h3>
            <ul className="list-plain small">
              {d.events.map((e, i) => (
                <li key={i}>
                  <span className="muted">{when(e.at)}</span> · {e.from !== e.to ? <b>{STAGE_LABEL[e.to] ?? e.to}</b> : null} {e.note} <span className="muted">— {e.actor}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </Shell>
  );
}
