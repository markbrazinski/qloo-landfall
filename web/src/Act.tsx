import "./act.css";
import "./prepare.css";
import "./launch.css";
import { type ReactNode, useEffect, useState } from "react";
import { type DestinationState, type KitMockup, type LaunchItem, type LaunchOption, type LaunchSettings, type Package, type Segment,
  api6, api65, apiLaunch, sharedOf, useLive, useProject } from "./api";
import { historyOf } from "./ActAdapt";
import { ActivityDrawer, stagesFor, usageLine } from "./Brand";
import { capOf, outcomeOf, partnerOf } from "./Deliver";
import { ContextEdit, ContextStrip, goalLine } from "./Goal";
import { CampaignMockup, type MockData, type MockKind } from "./mockups/CampaignMockup";
import { DecisionBar, DrawerStack, Link, Notice, ProjectHeader, Skeleton, StageNav, navigate, useDrawers } from "./ui";

/** Launch (owner, 28 Oct 2026): v4's Prepare and Act & adapt as one checklist. Each item shows exactly what goes out,
 *  whether it's ready (or the one thing it needs, fixed in place), and ends in one sign-off that approves and runs it.
 *  Everything is prepared in the background (POST …/launch/prewarm); nothing leaves before the founder signs off. */
type Drawers = ReturnType<typeof useDrawers>;
type Load = { v: Awaited<ReturnType<typeof apiLaunch.view>>; pkgs: Record<string, Package> };
const TITLE: Record<string, string> = { retail: "Shelf trial", popup: "Pop-up venue", creator: "Creator", community: "Community partner", digital: "Google Ads" };
const KIND: Record<string, MockKind> = { retail: "shelf", popup: "sampling", creator: "brief", community: "sampling" };
const DONE = ["SENT", "TEST_SENT", "CREATED_PAUSED"];
const KM_PER_MI = 1.609344;  // radius is stored in km; shown and edited in miles (owner, 28 Oct 2026)
const SETTINGS_FIELDS = ["from_name", "budget", "start", "end", "radius_km"];
const fmtDay = (s: string | null) => (s ? new Date(`${s}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "—");

async function load(pid: string, city: string): Promise<Load> {
  const v = await apiLaunch.view(pid, city);
  const ids = v.items.flatMap((i) => i.packages.map((p) => p.id));
  const got = await Promise.all(ids.map((x) => api6.package(pid, x).catch(() => null)));
  return { v, pkgs: Object.fromEntries(got.filter((x): x is Package => !!x?.version).map((x) => [x.id, x])) };
}

function mockData(id: string, m: KitMockup): MockData {
  const prods = m.kit?.products ?? m.products;
  return { brand: m.overlay.brand || "Your brand", city: m.overlay.city, concept: { name: m.overlay.concept.name, line: m.overlay.concept.line },
    products: prods.map((x) => ({ title: x.title, src: x.artifact ? api65.artifact(id, x.artifact.sha256) : null })),
    copy: m.kit?.copy ?? null, partner: m.kit?.partner ?? null,
    scene: m.state === "READY" && m.image ? api65.artifact(id, m.image.artifact.sha256) : null, looks: m.looks };
}

type PickedAd = { kind: MockKind; d: MockData };
const AD_KINDS: MockKind[] = ["ad1", "ad2", "ad3"];

/** The three Launch ads' picture data: the digital mockup (scene, looks, products) with the drafted copy. */
function adMock(id: string, it: LaunchItem, pk?: Package, mock?: KitMockup): MockData {
  const base: MockData = mock ? mockData(id, mock) : pk ? { brand: pk.creative.display_name || "", city: pk.targeting.geo.name, concept: pk.concept, partner: null,
    products: pk.assets.filter((a) => a.readable).slice(0, 3).map((a) => ({ title: a.alt_text, src: api6.exportUrl(id, pk.id, "asset", pk.version, a.artifact.id, true) })), copy: null }
    : { brand: "", city: "", concept: { name: "", line: null }, partner: null, products: [], copy: null };
  const vs = it.variants || [];
  return { ...base, copy: { headlines: vs.map((x) => x.headline), primary_text: null, bodies: vs.map((x) => x.description), cta: pk?.creative.cta ?? null } };
}

export default function LaunchPage({ id, city }: { id: string; city: string }) {
  const { project: p, reload: reloadP } = useProject(id);
  const { data, error, reload } = useLive(id, (pid) => load(pid, city));
  const { data: mocks, reload: reloadMocks } = useLive(id, (pid) => api65.mockups(pid, city) as Promise<KitMockup[]>);
  const drawers = useDrawers();
  // start (or find) everything in the background, then read it all once more
  useEffect(() => { apiLaunch.prewarm(id, city).catch(() => null).then(() => { reload(); reloadP(); reloadMocks(); }); }, [id, city]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!p) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const shell = (body: ReactNode) => (
    <div className="app">
      <ProjectHeader brand={p.brief?.brand_name || p.site} site={p.site} intent={goalLine(p)} briefVersion={p.brief?.version}
        running={false} usage={usageLine(p)} demo={p.demo} onBrief={() => navigate(`/p/${id}`)}
        onActivity={() => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> })} />
      <StageNav stages={stagesFor(p)} current={3} />
      <ContextStrip p={p} onEdit={() => drawers.push({ key: "ctx", kicker: "YOUR GOAL", render: () => <ContextEdit p={p} reload={reloadP} /> })} />
      <main id="main">{body}</main>
      <DrawerStack d={drawers} />
    </div>
  );
  if (error && !data) return shell(<div className="aa-wrap"><p className="field-error" role="alert" style={{ marginTop: 24 }}>{error}</p></div>);
  if (!data) return shell(<div className="aa-wrap"><div style={{ marginTop: 24 }}><Skeleton width="50%" /><Skeleton width="70%" /></div></div>);
  const { v, pkgs } = data;
  if (!v.plan || !v.settings) return shell(
    <DecisionBar kicker="LAUNCH" title="Sign off a plan first" subtitle={v.reason}
      secondary={<Link to={`/p/${id}/plan/${city}`} className="btn-secondary btn-lg">← Back to the plan</Link>} />);
  const kit = (mocks || []).filter((m) => !m.earlier_plan);
  const rows = v.items.map((it) => ({ it, pk: it.packages.map((x) => pkgs[x.id]).find(Boolean) }));
  // owner, 28 Oct 2026: once an ad is picked, it is the picture everywhere on the page (and it goes with every email)
  const ads = rows.find(({ it }) => it.method === "digital");
  const pickedAd: PickedAd | null = ads && ads.it.picked != null ? (() => {
    const d = adMock(id, ads.it, ads.pk, kit.find((m) => m.method === "digital"));
    return { kind: AD_KINDS[ads.it.picked!] || "ad1", d: ads.it.picked === 2 ? { ...d, scene: null } : d };
  })() : null;
  const done = rows.filter(({ pk }) => isDone(pk?.destination_states[0])).length;
  const cityName = kit[0]?.overlay.city || p.beachhead?.name || city;
  const hist = historyOf(Object.values(pkgs));
  return shell(<>
    <DecisionBar kicker={`LAUNCH · PLAN V${v.plan.version}`} title={`Launch in ${cityName}`}
      subtitle={<>{done} of {rows.length} signed off. Each item shows exactly what goes out; nothing leaves until you sign it off.</>} />
    <div className="aa-wrap launch">
      {v.plan_changed && <Notice kind="changed">Your plan changed since you signed off. This checklist follows signed plan v{v.plan.version};{" "}
        <Link className="link" to={`/p/${id}/plan/${city}`}>sign again</Link> to use your changes.</Notice>}
      <Settings id={id} city={city} s={v.settings} hasAds={v.items.some((i) => i.method === "digital")} reload={reload} />
      <ol className="launch-list" aria-label="Launch checklist">
        {rows.map(({ it, pk }, i) => (
          <Item key={it.method} n={i + 1} id={id} city={city} it={it} pk={pk} reload={reload} drawers={drawers}
            mock={kit.find((m) => m.method === it.method)} pickedAd={pickedAd} />
        ))}
      </ol>
      <section className="aa-card launch-results" aria-label="Recent results">
        <span className="aa-kicker">Recent results</span>
        {hist.length ? hist.slice(0, 6).map((h, i) => <div key={i} className="aa-hist"><span>{h.at.slice(0, 10)}</span><span>{h.t}</span></div>)
          : <p className="small steel">Nothing has gone out yet. What you sign off, and what you record yourself, shows here.</p>}
        {hist.length > 6 && <button type="button" className="link" style={{ alignSelf: "flex-start" }} onClick={() => drawers.push({ key: "hist", kicker: "DELIVERY HISTORY",
          render: () => <div className="stack">{hist.map((h, i) => <div key={i} className="aa-hist"><span>{h.at.slice(0, 16).replace("T", " ")}</span><span>{h.t}</span></div>)}</div> })}>View all</button>}
      </section>
      {v.earlier_plan.length > 0 && <details className="card launch-earlier"><summary className="subheading">Earlier plan · {v.earlier_plan.length}</summary>
        <ul className="plain">{v.earlier_plan.map((e) => <li key={e.package.id} className="small"><Link className="link" to={`/p/${id}/deliver/${e.package.id}`}>
          {e.package.id.split("_").pop()}</Link> · {e.reason}</li>)}</ul></details>}
    </div>
  </>);
}

const isDone = (d?: DestinationState) => !!d && (DONE.includes(d.summary) || !!d.manual);

/** Asked once, with defaults (owner, 28 Oct 2026): never a blocker per package. Saving updates every package not yet sent. */
function Settings({ id, city, s, hasAds, reload }: { id: string; city: string; s: LaunchSettings; hasAds: boolean; reload: () => void }) {
  const major = (m: LaunchSettings["budget"]) => (m ? String(m.minor / 100) : "");
  const init = { sender_name: s.sender_name || "", budget: major(s.budget), radius_km: s.radius_km == null ? "" : String(Math.round((s.radius_km / KM_PER_MI) * 10) / 10), start: s.start || "", end: s.end || "" };
  const [f, setF] = useState(init);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const changed = (Object.keys(init) as (keyof typeof init)[]).filter((k) => f[k] !== init[k]);
  const by = (k: keyof LaunchSettings["by"]) => ({ DEFAULT: "default", YOU_SAID: "you said", PLAN: "from your plan", PLAN_ESTIMATE: "from your plan's estimate", PLAN_CONVERTED: "from your plan, in the ad account's currency" } as Record<string, string>)[s.by[k]] || "";
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      const fields: Record<string, unknown> = {};
      for (const k of changed) fields[k] = k === "budget" ? (f.budget ? { amount: f.budget, ccy: s.budget?.ccy || "USD" } : null)
        : k === "radius_km" ? (f.radius_km ? Math.round(Number(f.radius_km) * KM_PER_MI * 100) / 100 : null) : f[k] || null;
      await apiLaunch.settings(id, city, fields); reload();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const field = (_k: keyof typeof init, label: string, input: ReactNode, hint: string) => (
    <label className="launch-field"><span className="label steel">{label}</span>{input}<span className="launch-by">{hint}</span></label>);
  return (
    <section id="launch-settings" className="launch-settings" aria-label="Launch settings">
      <div className="launch-settings-head"><b>Asked once for every item</b><span className="small steel">Change any of these and every message and the campaign follow.</span></div>
      <div className="launch-settings-grid">
        {field("sender_name", "Sender name", <input className="input" value={f.sender_name} onChange={(e) => setF({ ...f, sender_name: e.target.value })} />, by("sender_name"))}
        {hasAds && <>
          {field("budget", `Ad budget a day${s.budget ? ` (${s.budget.ccy})` : ""}`, <input className="input" inputMode="decimal" value={f.budget}
            onChange={(e) => setF({ ...f, budget: e.target.value })} />, by("budget"))}
          {field("radius_km", "Radius (mi)", <input className="input" inputMode="numeric" value={f.radius_km}
            onChange={(e) => setF({ ...f, radius_km: e.target.value })} />, by("radius_km"))}
          {field("start", "Start", <input className="input" type="date" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} />, by("start"))}
          {field("end", "End", <input className="input" type="date" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} />, by("end"))}
        </>}
      </div>
      {changed.length > 0 && <div className="btn-row"><button type="button" className="btn-secondary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Use these for every item"}</button>
        <button type="button" className="link" onClick={() => setF(init)}>Undo</button></div>}
      {err && <p className="field-error" role="alert">{err}</p>}
    </section>
  );
}

type Need = { field: string; text: string; short: string };
function needsOf(d: DestinationState): Need[] {
  const cap = capOf(d);
  const list = [...d.state.missing, ...(cap === "connected" ? d.plan.blockers.filter((b) => !d.state.missing.some((m) => m.text === b.text)
    && !["account", "sender"].includes(b.field)) : [])];
  return list.map((m) => ({ ...m, short: SETTINGS_FIELDS.includes(m.field) ? m.text.replace(/^Add |^Enter |^Choose /, "").replace(/, or set one.*$/, "")
    : m.field === "to" ? "an email address" : /rights/i.test(m.text) ? "your OK to use the image"
    : /^".*"/.test(m.text) ? "a sentence to check" : m.field === "template" ? m.text.replace(/^Needs /, "").replace(/:.*$/, "") : m.text }));
}

type Sign = { label: string; sub: ReactNode; run: () => Promise<unknown>; done: string } | null;

function Item({ n, id, city, it, pk, mock, pickedAd, reload, drawers }: {
  n: number; id: string; city: string; it: LaunchItem; pk?: Package; mock?: KitMockup; pickedAd: PickedAd | null; reload: () => void; drawers: Drawers;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const d = pk?.destination_states[0];
  const act = async (label: string, fn: () => Promise<unknown>, after?: string) => {
    setBusy(label); setErr(null); setNote(null);
    try { const r = await fn(); const n2 = (r as { note?: string | null })?.note; if (n2 || after) setNote(n2 || after!); await reload(); }
    catch (e) { setErr((e as Error).message); await reload(); } finally { setBusy(null); }
  };
  const digital = it.method === "digital";
  const partner = pk ? partnerOf(pk) : "";
  const pickOpt = pk?.partner?.lead_id ? it.options?.find((o) => o.lead_id === pk.partner!.lead_id) : undefined;
  const dm = !!pk && !digital && !pk.recipients.to.length && (pickOpt?.route.kind === "profile" || pickOpt?.route.kind === "phone");
  const phone = pickOpt?.route.kind === "phone" ? pickOpt.route.value : null;
  const noAddress = !!pk && !digital && !pk.recipients.to.length && !dm;  // test routing would send without one; the real message needs it
  const needs = pk && d ? [...(noAddress ? [{ field: "to", text: "Their email address", short: "an email address" }] : []),
    ...needsOf(d).filter((m) => m.field !== "to")] : [];
  const finished = isDone(d);
  const cap = d && capOf(d);
  const runnable = !!d && (d.next.includes("approve") || d.next.includes("execute"));
  const body = pk?.email ? `Subject: ${pk.email.subject}\n\n${pk.email.body}` : "";

  // ---- the one sign-off: approve this exact version and run it, or the honest manual route ----
  let sign: Sign = null;
  if (pk && d && !finished && !needs.length && !["UNCERTAIN", "EXECUTING", "FAILED_VERIFICATION"].includes(d.summary) && !(digital && it.picked == null)) {
    const approveRun = async () => {
      let aid = d.next.includes("execute") ? d.state.approval_id : null;
      if (!aid) {
        const r = await api6.approve(id, pk.id, d.destination, pk.version);
        if (r.use !== "execute") throw new Error("Approved for your own use only: nothing was sent or created.");
        aid = r.approval_id;
      }
      return api6.execute(id, pk.id, d.destination, aid);
    };
    if (dm && phone) sign = { label: "Copy message · I’ll call or text them", done: "Copied. Recorded as sent by you.",
      sub: <>No email address was found for {partner}; their own site lists <a className="link" href={`tel:${phone}`}>{phone}</a>. Copies the message and records it as sent by you.</>,
      run: async () => { await navigator.clipboard.writeText(pk.email?.body || "").catch(() => null); return apiLaunch.manual(id, pk.id, "EMAIL", `Phone ${phone}`); } };
    else if (dm) sign = { label: "Copy message for Instagram DM", done: "Copied. Paste it into a DM to them; it's recorded as sent by you.",
      sub: <>No email address was found for {partner}. Copies the message and records it as sent by you, as a DM from your own account.</>,
      run: async () => { await navigator.clipboard.writeText(pk.email?.body || "").catch(() => null); return apiLaunch.manual(id, pk.id, "EMAIL", "Instagram DM"); } };
    else if (cap === "connected" && runnable && !digital) sign = { label: d.routing ? "Send test email" : "Send with Gmail", done: "",
      sub: d.routing ? <>Goes from Landfall’s test sender to its private test inbox. {partner} isn’t contacted. {sharedOf(d)?.disclosure}</>
        : <>Sends this exact message from {d.state.account}.</>, run: approveRun };
    else if (cap === "connected" && runnable) sign = { label: sharedOf(d) ? "Create campaign (paused, test account)" : "Create campaign (paused)", done: "",
      sub: <>Creates 1 campaign, 1 ad group and 1 search ad in {d.state.account || "the account"}, all paused: nothing serves or spends. {sharedOf(d)?.disclosure}</>, run: approveRun };
    else if (!digital) sign = { label: "Copy message · I’ll send it myself", done: "Copied. Recorded as sent by you.",
      sub: <>Sending from Landfall isn’t available here ({d.text.replace(/\.$/, "")}). Copies the message and records it as sent from your own mail.</>,
      run: async () => { await navigator.clipboard.writeText(body).catch(() => null); return apiLaunch.manual(id, pk.id, "EMAIL", "Sent from your own mail"); } };
    else sign = { label: "I’ve set it up in Google Ads", done: "Recorded as set up by you.",
      sub: <>Creating it from Landfall isn’t available here. <a className="link" href={api6.exportUrl(id, pk.id, "settings_csv", pk.version)} download>Download the settings sheet</a>, set it up, then record it.</>,
      run: () => api6.manual(id, pk.id, d.destination, new Date().toISOString().slice(0, 10)) };
  }

  // ---- the status line ----
  const status = (() => {
    if (finished && d) return { tone: "done", text: outcomeOf(d, partner) || "Done" };
    if (d && ["UNCERTAIN", "FAILED", "FAILED_VERIFICATION", "PARTLY_CREATED", "EXECUTING"].includes(d.summary)) return { tone: "need", text: outcomeOf(d, partner) || d.text };
    if (!pk) {
      if (it.preparing) return { tone: "wait", text: digital ? "Writing the ads…" : "Preparing the message…" };
      if (digital) return { tone: "need", text: it.failed ? "The ads couldn’t be drafted" : "Drafting the ads…" };
      if (it.creators?.searching && !it.options?.length) return { tone: "wait", text: "Finding creators…" };
      if (!it.options?.length) return { tone: "need", text: "Nobody Landfall can contact yet" };
      return { tone: "need", text: it.method === "creator" ? "Pick a creator" : "Pick who it goes to" };
    }
    if (digital && it.picked == null) return { tone: "need", text: "Pick the ad to run" };
    if (needs.length) return { tone: "need", text: `Needs: ${needs[0].short}` };
    if (sign) return { tone: "ready", text: digital ? "Ready to create" : phone ? "Ready to call" : dm ? "Ready to copy for a DM" : "Ready to send" };
    return { tone: "need", text: d?.text || "Not ready" };
  })();

  const head = (
    <div className="launch-head">
      <span className={`launch-num ${status.tone}`} aria-hidden>{status.tone === "done" ? "✓" : n}</span>
      <div className="launch-title"><b>{TITLE[it.method] || it.label}{pk?.partner && !digital ? ` · ${pk.partner.name}` : ""}</b>
        <span className="small steel">{it.role === "MAIN" ? "Main test" : "Supports the main test"}{(pk as (Package & { copy_origin?: string }) | undefined)?.copy_origin === "ENRICHED" ? " · tailored from your standard message" : ""}</span></div>
      <span className={`launch-status ${status.tone}`} role="status">{status.text}</span>
    </div>
  );
  if (finished && !open) return (
    <li className="launch-item done">{head}
      <div className="launch-done-row"><button type="button" className="link" onClick={() => setOpen(true)}>Show what went out</button>
        {pk && <Link className="link" to={`/p/${id}/deliver/${pk.id}`}>Full record</Link>}</div></li>);

  return (
    <li className={`launch-item ${status.tone}`}>
      {head}
      {!digital && <Recipient id={id} city={city} it={it} pk={pk} busy={!!busy || finished} act={act} />}
      {digital ? <Ads id={id} it={it} pk={pk} mock={mock} busy={!!busy || finished} act={act} />
        : <Message id={id} it={it} pk={pk} mock={mock} pickedAd={pickedAd} />}
      {pk && needs.length > 0 && !finished && <div className="launch-needs">{needs.map((m) => <Fix key={m.field + m.text} id={id} pk={pk} m={m} act={act} busy={!!busy} />)}</div>}
      {d && ["UNCERTAIN", "FAILED", "FAILED_VERIFICATION", "PARTLY_CREATED"].includes(d.summary) &&
        <div className="aa-result aa-result-open" role="status"><b>{outcomeOf(d, partner) || d.summary}</b><span className="aa-line">{d.text}</span>
          <Link className="btn-secondary" to={`/p/${id}/deliver/${pk!.id}${digital ? `?dst=${d.destination}` : ""}`}>Check and resolve it</Link></div>}
      {sign && <div className="launch-sign">
        <button type="button" className="btn-primary btn-lg" disabled={!!busy} onClick={() => act(sign!.label, sign!.run, sign!.done || undefined)}>{busy === sign.label ? "Working…" : sign.label}</button>
        <span className="aa-sub">{sign.sub} Editing anything after this needs a new sign-off.</span></div>}
      {finished && d && <div className="aa-result" role="status"><b>{outcomeOf(d, partner)}</b><span className="aa-line">{d.text}</span>
        <button type="button" className="link" onClick={() => setOpen(false)}>Collapse</button></div>}
      <div className="launch-foot">
        {pk && <Link className="link" to={`/p/${id}/deliver/${pk.id}`}>See exactly what’s sent</Link>}
        <Link className="link" to={`/p/${id}/plan/${city}/${it.method}`}>Research behind this</Link>
        {mock && <button type="button" className="link" onClick={() => drawers.push({ key: `kit-${it.method}`, kicker: "LAUNCH KIT · ILLUSTRATIVE",
          render: () => <div className="stack"><div className="drawer-mock"><CampaignMockup kind={digital ? "waitlist" : KIND[it.method] || "shelf"} d={mockData(id, mock)} /></div>
            <p className="small steel">Illustrative. It doesn’t imply partner approval; never attached to what you send.</p></div> })}>Launch kit</button>}
      </div>
      {note && <p className="small launch-note" role="status">{note}</p>}
      {err && <p className="field-error" role="alert">{err}</p>}
    </li>
  );
}

type ActFn = (label: string, fn: () => Promise<unknown>, after?: string) => Promise<unknown> | void;
const SELF = "self";

/** Who it goes to: contactable people only (owner, 28 Oct 2026). Creators from Influencers.club are listed biggest first;
 *  picking one looks up their email (once); without one, the message goes as a DM you send. */
function Recipient({ id, city, it, pk, busy, act }: { id: string; city: string; it: LaunchItem; pk?: Package; busy: boolean; act: ActFn }) {
  const [all, setAll] = useState(false);
  const [pending, setPending] = useState<string | null>(null);  // the row being picked: its email lookup can take a while
  const [change, setChange] = useState(false);
  const opts = it.options || [];
  const cur = pk ? (pk.partner?.lead_id ? opts.find((o) => o.lead_id === pk.partner!.lead_id)?.key ?? pk.partner.lead_id : SELF) : null;
  const pick = (o: LaunchOption | null) => act("pick", () => apiLaunch.pick(id, city, o ? { method: it.method, lead_id: o.lead_id, ...(o.search ? { search_id: o.search.id, index: o.search.index } : {}) }
    : { method: it.method }));
  const c = it.creators;
  if (it.method !== "creator") return (
    <div className="launch-to">
      <span className="label steel">To</span>
      {opts.length === 0 && !pk ? <span className="small">Landfall found nobody it can contact for this yet (no email address on their own pages).{" "}
        <button type="button" className="link" disabled={busy} onClick={() => pick(null)}>Someone you’ll name yourself</button></span>
        : <select className="input" aria-label="Who it goes to" value={cur ?? ""} disabled={busy} onChange={(e) => pick(e.target.value === SELF ? null : opts.find((o) => o.key === e.target.value)!)}>
          {!cur && <option value="">Choose…</option>}
          {opts.map((o) => <option key={o.key} value={o.key}>{o.name} · {o.route.value}</option>)}
          <option value={SELF}>Someone you’ll name yourself</option></select>}
    </div>);
  // once a creator is picked, the others fold away so the message is in view (owner, 28 Oct 2026)
  const picked = cur && cur !== SELF ? opts.find((o) => o.key === cur) : undefined;
  const folded = !!picked && !change && !pending;
  const shown = folded ? [picked!] : all ? opts : opts.slice(0, 5);
  return (
    <div className="launch-creators" role="group" aria-label="Pick a creator">
      <span className="label steel">Pick a creator{c ? ` · ${c.attribution}` : ""}</span>
      {c?.searching && <p className="small"><Skeleton width="30%" /> Searching Influencers.club for creators with an audience in this city…</p>}
      {c && !c.available && <p className="small">{c.reason} Creators Landfall can reach are listed below; or name one yourself.</p>}
      {c?.note && <p className="small prep-question">{c.note}</p>}
      {c?.searched && c.status === "EMPTY" && <p className="small">No creators matched the search. That describes the search, not the city.</p>}
      {c?.available && !c.enrich_ok && <p className="small prep-question">The creator-contact allowance is used up: a creator you pick gets the message as a DM you send yourself.</p>}
      {opts.length === 0 && !c?.searching && <p className="small">Nobody Landfall can contact yet: no creator here has an email address or a confirmed profile.</p>}
      <ul className="plain launch-creator-list">{shown.map((o) => {
        const on = cur === o.key;
        return <li key={o.key} className={`launch-creator ${on ? "on" : ""}`}>
          <span className="launch-creator-name"><b>{o.name}</b> <a className="source-url" href={o.route.kind === "profile" ? o.route.value : `${o.route.kind === "phone" ? "tel" : "mailto"}:${o.route.value}`} target="_blank" rel="noreferrer noopener">
            {o.route.kind === "profile" ? o.route.value.replace(/^https:\/\/(www\.)?/, "") : o.route.value}</a></span>
          <span className="small steel">{[o.followers != null && `${o.followers.toLocaleString()} followers`, o.engagement_percent != null && `${o.engagement_percent}% engagement`,
            o.audience_location && `audience: ${[o.audience_location].flat().join(", ")} (estimate)`, o.source === "RESEARCH" && "found by research",
            o.route.kind === "email" ? "email on file" : "email looked up when you pick"].filter(Boolean).join(" · ")}</span>
          {on ? <span className="mono steel">Picked</span>
            : pending === o.key ? <span className="small steel" role="status">Looking up their email…</span>
            : <button type="button" className="btn-secondary" disabled={busy || !!pending} onClick={async () => {
              setPending(o.key); try { await pick(o); } finally { setPending(null); setChange(false); } }}>Pick</button>}
        </li>;
      })}</ul>
      {folded ? <button type="button" className="link" style={{ alignSelf: "flex-start" }} onClick={() => setChange(true)}>Change creator ({opts.length - 1} other{opts.length === 2 ? "" : "s"})</button>
        : opts.length > 5 && <button type="button" className="link" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${opts.length}`}</button>}
      {cur !== SELF && <button type="button" className="link" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={() => pick(null)}>Someone you’ll name yourself</button>}
    </div>
  );
}

/** Exactly what goes out: the message itself (or, before it's prepared, the standard message filled from your records). */
function Message({ id, it, pk, mock, pickedAd }: { id: string; it: LaunchItem; pk?: Package; mock?: KitMockup; pickedAd: PickedAd | null }) {
  const to = pk ? (pk.recipients.to.join(", ") || (pk.partner ? `${pk.partner.name} · no email address` : "add the address below")) : it.preview?.to;
  const subject = pk ? pk.email?.subject : it.preview?.subject;
  const body = pk ? pk.email?.body : it.preview?.body;
  if (!subject && !body) return null;
  const attach = pk?.assets.filter((a) => a.role === "ATTACHMENT") || [];
  return (
    <div className="prep-preview launch-message">
      <div className="prep-preview-text">
        <span><span className="steel">To </span>{to}</span>
        <span><span className="steel">Subject </span><b>{subject}</b></span>
        <span className="launch-body">{body}</span>
        {attach.length > 0 && <span className="mono steel">Attached: {attach.map((a) => a.filename).join(", ")}</span>}
        {!pk && <span className="small steel">The standard message, filled from your records. It’s prepared for sending as soon as you pick.</span>}
        {pk?.preparing && <span className="small steel">Landfall is tailoring this message in the background; you can send it as it is.</span>}
      </div>
      {pickedAd ? <div className="prep-preview-mock"><div><CampaignMockup kind={pickedAd.kind} d={pickedAd.d} /></div></div>
        : mock && <div className="prep-preview-mock"><div><CampaignMockup kind={KIND[it.method] || "shelf"} d={mockData(id, mock)} /></div></div>}
    </div>
  );
}

/** Google Ads only (owner, 28 Oct 2026): three drafted ads to choose from; the one you pick leads the campaign. */
function Ads({ id, it, pk, mock, busy, act }: { id: string; it: LaunchItem; pk?: Package; mock?: KitMockup; busy: boolean; act: ActFn }) {
  const styled = it.styled?.length ? <span className="small steel">Styled from Qloo: {it.styled.join(" · ")}</span> : null;
  if (!it.variants?.length) return <div className="stack" style={{ gap: 4 }}><p className="small steel">Landfall writes three ads for you in the background.</p>{styled}</div>;
  // the ads show as soon as their copy is written, before the campaign exists (owner, 28 Oct 2026: "the ads are the first thing")
  const base = adMock(id, it, pk, mock);
  const vs = it.variants;
  const b = pk?.budget_view;
  return (
    <div className="stack" style={{ gap: 12 }}>
      {styled}
      <div className="launch-ads" role="radiogroup" aria-label="Pick the ad to run">
        {vs.map((x) => {
          const on = it.picked === x.index;
          return <button key={x.index} type="button" role="radio" aria-checked={on} className={`launch-ad ${on ? "on" : ""}`} disabled={busy || !pk}
            onClick={() => pk && act("pick-ad", () => api6.edit(id, pk.id, pk.version, { ad_pick: x.index }))}>
            <div className="launch-ad-frame"><CampaignMockup kind={AD_KINDS[x.index] || "ad1"} d={{ ...base, scene: x.index === 2 ? null : base.scene }} /></div>
            <b>{x.headline}</b><span className="small steel">{x.description}</span>
            {x.to_check.length > 0 && <span className="small prep-question">△ To check: {x.to_check.join(" · ")}</span>}
            <span className="launch-ad-pick">{on ? "✓ This ad" : pk ? "Use this ad" : "Setting up the campaign…"}</span>
          </button>;
        })}
      </div>
      {pk && <dl className="aa-rows aa-rows-wide launch-ad-rows">
        <dt>Where</dt><dd>Google Search · {pk.targeting.geo.name}{pk.targeting.geo.radius_km ? ` + ${Math.round((pk.targeting.geo.radius_km / KM_PER_MI) * 10) / 10} mi` : ""}</dd>
        <dt>Keywords</dt><dd>{(pk.copy.keywords || []).map((k) => k.text).join(" · ") || "None yet"}</dd>
        <dt>Budget</dt><dd>{b?.total_text ? `${b.total_text} a day` : "Not set"} · {fmtDay(pk.timing.start)} – {fmtDay(pk.timing.end)}</dd>
        <dt>Landing page</dt><dd>{pk.creative.landing_url || "Not set"}</dd>
      </dl>}
      <span className="aa-sub">Previews are illustrative, drawn from this copy and your saved images; Google shows a text search ad built from the picked headline and description first.</span>
    </div>
  );
}

/** The one thing an item needs, fixed where it's shown. */
function Fix({ id, pk, m, act, busy }: { id: string; pk: Package; m: Need; act: ActFn; busy: boolean }) {
  const [val, setVal] = useState("");
  const edit = (fields: object, done = "Saved.") => act("fix", () => api6.edit(id, pk.id, pk.version, fields), done);
  const segs: Segment[] = [pk.copy.subject, ...(pk.copy.segments || []), ...(pk.copy.headlines || []), ...(pk.copy.descriptions || []),
    pk.copy.primary_text, pk.copy.ad_text].filter((s): s is Segment => !!s);
  const seg = segs.find((s) => s.field === m.field && s.state === "TO_CHECK");
  const line = (children: ReactNode) => <div className="launch-fix"><span className="prep-question">△</span><div className="launch-fix-body">{children}</div></div>;
  if (seg) {
    const list = (k: "segments" | "headlines" | "descriptions") => (pk.copy[k] || []).filter((s) => s !== seg).map((s) => s.text);
    const kind = m.field.startsWith("paragraphs") ? "segments" : m.field.startsWith("headlines") ? "headlines" : m.field.startsWith("descriptions") ? "descriptions" : null;
    return line(<><span>“{seg.text}” {seg.why || "isn’t backed by its evidence"}.</span>
      <span className="btn-row"><button type="button" className="btn-secondary" disabled={busy} onClick={() => edit({ confirm: [seg.field] }, "Kept as your own statement.")}>Keep it as mine</button>
        {kind && <button type="button" className="btn-secondary" disabled={busy} onClick={() => edit({ [kind]: list(kind) }, "Removed.")}>Remove it</button>}</span></>);
  }
  if (m.field === "to") return line(<form className="aa-inline" onSubmit={(e) => { e.preventDefault(); if (val.trim()) edit({ to: [val.trim()] }, "Added."); }}>
    <span>Their email address</span><input className="input" type="email" value={val} onChange={(e) => setVal(e.target.value)} placeholder="name@example.com" aria-label="Their email address" />
    <button type="submit" className="btn-secondary" disabled={busy || !val.trim()}>Add</button></form>);
  if (m.field === "template") {
    const f = (m.text.match(/^Needs ([a-z ]+):/) || [])[1]?.replace(/ /g, "_");
    const fill = (t: string) => t.split(`{${f}}`).join(val.trim());
    return line(<form className="aa-inline" onSubmit={(e) => { e.preventDefault(); if (val.trim()) edit({ subject: fill(pk.copy.subject?.text || ""), segments: (pk.copy.segments || []).map((s) => fill(s.text)) }, "Filled in."); }}>
      <span>{m.short.replace(/^\w/, (c) => c.toUpperCase())}</span><input className="input" value={val} onChange={(e) => setVal(e.target.value)} aria-label={m.short} />
      <button type="submit" className="btn-secondary" disabled={busy || !val.trim()}>Fill in</button></form>);
  }
  if (/rights/i.test(m.text)) return line(<><span>{m.text}. Your own product photos from your site go into the ad.</span>
    <button type="button" className="btn-secondary" disabled={busy} onClick={() => edit({ rights_confirmed: true }, "Confirmed.")}>I may use these images</button></>);
  if (m.field === "landing_url") return line(<form className="aa-inline" onSubmit={(e) => { e.preventDefault(); if (val.trim()) edit({ landing_url: val.trim() }, "Added."); }}>
    <span>Landing page</span><input className="input" value={val} onChange={(e) => setVal(e.target.value)} placeholder="yourbrand.com/city" aria-label="Landing page" />
    <button type="submit" className="btn-secondary" disabled={busy || !val.trim()}>Add</button></form>);
  if (SETTINGS_FIELDS.includes(m.field)) return line(<span>{m.text}: <a className="link" href="#launch-settings">set it once above</a>.</span>);
  return line(<span>{m.text}. <Link className="link" to={`/p/${id}/deliver/${pk.id}`}>Fix it in the full review</Link></span>);
}
