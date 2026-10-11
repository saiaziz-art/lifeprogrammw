"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useState } from "react";
import { Shell } from "@/components/Shell";
import { Empty } from "@/components/ui";
import { api, gbp, useApi, when } from "@/lib/api";

const MapView = dynamic(() => import("@/components/MapView").then((m) => m.MapView), { ssr: false });

type InboxItem = { id: number; source_id: string; url: string | null; title: string | null; asking_price: number | null; text: string | null; reason: string; received_at: string };
const EMPTY = { type: "FeatureCollection" as const, features: [] };

export default function ImportPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"listing" | "offmarket" | "eml">("listing");
  const [pin, setPin] = useState<[number, number] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [f, setF] = useState({ title: "", url: "", asking_price: "", price_qualifier: "asking", tenure: "freehold", postcode: "", address: "", area_value: "", area_unit: "acres", description: "", category: "plot", agent: "" });
  const [om, setOm] = useState({ title: "", category: "barn", notes: "" });
  const [files, setFiles] = useState<FileList | null>(null);
  const inbox = useApi<InboxItem[]>("/inbox");
  const [placing, setPlacing] = useState<InboxItem | null>(null);

  const onPick = useCallback((p: [number, number]) => setPin(p), []);

  async function submitListing(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      const r = await api<{ site_id: number }>("/import/listing", {
        method: "POST",
        json: {
          title: f.title,
          url: f.url || null,
          asking_price: f.asking_price ? Number(f.asking_price) : null,
          price_qualifier: f.price_qualifier,
          tenure: f.tenure,
          postcode: f.postcode || null,
          address: f.address || null,
          area_value: f.area_value ? Number(f.area_value) : null,
          area_unit: f.area_unit,
          description: f.description || null,
          category: f.category,
          agent: f.agent || null,
          lon: pin?.[0] ?? null,
          lat: pin?.[1] ?? null,
        },
      });
      router.push(`/sites/${r.site_id}`);
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  async function submitOffMarket(e: FormEvent) {
    e.preventDefault();
    if (!pin) return setMsg("Click the map to place the site first.");
    try {
      const r = await api<{ site_id: number }>("/import/off-market", { method: "POST", json: { title: om.title, category: om.category, notes: om.notes, lon: pin[0], lat: pin[1] } });
      router.push(`/sites/${r.site_id}`);
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  async function submitEml(e: FormEvent) {
    e.preventDefault();
    if (!files?.length) return;
    const fd = new FormData();
    Array.from(files).forEach((x) => fd.append("files", x));
    try {
      const r = await api<{ queued_job: number; files: number }>("/import/eml", { method: "POST", body: fd });
      setMsg(`Queued ${r.files} email(s) for parsing (job #${r.queued_job}). Located listings appear in the Deal Explorer; others land in the inbox below.`);
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  async function place(item: InboxItem) {
    if (!pin) return setMsg("Click the map to choose the location first.");
    const r = await api<{ site_id: number }>(`/inbox/${item.id}/place`, { method: "POST", json: { lon: pin[0], lat: pin[1] } });
    router.push(`/sites/${r.site_id}`);
  }

  const pinFC = pin ? { type: "FeatureCollection" as const, features: [{ type: "Feature" as const, geometry: { type: "Point", coordinates: pin }, properties: { id: 0, score: null, origin: "on_market", fatal: false, stale: false } }] } : EMPTY;

  return (
    <Shell title="Add listings & sites">
      <div className="page-head">
        <div>
          <h1>Add listings, sites & alert emails</h1>
          <p>The lawful routes for portal, agent and auction listings: paste a listing you found, forward/upload alert emails, or pin an unadvertised site you spotted. PLOT stores links and quoted facts, not copies of adverts.</p>
        </div>
      </div>
      <div className="tabs">
        <button className={mode === "listing" ? "on" : ""} onClick={() => setMode("listing")}>
          Listing you found
        </button>
        <button className={mode === "offmarket" ? "on" : ""} onClick={() => setMode("offmarket")}>
          Off-market site
        </button>
        <button className={mode === "eml" ? "on" : ""} onClick={() => setMode("eml")}>
          Upload alert emails
        </button>
      </div>
      {msg ? <div className="notice warn" style={{ marginBottom: 12 }}>{msg}</div> : null}
      <div className="grid g2">
        <div className="card">
          {mode === "listing" ? (
            <form className="stack" onSubmit={submitListing}>
              <label className="f">
                Headline *<input required minLength={3} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="e.g. Building plot with outline consent" />
              </label>
              <label className="f">
                Original listing URL<input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://…" />
              </label>
              <div className="grid g3">
                <label className="f">
                  Asking price (£)<input inputMode="numeric" value={f.asking_price} onChange={(e) => setF({ ...f, asking_price: e.target.value.replace(/[^0-9]/g, "") })} />
                </label>
                <label className="f">
                  Qualifier
                  <select value={f.price_qualifier} onChange={(e) => setF({ ...f, price_qualifier: e.target.value })}>
                    <option value="asking">Asking</option>
                    <option value="guide">Guide</option>
                    <option value="offers_over">Offers over</option>
                    <option value="auction_guide">Auction guide</option>
                  </select>
                </label>
                <label className="f">
                  Tenure
                  <select value={f.tenure} onChange={(e) => setF({ ...f, tenure: e.target.value })}>
                    <option value="freehold">Freehold</option>
                    <option value="leasehold">Leasehold</option>
                    <option value="unknown">Unknown</option>
                  </select>
                </label>
              </div>
              <div className="grid g3">
                <label className="f">
                  Site area<input inputMode="decimal" value={f.area_value} onChange={(e) => setF({ ...f, area_value: e.target.value })} />
                </label>
                <label className="f">
                  Unit
                  <select value={f.area_unit} onChange={(e) => setF({ ...f, area_unit: e.target.value })}>
                    <option value="acres">acres</option>
                    <option value="hectares">hectares</option>
                    <option value="m2">m²</option>
                    <option value="sqft">sq ft</option>
                  </select>
                </label>
                <label className="f">
                  Type
                  <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
                    <option value="plot">Building plot</option>
                    <option value="land">Land</option>
                    <option value="barn">Barn / conversion</option>
                    <option value="renovation">Renovation / replacement</option>
                    <option value="brownfield">Brownfield</option>
                    <option value="other">Other</option>
                  </select>
                </label>
              </div>
              <label className="f">
                Postcode (if no map pin)<input value={f.postcode} onChange={(e) => setF({ ...f, postcode: e.target.value })} />
              </label>
              <label className="f">
                Address / location text<input value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} />
              </label>
              <label className="f">
                Agent<input value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })} />
              </label>
              <label className="f">
                Key facts from the advert (planning, services, access…)
                <textarea value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
              </label>
              <button className="btn primary">Add & assess</button>
            </form>
          ) : null}
          {mode === "offmarket" ? (
            <form className="stack" onSubmit={submitOffMarket}>
              <div className="notice info">An off-market site is recorded as a lead at “potential site identified”. Nothing implies it is for sale.</div>
              <label className="f">
                Description *<input required minLength={3} value={om.title} onChange={(e) => setOm({ ...om, title: e.target.value })} placeholder="e.g. Redundant barn north of the lane" />
              </label>
              <label className="f">
                Type
                <select value={om.category} onChange={(e) => setOm({ ...om, category: e.target.value })}>
                  <option value="barn">Redundant agricultural building</option>
                  <option value="building">Derelict / vacant building</option>
                  <option value="land">Under-used land</option>
                  <option value="brownfield">Previously developed land</option>
                  <option value="renovation">Dwelling needing replacement</option>
                  <option value="other">Other</option>
                </select>
              </label>
              <label className="f">
                Why it looks promising
                <textarea value={om.notes} onChange={(e) => setOm({ ...om, notes: e.target.value })} />
              </label>
              <button className="btn primary" disabled={!pin}>
                Add off-market lead
              </button>
            </form>
          ) : null}
          {mode === "eml" ? (
            <form className="stack" onSubmit={submitEml}>
              <p>Save alert emails from Rightmove, Zoopla, OnTheMarket, plot directories, agents or auction houses as <code>.eml</code> files and upload them — or configure an IMAP mailbox in Admin so the daily run collects them automatically.</p>
              <input type="file" accept=".eml,message/rfc822" multiple onChange={(e) => setFiles(e.target.files)} />
              <button className="btn primary" disabled={!files?.length}>
                Upload & parse
              </button>
            </form>
          ) : null}
        </div>
        <div className="stack">
          <div className="small">{mode === "eml" ? "Pick a location for an inbox item, then press ‘Place’." : "Click the map to set the exact location (recommended for accurate constraint checks)."}</div>
          <MapView points={pinFC} boundaries={EMPTY} filters={{ minScore: 0, origins: ["on_market"], includeFatal: true, includeStale: true }} height="420px" onPick={onPick} />
          <div className="small">{pin ? `Pinned at ${pin[1].toFixed(5)}, ${pin[0].toFixed(5)}` : "No pin yet."}</div>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h3>Inbox — listings that need a location</h3>
        {inbox.data?.length ? (
          <table className="t">
            <tbody>
              {inbox.data.map((it) => (
                <tr key={it.id}>
                  <td>
                    <div>{it.title}</div>
                    <div className="small">{it.reason} · received {when(it.received_at)}</div>
                  </td>
                  <td className="num">{gbp(it.asking_price)}</td>
                  <td>{it.url ? <a href={it.url} target="_blank" rel="noreferrer noopener">original ↗</a> : null}</td>
                  <td>
                    <button className="btn sm" onClick={() => { setPlacing(it); place(it); }} disabled={!pin && placing?.id !== it.id}>
                      Place at pin
                    </button>{" "}
                    <button className="btn sm" onClick={async () => { await api(`/inbox/${it.id}/dismiss`, { method: "POST" }); inbox.reload(); }}>
                      Dismiss
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>Inbox empty.</Empty>
        )}
      </div>
    </Shell>
  );
}
