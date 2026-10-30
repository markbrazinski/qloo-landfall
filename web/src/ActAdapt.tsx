import "./act.css";
import { useState } from "react";
import { type ActItem, type DestinationState, type Package, api6, api65, useLive, useProject } from "./api";
import { ActivityDrawer, stagesFor, usageLine } from "./Brand";
import { Pill, capOf, outcomeOf, partnerOf, reasonOf } from "./Deliver";
import { ContextEdit, ContextStrip, goalLine } from "./Goal";
import { DecisionBar, DrawerStack, ProjectHeader, Skeleton, StageNav, navigate, useDrawers } from "./ui";

const METHOD: Record<string, string> = { retail: "Retail", popup: "Pop-up", creator: "Creators", community: "Community", digital: "Local ads" };
const DONE = ["SENT", "CREATED_PAUSED"];  // TEST_SENT is never contact (6.7 G3): the real outreach is still yours to do
type Row = { v: Package; d: DestinationState };

/** Every package of the signed plan, with its destinations: GET /plans/{city}/act, then each package view. */
async function load(pid: string, city: string) {
  const a = await api65.act(pid, city);
  const ids = [...new Set(a.items.filter((i) => i.package).map((i) => i.package!.id))];
  return { a, pkgs: await Promise.all(ids.map((x) => api6.package(pid, x))) };
}

/** v4 screen 12 · Act & adapt: the next useful action, outreach by method, ads by campaign, recent results, package contents. */
export default function ActAdaptPage({ id, city }: { id: string; city: string }) {
  const { project: p, reload } = useProject(id);
  const { data, error } = useLive(id, (pid) => load(pid, city));
  const drawers = useDrawers();
  const [open, setOpen] = useState(false);
  if (!p) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const body = () => {
    if (error && !data) return <div className="aa-wrap"><p className="field-error" role="alert" style={{ marginTop: 24 }}>{error}</p></div>;
    if (!data) return <div className="aa-wrap"><div style={{ marginTop: 24 }}><Skeleton width="50%" /><Skeleton width="70%" /></div></div>;
    const { a, pkgs } = data;
    const rows: Row[] = pkgs.filter((v) => v.version).flatMap((v) => v.destination_states.map((d) => ({ v, d })));
    const email = rows.filter((r) => r.v.flow === "outreach" && r.d.destination === "EMAIL");
    const ads = rows.filter((r) => r.v.flow === "digital");
    const open_ = (r: Row) => !DONE.includes(r.d.summary) && !r.d.manual;
    const go = (r: Row) => navigate(`/p/${id}/deliver/${r.v.id}${r.v.flow === "digital" ? `?dst=${r.d.destination}` : ""}`);
    const prepare = () => navigate(`/p/${id}/prepare/${city}`);
    const waiting = a.items.filter((i) => (i.kind === "CHOOSE_RECIPIENTS" || i.kind === "DIGITAL_SETTINGS" || i.kind === "BRAND_ASSETS") && i.status === "NEEDS_INFORMATION");
    // the next useful action (v4 `needs()`): the first message, then the first campaign, still waiting on the founder
    const e = email.find(open_), ad = !e ? ads.find(open_) : undefined;
    const next = !a.plan ? { t: "Nothing to act on yet", sub: a.reason || "Prepare the plan first.", btn: "Open Prepare", go: prepare }
      : e ? { t: `Inquiry to ${partnerOf(e.v)}`, btn: "Review the email", go: () => go(e),
          sub: !e.v.recipients.to.length ? "Needs information: an email address" : e.d.summary === "UNCERTAIN" ? "Not sure Gmail accepted it · check your Sent folder"
            : e.d.summary === "TEST_SENT" ? `Test email sent · ${partnerOf(e.v)} not contacted. Send it from your own mail when you’re ready`
            : capOf(e.d) === "connected" ? "Review and send with Gmail" : "Review it, then send it from your own mail" }
      : ad ? { t: `Local ads · ${ad.d.label}`, btn: "Review the ads", go: () => go(ad),
          sub: !ad.v.creative.landing_url ? "Needs information: landing page link" : capOf(ad.d) === "connected" ? "Review and create paused" : "Review the package, then set it up yourself" }
      : waiting.length ? { t: "Finish preparing the plan", sub: `${waiting.length} item${waiting.length > 1 ? "s" : ""} in Prepare still need${waiting.length > 1 ? "" : "s"} information`, btn: "Open Prepare", go: prepare }
      : { t: "Nothing is waiting on you", sub: "Results will appear here.", btn: "Back to the plan", go: () => navigate(`/p/${id}/plan/${city}`) };
    // outreach · by method: one card per method, every recipient's message in it
    const methods = [...new Set(a.items.filter((i) => i.method && i.method !== "digital" && (i.kind === "CHOOSE_RECIPIENTS" || i.kind === "OUTREACH")).map((i) => i.method!))];
    const outreach = methods.map((m) => ({ m, list: email.filter((r) => r.v.method === m), choose: a.items.find((i) => i.kind === "CHOOSE_RECIPIENTS" && i.method === m) }));
    const unprepared = a.items.filter((i) => i.kind === "DIGITAL_SETTINGS");
    const hist = historyOf(pkgs);
    const parts = a.items.filter((i) => i.kind !== "DELIVER");
    const drafted = parts.filter((i) => i.status !== "NEEDS_INFORMATION").length;
    return <>
      <DecisionBar kicker={`ACT & ADAPT${a.plan ? ` · PLAN V${a.plan.version}` : ""}`} title={next.t} subtitle={next.sub}
        primary={{ label: next.btn, onClick: next.go }} />
      <div className="aa-wrap">
        {outreach.length > 0 && <><span className="aa-section">Outreach · by method</span>
          <div className="aa-grid2">{outreach.map(({ m, list, choose }) => <MethodCard key={m} m={m} list={list} choose={choose} onGo={go} onPrepare={prepare} />)}</div></>}
        {(ads.length > 0 || unprepared.length > 0) && <><span className="aa-section">Ads · by campaign</span>
          <div className="aa-grid2">
            {ads.map((r) => <AdCard key={r.v.id + r.d.destination} r={r} onGo={go} />)}
            {unprepared.map((i) => <article key={i.key} className="aa-card" aria-label="Local ads">
              <div className="aa-card-head"><b>Local ads</b></div>
              <span className="aa-miss">△ Needs information: {i.missing.join(", ")}</span>
              <div className="btn-row"><button type="button" className="btn-secondary" onClick={prepare}>Prepare it</button></div></article>)}
          </div></>}
        <div className="aa-grid2" style={{ marginTop: 20 }}>
          <section className="aa-card" aria-label="Recent results">
            <span className="aa-kicker">Recent results</span>
            {hist.slice(0, 5).map((h, i) => <div key={i} className="aa-hist"><span>{h.at.slice(0, 10)}</span><span>{h.t}</span></div>)}
            <button type="button" className="link" style={{ alignSelf: "flex-start" }} onClick={() => drawers.push({ key: "hist", kicker: "DELIVERY HISTORY", render: () => (
              <div className="stack"><p className="small steel">What left Landfall, or what you recorded. Technical activity is separate.</p>
                {hist.length ? hist.map((h, i) => <div key={i} className="aa-hist"><span>{h.at.slice(0, 16).replace("T", " ")}</span><span>{h.t}</span></div>)
                  : <p className="small">Nothing yet.</p>}</div>) })}>View all</button>
          </section>
          <section className="aa-card" aria-label="Package contents">
            <button type="button" className="aa-pkg-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
              <span><b>Package contents</b><span className="aa-muted"> · {parts.length} part{parts.length === 1 ? "" : "s"} · {drafted} drafted for review · {parts.length - drafted} still in progress or need information</span></span>
              <span className="aa-open">{open ? "Hide" : "Open"}</span></button>
            {open && parts.map((i) => <div key={i.key} className="aa-part"><span>{partName(i)} <span className="aa-muted">· {METHOD[i.method || ""] || "Every method"}</span></span>
              <span className={i.status === "NEEDS_INFORMATION" ? "aa-miss" : "aa-muted"}>{i.status === "NEEDS_INFORMATION" ? "NEEDS INFORMATION" : "DRAFTED"}</span></div>)}
          </section>
        </div>
      </div>
    </>;
  };
  return (
    <div className="app">
      <ProjectHeader brand={p.brief?.brand_name || p.site} site={p.site} intent={goalLine(p)} briefVersion={p.brief?.version}
        running={false} usage={usageLine(p)} demo={p.demo} onBrief={() => navigate(`/p/${id}`)}
        onActivity={() => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> })} />
      <StageNav stages={stagesFor(p)} current={3} />
      <ContextStrip p={p} onEdit={() => drawers.push({ key: "ctx", kicker: "YOUR GOAL", render: () => <ContextEdit p={p} reload={reload} /> })} />
      <main id="main">{body()}</main>
      <DrawerStack d={drawers} />
    </div>
  );
}

/** What left Landfall, or what you recorded, newest first (Recent results; the Launch page shows it below its checklist). */
export function historyOf(pkgs: Package[]) {
  const rows: Row[] = pkgs.filter((v) => v.version).flatMap((v) => v.destination_states.map((d) => ({ v, d })));
  return rows.flatMap((r) => {
    const name = r.v.flow === "outreach" ? `Inquiry to ${partnerOf(r.v)}` : `Google Ads · ${r.d.label}`;
    return [...r.d.actions.filter((x) => x.status !== "REFUSED").map((x) => ({ at: x.at, t: `${name} · ${x.mode === "SIMULATED" ? `simulated · nothing was ${r.d.destination === "EMAIL" ? "sent" : "created"}` : histWord(r.d, x.status, partnerOf(r.v))}` })),
      ...r.d.manual_records.map((m) => ({ at: m.date, t: `${name} · ${r.d.destination === "EMAIL" ? (m.note ? `${m.note.toLowerCase()} · recorded by you` : "marked sent by you") : "set up by you"}${(m as { withdrawn_at?: string }).withdrawn_at ? " (undone)" : ""}` }))];
  }).sort((x, y) => (x.at < y.at ? 1 : -1));
}

function histWord(d: DestinationState, status: string, partner: string) {
  const email = d.destination === "EMAIL";
  return ({ SUCCEEDED: d.test ? `test email sent · ${partner} not contacted` : email ? "Gmail accepted it" : "created, paused",
    UNCERTAIN: email ? "not sure Gmail accepted it" : "not sure it was created", FAILED: email ? "Gmail refused it" : "didn’t go through",
    FAILED_VERIFICATION: "created, not confirmed paused", NOT_EXECUTED_BY_FOUNDER: "you said it didn’t happen", EXECUTING: email ? "sending…" : "creating…" } as Record<string, string>)[status]
    || status.toLowerCase().replace(/_/g, " ");
}

function partName(i: ActItem) {
  return { BRAND_ASSETS: "Your images", CHOOSE_RECIPIENTS: "Who to approach", OUTREACH: `Inquiry to ${i.partner || "your partner"}`,
    DIGITAL_SETTINGS: "Campaign settings", CAMPAIGN_REVIEW: "Ad package", DELIVER: "Delivery" }[i.kind];
}

/** LF-DestinationCard: pill · "Prepared: …" · the reason · one button. */
function MethodCard({ m, list, choose, onGo, onPrepare }: { m: string; list: Row[]; choose?: ActItem; onGo: (r: Row) => void; onPrepare: () => void }) {
  const first = list[0];
  if (!first) return (
    <article className="aa-card" aria-label={`${METHOD[m] || m} · Gmail`}>
      <div className="aa-card-head"><b>{METHOD[m] || m} · Gmail</b></div>
      <span className="aa-miss">△ Needs information: {choose?.missing.join(", ") || "Choose who to approach"}</span>
      <div className="btn-row"><button type="button" className="btn-secondary" onClick={onPrepare}>Choose in Prepare</button></div>
    </article>);
  const who = m === "creator" || list.length > 1 ? `${list.length} picked` : first.v.partner?.name;
  const name = [METHOD[m] || m, who, "Gmail"].filter(Boolean).join(" · ");
  const attach = first.v.assets.filter((a) => a.role === "ATTACHMENT").length;
  const prepared = list.length === 1 ? `1 inquiry${attach ? ` and ${attach} attachment${attach > 1 ? "s" : ""}` : ""}`
    : `${list.length} separate messages, one per ${m === "creator" ? "creator" : "recipient"}`;
  const miss = list.filter((r) => !r.v.recipients.to.length).map((r) => partnerOf(r.v));
  const done = list.map((r) => ({ r, o: outcomeOf(r.d, partnerOf(r.v)) })).filter((x) => x.o);
  const target = list.find((r) => !DONE.includes(r.d.summary) && !r.d.manual) || first;
  const cap = capOf(first.d);
  return (
    <article className="aa-card" aria-label={name}>
      <div className="aa-card-head"><b>{name}</b><Pill d={first.d} /></div>
      <span className="aa-line"><span className="aa-muted">Prepared: </span>{prepared}</span>
      <span className="aa-reason">{reasonOf(first.d, partnerOf(first.v))}</span>
      {miss.length > 0 && <span className="aa-miss">△ Needs information: an email address{list.length > 1 ? ` for ${miss.join(", ")}` : ""}</span>}
      {done.length > 0 && <span className="aa-done">{done.map((x) => (list.length > 1 ? `${partnerOf(x.r.v)}: ${x.o}` : x.o)).join(" · ")}</span>}
      <div className="btn-row"><button type="button" className="btn-secondary" onClick={() => onGo(target)}>
        {cap === "connected" ? "Review and send" : cap === "connect" ? "Connect Gmail" : "Review and send yourself"}</button>
        {cap === "connect" && <button type="button" className="btn-secondary" onClick={() => onGo(target)}>Review package</button>}</div>
    </article>
  );
}

function AdCard({ r, onGo }: { r: Row; onGo: (r: Row) => void }) {
  const { v, d } = r;
  const name = `Local ads · ${d.label}`;
  const cap = capOf(d);
  const ads = d.destination === "GOOGLE_ADS" ? "1 search ad" : "1 image ad";
  const miss = d.state.missing.map((m) => m.text);
  const o = outcomeOf(d, partnerOf(v));
  return (
    <article className="aa-card" aria-label={name}>
      <div className="aa-card-head"><b>{name}</b><Pill d={d} /></div>
      <span className="aa-line"><span className="aa-muted">Prepared: </span>1 campaign · {ads} · would be created {d.destination === "TIKTOK_ADS" ? "disabled (not delivering)" : "paused"}</span>
      <span className="aa-reason">{reasonOf(d, "")}</span>
      {miss.length > 0 && <span className="aa-miss">△ Needs information: {miss.join("; ")}</span>}
      {o && <span className="aa-done">{o}</span>}
      <div className="btn-row"><button type="button" className="btn-secondary" onClick={() => onGo(r)}>
        {cap === "connected" ? "Review and create paused" : cap === "connect" ? `Connect ${d.label}` : "Review and set up yourself"}</button>
        {cap === "connect" && <button type="button" className="btn-secondary" onClick={() => onGo(r)}>Review package</button>}</div>
    </article>
  );
}

