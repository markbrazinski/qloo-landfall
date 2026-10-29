import { useEffect, useRef, useState } from "react";
import { type Axes, type Candidate45, type Dossier, type Draft, type InvestigationView, type Proximity, type Project, type QlooCell, type QlooLead,
  api, api45, api6, api65, pathOf, qlooOf, useLive, useProject } from "./api";
import { ActivityDrawer, ProductDrawer, stagesFor, usageLine } from "./Brand";
import { ContextEdit, ContextStrip, goalLine } from "./Goal";
import { ChangePreview, ROLE_LABEL, changePlan, shownRole } from "./Plan";
import "./plan.css";
import "./detail.css";
import { openEvidence, openPartner } from "./Opportunities";
import { Chip, DecisionBar, DrawerStack, Link, Notice, ProjectHeader, Skeleton, StageNav, navigate, useDrawers } from "./ui";
import { noCaption } from "./api";

type Drawers = ReturnType<typeof useDrawers>;
const L = (s: string | null | undefined) => (s || "").replace(/_/g, " ");
const OVERALL: Record<string, string> = { EVIDENCED: "EVIDENCED", HYPOTHESIS_LED: "HYPOTHESIS-LED", NOT_YET: "NOT YET" };
const NOUN: Record<string, string> = { retail: "shop", popup: "venue", creator: "creator", community: "group", digital: "channel" };

/** /plan/{city}/{method} is v4's read-only method detail (09, B05–B08); the older /opportunities route keeps the slice-4.5
 * investigation workspace (Develop, drafts), which v4 has no screen for. */
export default function InvestigationPage(props: { id: string; city: string; method: string }) {
  return window.location.pathname.includes("/plan/") ? <MethodDetail {...props} /> : <LegacyInvestigation {...props} />;
}

/** S08 · investigation detail (BUILD-PLAN §4.5.8): /p/{id}/opportunities/{city}/{method}. */
function LegacyInvestigation({ id, city, method }: { id: string; city: string; method: string }) {
  const { project: p, reload: reloadProject } = useProject(id);
  const { data: v, error, reload } = useLive(id, (pid) => api45.investigation(pid, city, method));
  const drawers = useDrawers();
  const underPlan = window.location.pathname.includes("/plan/");  // P4: opened from the plan, so Back returns there
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [rationale, setRationale] = useState("");
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); reload(); reloadProject(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  if (error && !v) return <div className="app"><main className="page"><div className="error-panel card">{error} <a className="link" href={`/p/${id}/opportunities`}>Back</a></div></main></div>;
  if (!p || !v) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const d = v.dossier;
  const running = v.status === "queued" || v.status === "running";
  const investigate = () => act(() => api45.investigate(id, city, method));
  const develop = () => act(() => api45.develop(id, d!.id, d!.version, rationale || undefined));
  const primary = v.ruled_by_note || !v.researched ? undefined
    : running ? { label: "Investigating…", disabledReason: "Findings appear as checks finish" }
    : !d ? { label: `Investigate ${v.label.toLowerCase()}`, onClick: investigate }
    : d.status === "complete" || d.status === "partial" ? { label: v.draft ? "Develop again" : "Develop this approach", onClick: develop }
    : { label: "Investigate again", onClick: investigate };
  return (
    <div className="app">
      <ProjectHeader brand={p.brief?.brand_name || p.site} site={p.site} intent={goalLine(p)} briefVersion={p.brief?.version}
        running={running} usage={usageLine(p)} demo={p.demo} onBrief={() => navigate(`/p/${id}`)}
        onActivity={() => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> })} />
      <main id="main">
        <div className="page">{underPlan
          ? <button type="button" className="link" onClick={() => navigate(`/p/${id}/plan/${city}`)}>← Back to the plan</button>
          : <button type="button" className="link" onClick={() => navigate(`/p/${id}/opportunities`)}>← Back to the approaches</button>}</div>
        <DecisionBar kicker={`${v.label.toUpperCase()} · ${v.city.toUpperCase()}`}
          title={d?.concept.name ? `${d.concept.name}` : `How ${v.label.toLowerCase()} could work in ${v.city}`}
          subtitle={d?.concept.line || "A specialist looks for real candidates, checks each claim against its page, and says what is still unknown."}
          primary={primary}
          secondary={d && !running ? <button type="button" className="btn-secondary btn-lg" disabled={busy} onClick={investigate}>Investigate further</button> : undefined} />
        <div className="page stack">
          {err && <p className="field-error" role="alert">{err}</p>}
          {v.ruled_by_note && <div className="notice notice-changed" role="status"><Chip label="YOU SAID" />
            <span>Not run: your note rules this out (“{v.ruled_by_note}”).</span>
            <button type="button" className="btn-secondary notice-action" onClick={() => navigate(`/p/${id}/opportunities/research`)}>Edit your note</button></div>}
          {!v.researched && <Notice kind="info">Research {v.city} first; the specialist builds on that research.</Notice>}
          {running && <RunningState v={v} act={act} busy={busy} />}
          {v.can_continue && !running && <div className="notice notice-changed" role="status"><Chip label={v.status === "failed" ? "FAILED" : "STOPPED"} />
            <span>{v.run?.message || "Stopped. What was checked is kept."}</span>
            <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={() => act(() => api45.resume(id, city, method))}>Continue</button></div>}
          {d && (d.status === "complete" || d.status === "partial") && !running && (
            <div className="search-row"><label className="small" htmlFor="rationale">Why this one? (optional, used by Develop)</label>
              <input id="rationale" className="input" value={rationale} onChange={(e) => setRationale(e.target.value)}
                placeholder="e.g. one independent menswear shop" /></div>)}
          {d && <DossierBody p={p} v={v} d={d} drawers={drawers} act={act} busy={busy} />}
          {method === "creator" && !d && v.researched && <AddCreator p={p} city={city} v={v} act={act} busy={busy} />}
          {!d && !running && !v.ruled_by_note && v.researched && <div className="card empty-state">
            <h2 className="heading-sm">Not investigated yet</h2>
            <p>An investigation reuses what the city research already read.</p></div>}
          {v.develop_run && ["queued", "running"].includes(v.develop_run.status) && <Notice kind="info">Developing the draft…</Notice>}
          {v.develop_run && ["failed", "partial"].includes(v.develop_run.status) && (
            <div className="notice notice-failed" role="alert"><Chip label="FAILED" />
              <span>The draft wasn't written: {v.develop_run.message || "the step failed"}. Nothing was changed.</span>
              <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={develop}>Retry Develop</button></div>)}
          {v.draft && <DraftView id={id} dr={v.draft} drawers={drawers} act={act} busy={busy} />}
        </div>
      </main>
      <DrawerStack d={drawers} />
    </div>
  );
}

function RunningState({ v, act, busy }: { v: InvestigationView; act: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  return (
    <section className="card" aria-busy="true" aria-label="Investigation underway">
      <div className="card-head"><h2 className="subheading">Investigating {v.label.toLowerCase()} in {v.city}</h2><Chip label="RUNNING" /></div>
      <Skeleton width="70%" /><Skeleton width="55%" /><Skeleton width="62%" />
      <p className="small steel">Every tool call is in Activity as it happens. Stop keeps what was checked; Continue resumes it.</p>
      {v.run && <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api.cancel(v.run!.id))}>Stop</button>}
    </section>
  );
}

/** LF-ReadinessAxes: three axes, never blended; willingness is always unknown until a reply. */
export function ReadinessAxes({ r }: { r: Partial<Axes> & { overall: string; reasons: string[] } }) {
  return (
    <div className="stack" aria-label="Readiness">
      <div className="tag-chips"><Chip label={OVERALL[r.overall] || L(r.overall)} />
        {r.fit && <span className="small">FIT <Chip label={L(r.fit)} /></span>}
        {r.grounding && <span className="small">GROUNDING {r.grounding.identity !== "NA" && <Chip label={L(r.grounding.identity)} />}
          {r.grounding.locality !== "NA" && <Chip label={L(r.grounding.locality)} />}<Chip label={L(r.grounding.method_evidence)} /></span>}
        {r.operational && <span className="small">OPERATIONS {r.operational.contact_route !== "NA" && <Chip label={`CONTACT: ${L(r.operational.contact_route)}`} />}
          <Chip label="WILLINGNESS UNKNOWN" /></span>}
      </div>
      {r.reasons.length > 0 && <ul className="small">{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
    </div>
  );
}

function DossierBody({ p, v, d, drawers, act, busy }: {
  p: Project; v: InvestigationView; d: Dossier; drawers: Drawers; act: (fn: () => Promise<unknown>) => void; busy: boolean;
}) {
  const kept = d.candidates.filter((c) => c.decision !== "REJECTED");
  const rejected = d.candidates.filter((c) => c.decision === "REJECTED");
  const failed = (d.workspace?.attempts || []).filter((a) => a.outcome === "FAILED");
  return (
    <>
      {d.stale.map((x) => <div key={x} className="notice notice-changed" role="status"><Chip label="CHANGED" />
        <span>{x}. This investigation is kept and readable; Investigate further to refresh it.</span></div>)}
      <section className="card" aria-label="Summary">
        <div className="card-head"><h2 className="subheading">Where this stands</h2>
          <span className="mono steel">{d.prompt_version} · {d.check_version} · v{d.version}</span></div>
        <ReadinessAxes r={d.readiness} />
        <p><span className="label steel">NEXT</span> {d.next_action.text}</p>
        {d.concept.name && <div className="concept"><span className="label steel">CONCEPT · PROPOSAL</span><strong>{d.concept.name}</strong>
          <span className="small">{d.concept.line}</span></div>}
        {d.products.length > 0 && <div className="tag-chips"><span className="label steel">YOUR PRODUCTS</span>
          {d.products.map((x) => <button key={x.id} type="button" className="link small" onClick={() => drawers.push({ key: `pr-${x.id}`,
            kicker: "PRODUCT", render: () => <ProductDrawer pid={p.id} prid={x.id} version={x.version} /> })}>{x.title}</button>)}</div>}
      </section>
      {d.method === "retail" && <RetailBody d={d} p={p} drawers={drawers} act={act} busy={busy} />}
      {d.method === "popup" && <PopupBody d={d} p={p} drawers={drawers} act={act} busy={busy} />}
      {d.method === "creator" && <CreatorBody d={d} v={v} p={p} act={act} busy={busy} />}
      {d.method === "community" && <CommunityBody d={d} p={p} drawers={drawers} />}
      {d.method === "digital" && <DigitalBody id={p.id} d={d} />}
      {kept.length === 0 && d.method !== "digital" && (
        <div className="card empty-state"><h2 className="heading-sm">No {NOUN[d.method]} we could check fits yet</h2>
          <p>What was tried is listed below. Add one you know (your include list), or investigate further.</p></div>)}
      {d.candidates.filter((c) => c.override).map((c) => <div key={c.ref} className="notice notice-changed" role="status">
        <Chip label="YOU SAID" /><span>You included {c.name} although the checks say {L(c.reason_code).toLowerCase()}.</span></div>)}
      {rejected.length > 0 && <section className="card considered" aria-label="Rejected">
        <h3 className="subheading">Ruled out, with the reason</h3>
        {rejected.map((c) => <div key={c.ref} className="considered-row"><div className="tag-chips"><Chip label="REJECTED" />
          <Chip label={L(c.reason_code)} />{c.interpretation && <Chip label="INTERPRETATION" />}</div>
          <div><strong>{c.name}</strong>{c.reason && c.reason !== c.reason_code && <p className="small">{c.reason}</p>}
            {v.preferences && c.reason_code !== "FOUNDER_EXCLUDED" && !v.preferences.include.includes(c.name) &&
              <button type="button" className="link small" disabled={busy} onClick={() => act(() => api45.editPrefs(p.id, v.preferences!.workspace_id,
                v.preferences!.version, { include: [...v.preferences!.include, c.name] }))}>Include anyway in the next investigation</button>}</div></div>)}
      </section>}
      {v.preferences && <Preferences p={p} v={v} act={act} busy={busy} />}
      {failed.map((a, i) => <div key={i} className="notice notice-failed"><Chip label="CHECK FAILED" />
        <span>Couldn't read {a.source_id ? "a page" : a.tool}: unknown, not absent.</span>
        <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={() => act(() => api45.investigate(p.id, d.city_id, d.method))}>Retry</button></div>)}
      <details className="card">
        <summary className="subheading">What was tried · {(d.workspace?.attempts || []).length} tool uses · {d.missing.length} not found</summary>
        <ul className="act-list">{(d.workspace?.attempts || []).map((a, i) => <li key={i}><Chip label={a.outcome === "OK" ? "DONE" : a.outcome === "EMPTY" ? "EMPTY" : a.outcome === "REFUSED" ? "SKIPPED" : "FAILED"} />
          <div><strong>{a.tool}</strong>{a.outcome === "EMPTY" && <div className="small">None found by this search (not "none exist")</div>}</div></li>)}</ul>
        {d.missing.map((m) => <p key={m.text} className="small"><Chip label="NOT KNOWN" /> {m.text}</p>)}
        {d.faults.length > 0 && <p className="small steel">Left out by the checks: {d.faults.map((f) => f.reason).join(" · ")}</p>}
      </details>
    </>
  );
}

/** Your include and exclude lists for this method in this city (YOU SAID), kept for the next investigation. */
function Preferences({ p, v, act, busy }: { p: Project; v: InvestigationView; act: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const pr = v.preferences!;
  const [inc, setInc] = useState(pr.include.join(", "));
  const [exc, setExc] = useState(pr.exclude.join(", "));
  const list = (t: string) => t.split(",").map((x) => x.trim()).filter(Boolean);
  return (
    <form className="card stack" aria-label="Your preferences" onSubmit={(e) => { e.preventDefault();
      act(() => api45.editPrefs(p.id, pr.workspace_id, pr.version, { include: list(inc), exclude: list(exc) })); }}>
      <h3 className="subheading">Your preferences for the next investigation <Chip label="YOU SAID" /></h3>
      <label className="small" htmlFor="pref-include">Always consider (comma-separated names)</label>
      <input id="pref-include" className="input" value={inc} onChange={(e) => setInc(e.target.value)} />
      <label className="small" htmlFor="pref-exclude">Never consider</label>
      <input id="pref-exclude" className="input" value={exc} onChange={(e) => setExc(e.target.value)} />
      <button type="submit" className="btn-secondary" disabled={busy}>Save preferences</button>
      <p className="small steel">Nothing re-runs. Investigate further uses these.</p>
    </form>
  );
}

function Claimish({ label, sid, cid, drawers }: { label: string; sid?: string | null; cid?: string; drawers: Drawers }) {
  return sid && cid ? <Chip label={label} onClick={() => openEvidence(drawers, sid, cid)} title="Open the evidence" /> : <Chip label={label} />;
}

/** LF-CandidateTable: stockist comparison. */
function RetailBody({ d, p, drawers, act, busy }: { d: Dossier; p: Project; drawers: Drawers; act: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const r = d.retail;
  return (
    <section className="card" aria-label="Stockist comparison">
      <div className="card-head"><h2 className="subheading">Stockist comparison</h2><span className="small steel">Carrying other brands isn't agreeing to carry yours.</span></div>
      {r.partnership_hypothesis && <p className="small"><Chip label="HYPOTHESIS" /> {r.partnership_hypothesis}</p>}
      <div className="matrix-scroll"><table className="matrix">
        <thead><tr><th scope="col">Shop</th><th scope="col">Identity</th><th scope="col">Distance</th><th scope="col">Outside brands seen</th>
          <th scope="col">Category</th><th scope="col">Route</th><th scope="col">Readiness</th></tr></thead>
        <tbody>{r.shortlist.map((s: any) => (  // eslint-disable-line @typescript-eslint/no-explicit-any
          <tr key={s.lead_key}>
            <th scope="row">{s.lead_id ? <button type="button" className="link" onClick={() => openPartner(drawers, p, s.lead_id)}>{s.name}</button> : s.name}</th>
            <td><Chip label={L(s.axes.grounding.identity)} /></td>
            <td className="small">{s.distance_km != null ? `≈ ${s.distance_km} km` : <Chip label={L(s.location?.state || "UNPLACED")} />}</td>
            <td className="small">{s.assortment_evidence.length ? s.assortment_evidence.slice(0, 4).map((e: any) => e.brand_or_category).join(", ") : <Chip label="NOT FOUND" />}</td>  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
            <td><Chip label={s.category_overlap ? L(s.category_overlap) : "NOT KNOWN"} /></td>
            <td><Chip label={L(s.stockist_route)} /></td>
            <td><Chip label={OVERALL[s.axes.overall] || L(s.axes.overall)} /></td>
          </tr>))}</tbody></table></div>
      {r.possible_activation && <p className="small"><Chip label="PROPOSAL" /> {r.possible_activation}</p>}
      <div className="stack">{r.commercial_questions.map((q: any) => <div key={q.text} className="row-inline"><Chip label="UNKNOWN" /><span className="small">{q.text}</span></div>)}</div>  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
      {d.proximity && (d.proximity.placed.length > 0 || d.proximity.unplaced.length > 0) &&
        <ProximityView px={d.proximity} onChoose={chooser(d, p, act, busy)} />}
    </section>
  );
}

/** One location-choice action for every method's AMBIGUOUS leads (P45-I9). */
function chooser(d: Dossier, p: Project, act: (fn: () => Promise<unknown>) => void, busy: boolean) {
  return (leadKey: string, option: number) => {
    const c = d.candidates.find((x) => x.ref === leadKey);
    const b = d[d.method as "retail"] || {};
    const row = [...(b.shortlist || []), ...(b.venues || []), ...(b.groups || [])].find((x: { lead_key: string }) => x.lead_key === leadKey);
    if (c?.lead_id && row?.location && !busy) act(() => api45.chooseLocation(p.id, c.lead_id!, row.location.version || 1, option));
  };
}

/** LF-ProximityView: placed points around the city point on an OpenStreetMap basemap; straight-line distances only. */
function ProximityView({ px, onChoose }: { px: Proximity; onChoose?: (leadKey: string, option: number) => void }) {
  const [sel, setSel] = useState<string | null>(null);
  const [failed, setFailed] = useState(() => typeof navigator !== "undefined" && navigator.onLine === false);
  return (
    <div className="stack" aria-label="Proximity">
      <h3 className="subheading">Where they are</h3>
      <p className="small steel">{failed ? "Positions from Qloo, drawn to scale: the street map couldn't load." : "Positions from Qloo."} {noCaption(px.caveat)}</p>
      {px.placed.length > 0 && (failed ? <Schematic px={px} sel={sel} /> : <StreetMap px={px} sel={sel} onFail={() => setFailed(true)} />)}
      <ul className="plain">{px.distances.map((x) => {
        const pt = px.placed.find((y) => y.lead_key === x.lead_key)!;
        return <li key={x.lead_key}><button type="button" className="link" aria-pressed={sel === x.lead_key}
          onClick={() => setSel(sel === x.lead_key ? null : x.lead_key)}>{pt.name}</button>
          <span className="small"> · {x.label} from {x.from} · {pt.address || "no address"} · <Chip label={L(pt.source)} /></span></li>;
      })}</ul>
      {px.unplaced.map((u) => <div key={u.lead_key} className="row-inline"><Chip label={L(u.state)} /><span className="small">{u.name}: {u.reason}</span>
        {u.state === "AMBIGUOUS" && onChoose && (u.options || []).map((o, i) => <button key={i} type="button" className="btn-secondary"
          onClick={() => onChoose(u.lead_key, i)}>{o.address || o.name}</button>)}</div>)}
    </div>
  );
}

// Web Mercator ("slippy map") pixels at zoom z: the tile grid OpenStreetMap serves.
const TILE = 256;
const wx = (lon: number, z: number) => ((lon + 180) / 360) * TILE * 2 ** z;
const wy = (lat: number, z: number) => { const s = Math.sin((lat * Math.PI) / 180); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * TILE * 2 ** z; };
const NICE_M = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000];

/** OSM raster tiles as <img>, only the ones the box covers, loaded normally by the browser; nothing while the box is hidden
 * (zero width, e.g. inside a closed "How we looked"). Any tile error hands back to the schematic. */
function StreetMap({ px, sel, onFail, cells }: { px: Proximity; sel: string | null; onFail: () => void; cells?: QlooCell[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const h = Math.round(Math.min(340, Math.max(240, w * 0.5)));
  const pts = [px.center, ...px.placed, ...(cells ?? [])];
  const pad = 44;
  const span = (f: (p: { lat: number; lon: number }) => number) => { const v = pts.map(f); return Math.max(...v) - Math.min(...v); };
  let z = 17;
  while (z > 3 && (span((p) => wx(p.lon, z)) > w - 2 * pad - 80 || span((p) => wy(p.lat, z)) > h - 2 * pad)) z--;
  const xs = pts.map((p) => wx(p.lon, z)), ys = pts.map((p) => wy(p.lat, z));
  const ox = (Math.min(...xs) + Math.max(...xs)) / 2 - w / 2, oy = (Math.min(...ys) + Math.max(...ys)) / 2 - h / 2;
  const n = 2 ** z;
  const tiles: { x: number; y: number; src: string }[] = [];
  if (w > 0) for (let ty = Math.floor(oy / TILE); ty <= Math.floor((oy + h - 1) / TILE); ty++) {
    if (ty < 0 || ty >= n) continue;
    for (let tx = Math.floor(ox / TILE); tx <= Math.floor((ox + w - 1) / TILE); tx++)
      tiles.push({ x: tx * TILE - ox, y: ty * TILE - oy, src: `https://tile.openstreetmap.org/${z}/${((tx % n) + n) % n}/${ty}.png` });
  }
  const mpp = (156543.03392 * Math.cos((px.center.lat * Math.PI) / 180)) / n;
  const bar = [...NICE_M].reverse().find((m) => m / mpp <= 120) ?? NICE_M[0];
  const at = (p: { lat: number; lon: number }) => [wx(p.lon, z) - ox, wy(p.lat, z) - oy];
  const [ccx, ccy] = at(px.center);
  const cell = Math.max(10, 153 / mpp);  // a 7-character geohash cell is about 153 m across
  const amin = Math.min(...(cells ?? []).map((c) => c.affinity)), amax = Math.max(...(cells ?? []).map((c) => c.affinity));
  return (
    <div ref={ref} className="dt-map" style={{ height: h }} role="img"
      aria-label={cells?.length ? "Street map of Qloo taste concentration and places" : "Street map of placed locations"}>
      {tiles.map((t) => <img key={t.src} src={t.src} alt="" draggable={false} onError={onFail} style={{ left: t.x, top: t.y, width: TILE, height: TILE }} />)}
      {w > 0 && <svg width={w} height={h} aria-hidden="true">
        {(cells ?? []).map((c) => { const [x, y] = at(c);  // shade by position inside this one Qloo request only
          return <rect key={`${c.lat},${c.lon}`} x={x - cell / 2} y={y - cell / 2} width={cell} height={cell} rx="2"
            style={{ fill: "var(--lf-marigold-deep)" }} opacity={0.3 + 0.45 * (amax > amin ? (c.affinity - amin) / (amax - amin) : 1)} />; })}
        <circle cx={ccx} cy={ccy} r="5" className="dt-center" /><text x={ccx + 9} y={ccy + 4} className="dt-label">city center</text>
        {px.placed.map((x) => { const [cx, cy] = at(x); const left = cx > w - 150;
          return <g key={x.lead_key} opacity={sel && sel !== x.lead_key ? 0.4 : 1}><circle cx={cx} cy={cy} r={sel === x.lead_key ? 8 : 6} className="dt-dot" />
            <text x={left ? cx - 10 : cx + 10} y={cy + 4} textAnchor={left ? "end" : "start"} className="dt-label">{x.name}</text></g>; })}
        <line x1="12" y1={h - 14} x2={12 + bar / mpp} y2={h - 14} className="dt-bar" />
        <text x="12" y={h - 20} className="dt-label">{bar >= 1000 ? `${bar / 1000} km` : `${bar} m`}</text>
      </svg>}
      <a className="dt-attr" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer noopener">© OpenStreetMap contributors</a>
    </div>
  );
}

/** The fallback when tiles can't load: the same points to scale on a blank square. */
function Schematic({ px, sel }: { px: Proximity; sel: string | null }) {
  const pts = px.placed.map((x) => {
    const kx = 111.32 * Math.cos((px.center.lat * Math.PI) / 180);
    return { ...x, dx: (x.lon - px.center.lon) * kx, dy: (x.lat - px.center.lat) * 110.57 };
  });
  const span = Math.max(2, ...pts.map((x) => Math.max(Math.abs(x.dx), Math.abs(x.dy)))) * 1.25;
  const sx = (v: number) => 150 + (v / span) * 130;
  const sy = (v: number) => 150 - (v / span) * 130;
  const scale = span >= 10 ? 5 : 1;
  return (
    <svg viewBox="0 0 300 300" width="100%" style={{ maxWidth: 360 }} role="img" aria-label="Schematic of placed locations" overflow="visible">
      <rect x="0" y="0" width="300" height="300" fill="none" stroke="currentColor" opacity="0.15" />
      <circle cx="150" cy="150" r="4" fill="none" stroke="currentColor" /><text x="156" y="146" fontSize="9">city center</text>
      {pts.map((x) => <g key={x.lead_key}><circle cx={sx(x.dx)} cy={sy(x.dy)} r={sel === x.lead_key ? 7 : 5}
        fill="currentColor" opacity={sel && sel !== x.lead_key ? 0.35 : 0.9} /><text x={sx(x.dx) + 8} y={sy(x.dy) + 3} fontSize="9">{x.name}</text></g>)}
      <line x1="20" y1="285" x2={20 + (scale / span) * 130} y2="285" stroke="currentColor" /><text x="20" y="278" fontSize="9">{scale} km</text>
    </svg>
  );
}

function PopupBody({ d, p, drawers, act, busy }: { d: Dossier; p: Project; drawers: Drawers; act: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const b = d.popup;
  const choose = chooser(d, p, act, busy);
  return (
    <section className="card" aria-label="Venues">
      <div className="card-head"><h2 className="subheading">Venues and what it would take</h2></div>
      {(b.activation_outline.format || b.activation_outline.duration) && <p className="small"><Chip label="PROPOSAL" />
        {[b.activation_outline.format, b.activation_outline.duration, b.activation_outline.footprint].filter(Boolean).join(" · ")}</p>}
      {b.venues.map((x: any) => (  // eslint-disable-line @typescript-eslint/no-explicit-any
        <div key={x.lead_key} className="partner-row">
          {x.lead_id ? <button type="button" className="link" onClick={() => openPartner(drawers, p, x.lead_id)}>{x.name}</button> : <strong>{x.name}</strong>}
          {x.event_use.map((u: any, i: number) => <span key={i} className="small"><Claimish label={L(u.kind)} sid={u.source_id} cid={u.claim_id} drawers={drawers} />
            {u.event_dates.map((e: any) => <span key={e.text}> {e.text} <Chip label={L(e.when)} /></span>)}</span>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
          <span className="small">capacity {x.capacity ? x.capacity.value : <Chip label="NOT KNOWN" />}</span>
          <Chip label={OVERALL[x.axes.overall] || L(x.axes.overall)} />
        </div>))}
      <p className="small steel">A past event is not availability. Being near each other is logistics, never fit.</p>
      <div className="stack"><h3 className="subheading">Requirements</h3>
        {b.requirements.map((r: any) => <div key={r.item} className="row-inline"><Chip label={L(r.state)} /><span className="small">{L(r.item).toLowerCase()}</span></div>)}</div>  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
      {d.proximity && <ProximityView px={d.proximity} onChoose={choose} />}
    </section>
  );
}

/** "Add a creator profile" (YOU SAID): canonicalised and never fetched; the next creator investigation checks it. */
function AddCreator({ p, city, v, act, busy }: { p: Project; city: string; v: InvestigationView; act: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const [url, setUrl] = useState("");
  return (
    <div className="stack">
      {v.founder_creators.map((x) => <p key={x.id} className="small"><Chip label="YOU SAID" /> {x.handle} · {x.platform} · used in the next investigation</p>)}
      <form className="search-row" onSubmit={(e) => { e.preventDefault(); if (url) act(async () => { await api45.addCreator(p.id, city, url); setUrl(""); }); }}>
        <label className="small" htmlFor="add-creator">Add a creator profile you know</label>
        <input id="add-creator" className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.tiktok.com/@handle" />
        <button type="submit" className="btn-secondary" disabled={busy || !url}>Add</button>
      </form>
    </div>
  );
}

function CreatorBody({ d, v, p, act, busy }: { d: Dossier; v: InvestigationView; p: Project; act: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const b = d.creator;
  return (
    <section className="card" aria-label="Creators">
      <div className="card-head"><h2 className="subheading">Creators</h2><span className="small steel">Where they are, what they make and who watches are kept apart.</span></div>
      {b.collaboration_concepts.map((c: string) => <p key={c} className="small"><Chip label="PROPOSAL" /> {c}</p>)}
      {b.creators.map((x: any) => {  // eslint-disable-line @typescript-eslint/no-explicit-any
        const c = x.creator;
        return (
          <article key={x.lead_key} className="card" aria-label={x.name}>
            <div className="tag-chips"><strong>{c.display_name}</strong>
              <a className="link small" href={c.canonical_url} target="_blank" rel="noreferrer">{c.handle} · {c.platform} ↗</a>
              <Chip label={L(c.identity)} /><span className="small">location <Chip label={L(c.location.state)} /> {c.location.text || ""}</span></div>
            {x.content_evidence.map((e: any) => <p key={e.url + e.theme} className="small"><Chip label="OBSERVED" /> “{e.theme}” · {e.title ? `${e.title} · ` : ""}{e.url ? pathOf(e.url) : ""}</p>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
            {c.metrics.map((m: any) => <p key={m.name} className="small">{m.name} {m.value} · {m.source} · seen {m.observed_at}{m.note ? ` (${m.note})` : ""}</p>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
            {c.unused_figures.map((m: any) => <p key={m.quote} className="small steel">A page says “{m.quote}”, undated; not used.</p>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
            <div className="tag-chips"><span className="small">Audience location <Chip label="UNKNOWN" /></span><span className="small">Fee <Chip label="UNKNOWN" /></span>
              <span className="small">Contact <Chip label={L(x.contact_route)} /></span></div>
          </article>);
      })}
      <AddCreator p={p} city={d.city_id} v={v} act={act} busy={busy} />
      <div className="stack">{b.outstanding.map((o: string) => <div key={o} className="row-inline"><Chip label="UNKNOWN" /><span className="small">{o}</span></div>)}</div>
    </section>
  );
}

function CommunityBody({ d, p, drawers }: { d: Dossier; p: Project; drawers: Drawers }) {
  const b = d.community;
  return (
    <section className="card" aria-label="Organizers and activities">
      <div className="card-head"><h2 className="subheading">Organizers and their activities</h2></div>
      {b.concept && <p className="small"><Chip label="PROPOSAL" /> {b.concept}</p>}
      {b.groups.map((g: any) => (  // eslint-disable-line @typescript-eslint/no-explicit-any
        <div key={g.lead_key} className="stack">
          <div className="tag-chips">{g.lead_id ? <button type="button" className="link" onClick={() => openPartner(drawers, p, g.lead_id)}>{g.name}</button> : <strong>{g.name}</strong>}
            <Chip label={g.role} /><Chip label={OVERALL[g.axes.overall] || L(g.axes.overall)} /></div>
          {g.activities.map((a: any) => (  // eslint-disable-line @typescript-eslint/no-explicit-any
            <div key={a.claim_id} className="row-inline"><Claimish label={L(a.recurrence)} sid={a.source_id} cid={a.claim_id} drawers={drawers} />
              <span className="small">{a.text}{a.schedule_quote ? ` · “${a.schedule_quote}”` : ""}{a.next_date ? ` · next ${a.next_date}` : ""}
                {a.possibly_stale ? " · possibly stale" : ""}</span>
              {a.event_dates.map((e: any) => <span key={e.text} className="small" style={{ opacity: e.when === "PAST" ? 0.6 : 1 }}> {e.text} <Chip label={L(e.when)} /></span>)}</div>))}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
        </div>))}
      {b.participation_assumptions.map((a: any) => <p key={a.text} className="small"><Chip label="ASSUMED" /> {a.text}</p>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
    </section>
  );
}

function DigitalBody({ id, d }: { id: string; d: Dossier }) {
  const b = d.digital;
  return (
    <section className="card" aria-label="Audience, message, channel, measurement">
      <div className="card-head"><h2 className="subheading">Audience → message → channel → measurement</h2>
        {b.brand_facts_only && <Chip label="INTERPRETATION" title="Built from brand facts only: no audiences were offered" />}</div>
      <div className="built-grid">
        <div><span className="label steel">AUDIENCE</span>{b.targeting.map((t: any) => <p key={t.element} className="small">{t.element} <Chip label={L(t.basis)} /></p>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
          {noCaption(b.caveat) && <p className="small steel">{noCaption(b.caveat)}</p>}</div>
        <div><span className="label steel">MESSAGE</span><p className="small">{b.message.line || <Chip label="NOT KNOWN" />}</p></div>
        <div><span className="label steel">CHANNEL</span><p className="small">{b.channel.primary}{b.channel.alternative ? ` (or ${b.channel.alternative})` : ""}</p>
          <p className="small">{L(b.objective).toLowerCase()} · {b.geography.radius_km_hypothesis ? `city + ${Math.round((b.geography.radius_km_hypothesis / 1.609344) * 10) / 10} mi` : "city"}</p></div>
        <div><span className="label steel">MEASUREMENT</span><p className="small">{b.measurement.primary_metric}</p>
          {b.measurement.stop_rule && <p className="small"><Chip label="HYPOTHESIS" /> {b.measurement.stop_rule}</p>}
          <PlanFigures id={id} city={d.city_id} method="digital" /></div>
      </div>
    </section>
  );
}

/** S5-21, S5-22: this method's money as the current plan holds it (the scenario for digital); never figures of its own. */
function PlanFigures({ id, city, method, lines }: { id: string; city: string; method: string; lines?: boolean }) {
  const { data: v } = useLive(id, (pid) => api65.plan(pid, city));
  if (!v) return null;
  const m = v.methods.find((x) => x.method === method);
  const mine = v.money?.lines.filter((l) => l.used_by.includes(method)) ?? [];
  const edit = <button type="button" className="link" onClick={() => navigate(`/p/${id}/plan/${city}`)}>Edit in your plan</button>;
  if (!lines) {
    const s = v.scenario;
    if (!s?.total_daily) return <p className="small steel">No scenario yet: add digital to your plan and set a daily budget there. {edit}</p>;
    const plats = s.results?.platforms.map((r) => `${r.allocation_text} on ${r.label}`).join(", ");
    const imp = s.results?.aggregate.impressions;
    return <p className="small"><Chip label="SCENARIO" /> {s.media_spend && `${money(s.media_spend)} over ${s.horizon_days} days`} ({plats}).
      {imp ? ` ${imp.low.toLocaleString()}–${imp.high.toLocaleString()} impressions.` : ` ${s.results?.note ?? "No rates yet"}.`} {edit}</p>;
  }
  if (!m?.money) return <p className="small steel">Not costed yet: add this method to your plan. {edit}</p>;
  return <>{mine.map((l) => <div key={l.id} className="row-inline"><Chip label={L(l.basis)} /><span className="small">{l.label}{l.text ? `: ${l.text}` : ""}{l.shared ? " · shared" : ""}</span></div>)}
    <p className="small">{m.money.text} {edit}</p></>;
}

function money(m: { minor: number; ccy: string }) {
  return `${m.ccy} ${(m.minor / 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function DraftView({ id, dr, drawers, act, busy }: { id: string; dr: Draft; drawers: Drawers; act: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const [measure, setMeasure] = useState(dr.success_measure);
  return (
    <section className="card" aria-label="Developed draft">
      <div className="card-head"><h2 className="subheading">Developed draft</h2><Chip label={dr.label} /><span className="mono steel">v{dr.version}</span></div>
      {dr.stale.map((x) => <div key={x} className="notice notice-changed" role="status"><Chip label="CHANGED" /><span>Built on research that changed: {x}.</span></div>)}
      <div className="concept"><span className="label steel">CONCEPT · PROPOSAL</span><strong>{dr.concept.name}</strong><span className="small">{dr.concept.line}</span></div>
      <ol className="small">{dr.activities.map((a) => <li key={a}>{a}</li>)}</ol>
      <p className="small"><Chip label="HYPOTHESIS" /> {dr.audience_hypothesis.text}{noCaption(dr.audience_hypothesis.caveat) ? ` (${noCaption(dr.audience_hypothesis.caveat)})` : ""}</p>
      {dr.partners.map((x) => <p key={x.name} className="small">{x.name} <Chip label="NOT CONFIRMED" title="Participation not confirmed" /></p>)}
      {dr.products.map((x) => <p key={x.id} className="small"><button type="button" className="link" onClick={() => drawers.push({
        key: `pr-${x.id}`, kicker: "PRODUCT", render: () => <ProductDrawer pid={id} prid={x.id} version={x.version} /> })}>{x.title}</button></p>)}
      <form className="search-row" onSubmit={(e) => { e.preventDefault(); act(() => api45.editDraft(id, dr.id, dr.version, { success_measure: measure })); }}>
        <label className="small" htmlFor="measure">Success measure {dr.you_said?.includes("success_measure") && <Chip label="YOU SAID" />}</label>
        <input id="measure" className="input" value={measure} onChange={(e) => setMeasure(e.target.value)} />
        <button type="submit" className="btn-secondary" disabled={busy || measure === dr.success_measure}>Save</button>
      </form>
      <div className="stack"><h3 className="subheading">To validate before you commit</h3>
        {dr.validation_tasks.map((t) => <div key={t.text} className="row-inline"><Chip label="TO CHECK" /><span className="small">{t.text}</span></div>)}</div>
      <div className="stack"><h3 className="subheading">What it will cost</h3>
        <PlanFigures id={id} city={dr.source.city_id} method={dr.source.method} lines /></div>
      <div className="btn-row">
        {dr.package ? <button type="button" className="btn-primary" onClick={() => navigate(`/p/${id}/deliver/${dr.package!.id}`)}>Open the delivery package</button>
          : <button type="button" className="btn-primary" disabled={busy} onClick={() => act(async () => {
            const r = await api6.prepare(id, dr.id, dr.version);
            navigate(`/p/${id}/deliver/${r.package_id}`);
          })}>{dr.source.method === "digital" ? "Prepare campaign" : "Prepare outreach"}</button>}
        {dr.package && <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(async () => {
          const r = await api6.prepare(id, dr.id, dr.version);
          navigate(`/p/${id}/deliver/${r.package_id}`);
        })}>Prepare again from this draft</button>}
      </div>
      {dr.digital_outline && <p className="small mono">{dr.digital_outline.channel.primary} · {L(dr.digital_outline.objective).toLowerCase()} · {dr.digital_outline.currency} ·
        horizon {dr.digital_outline.horizon_days.value} days ({dr.digital_outline.horizon_days.state.toLowerCase()})</p>}
    </section>
  );
}


// ---- v4 09 / B05–B08: the method detail inside the plan (read-only specialist layout, one plan action) ----
type Act = (fn: () => Promise<unknown>) => Promise<boolean>;
const PLURAL: Record<string, string> = { retail: "shops", popup: "venues", creator: "creators", community: "groups", digital: "channels" };
const lower = (x: string) => x.charAt(0).toLowerCase() + x.slice(1);
type Row = { chip: string; text: string; open?: () => void; href?: string };

/** Each open requirement as the question a founder asks (q), its word on a card (short), what in a drafted message counts as
 * asking it (re), and how it reads when it's the founder's own check (self). Order here is the order asked. */
const ASK: Record<string, { q: string; short: string; re?: RegExp; self?: string }> = {
  "shelf trial": { q: "Would you try a small shelf trial of our products?", short: "a shelf trial", re: /shelf|stock (?:us|our)|carry/i },
  "wholesale or consignment": { q: "Do you buy wholesale, or take stock on consignment?", short: "terms", re: /wholesale|consign/i },
  "minimum order": { q: "What's your minimum order?", short: "minimum order", re: /minimum/i },
  margin: { q: "What margin do you need?", short: "margin", re: /margin/i },
  exclusivity: { q: "Do you ask for exclusivity?", short: "exclusivity", re: /exclusiv/i },
  "payment terms": { q: "What are your payment terms?", short: "payment terms", re: /payment/i },
  "audience geography": { q: "Where is your audience, by city?", short: "audience location", re: /audience/i },
  fee: { q: "What do you charge for a post?", short: "rate", re: /\b(?:fee|rate|charge)s?\b/i },
  "usage rights": { q: "Can we reuse the content, and for how long?", short: "reuse rights", re: /reuse|usage rights/i },
  availability: { q: "", short: "dates", re: /availab|\bdates?\b|timing|free on/i },
  "hire fee": { q: "What does a weekend hire cost, and is there a deposit?", short: "price", re: /\b(?:cost|price|fee|rate|deposit)s?\b/i },
  capacity: { q: "What's the capacity?", short: "size", re: /capacit|how many|\bsize\b/i },
  insurance: { q: "What insurance do you need from us?", short: "insurance", re: /insur/i },
  staffing: { q: "Do you provide staff, or do we bring our own?", short: "staffing", re: /\bstaff/i },
  "power fixtures": { q: "Is there power, and are tables or fixtures included?", short: "power and fixtures", re: /power|fixture/i },
  "load in": { q: "When and where can we load in?", short: "load-in", re: /load.?in|set.?up/i },
  permits: { q: "Do we need a permit to sell there, or do you hold one?", short: "permits", re: /permit/i, self: "whether you need a permit to sell there" },
  "stock import": { q: "How does stock get to you?", short: "stock import", self: "import and labeling rules for your stock" },
  "ad-disclosure duty": { q: "How do you mark paid posts?", short: "ad disclosure", re: /disclos/i },
  "would the organizer welcome a brand partner": { q: "What do you run, and would you welcome a brand partner at a meet-up?",
    short: "their meet-ups and a brand partner", re: /partner|sponsor/i },
  "exact interest names, checked in each ad platform": { q: "Check the exact interest names in each ad platform", short: "interest names" },
};
const ORDER = Object.keys(ASK);
type Msg = { partner: string | null; body: string; start: string | null } | null;
type Ask = { key: string; q: string; short: string; inMsg: boolean };
type Asks = { partner: string | null; ask: Ask[]; check: Ask[]; known: Row[]; msg: Msg };

/** Turns the dossier's open requirements into questions for the first proposed partner (unknown means not asked yet, not
 * absent); TO_CHECK items are the founder's own checks; anything already established keeps its state. */
function asksOf(d: Dossier, v: InvestigationView): Asks {
  const b = d[d.method as "retail"] || {};
  const msg = (d as Dossier & { message?: Msg }).message ?? null;
  const names: string[] = d.method === "digital" ? [] : (b.shortlist ?? b.venues ?? b.creators ?? b.groups ?? []).map((x: { name: string }) => x.name);
  const partner = (v.draft?.partners ?? []).map((x) => x.name).find((n) => names.includes(n)) ?? names[0] ?? null;
  const when = msg?.start ? ` in ${new Date(msg.start).toLocaleDateString("en-GB", { month: "long" })}` : "";
  const raw: { text: string; state: string }[] = [
    ...(d.method === "retail" ? [{ text: "shelf trial", state: "UNKNOWN" }] : []),
    ...(d.method === "popup" ? [...(b.requirements ?? []).map((r: { item: string; state: string }) => ({ text: r.item, state: r.state })),
      { text: "hire fee", state: "UNKNOWN" }] : []),
    ...(d.workspace?.open_questions ?? []), ...d.dependencies, ...(b.inquiry_questions ?? []).map((t: string) => ({ text: t, state: "UNKNOWN" }))];
  const seen = new Set<string>();
  const items = raw.map((x) => ({ ...x, key: x.text.toLowerCase().replace(/_/g, " ").replace(/\?$/, "").trim() }))
    .filter((x) => !seen.has(x.key) && !!seen.add(x.key))
    .sort((a, c) => (ORDER.indexOf(a.key) + 1 || 99) - (ORDER.indexOf(c.key) + 1 || 99));
  const toAsk = (x: { key: string; text: string }, self = false): Ask => {
    const a = ASK[x.key];
    const q = x.key === "availability" ? (d.method === "popup" ? `Is it free on a weekend${when}?` : when ? `Are you free${when}?` : "When are you free for a collaboration?")
      : (self ? a?.self : a?.q) ?? x.text.charAt(0).toUpperCase() + x.text.slice(1);
    return { key: x.key, q, short: a?.short ?? lower(x.text.replace(/\?$/, "")), inMsg: !!(msg && a?.re?.test(msg.body)) };
  };
  return { partner, msg,
    ask: items.filter((x) => x.state === "UNKNOWN").map((x) => toAsk(x)),
    check: items.filter((x) => x.state === "TO_CHECK" || x.state === "NOT_CHECKED").map((x) => toAsk(x, true)),
    known: items.filter((x) => !["UNKNOWN", "TO_CHECK", "NOT_CHECKED"].includes(x.state)).map((x) => ({ chip: L(x.state), text: toAsk(x).short })) };
}

function MethodDetail({ id, city, method }: { id: string; city: string; method: string }) {
  const { project: p, reload: reloadProject } = useProject(id);
  const { data: v, error, reload } = useLive(id, (pid) => api45.investigation(pid, city, method));
  const { data: pv, reload: reloadPlan } = useLive(id, (pid) => api65.plan(pid, city));
  const drawers = useDrawers();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<"add" | "remove" | null>(null);
  const act: Act = async (fn) => {
    setBusy(true); setErr(null);
    try { await fn(); reload(); reloadPlan(); reloadProject(); return true; } catch (e) { setErr((e as Error).message); return false; } finally { setBusy(false); }
  };
  if (error && !v) return <div className="app"><main className="page"><div className="error-panel card">{error} <Link className="link" to={`/p/${id}/plan/${city}`}>Back to the plan</Link></div></main></div>;
  if (!p || !v) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const d = v.dossier;
  const running = v.status === "queued" || v.status === "running";
  const m = pv?.methods.find((x) => x.method === method);
  const shown = pv && m ? shownRole(pv, m) : null;
  const included = !!m?.role && ["MAIN", "SUPPORTS"].includes(m.role.role);
  const investigate = () => act(() => api45.investigate(id, city, method));
  const ak = d && asksOf(d, v);
  // The banner is always an action: the specialist's own ask when evidenced, else the first questions for the partner;
  // never a readiness reason ("Fit is our interpretation…"), which stays on the cards and in How we looked.
  const next = shown?.role === "NOT_NOW" && shown.reason ? `Not now: ${lower(shown.reason)}`
    : d?.next_action.kind === "DEVELOP" && v.draft ? "The outline is drafted. Its costs are on the plan; nothing is spent."
    : d?.next_action.kind === "INQUIRE" || d?.next_action.kind === "DEVELOP" ? d.next_action.text
    : ak?.partner && ak.ask.length ? `Ask ${ak.partner} about ${ak.ask.slice(0, 2).map((a) => a.short).join(" and ")}.`
    : d?.next_action.text.startsWith("Confirm ") ? `${d.next_action.text}.`
    : d ? `Add a ${NOUN[d.method]} you know, or investigate again: both are under How we looked.` : undefined;
  const action = m && pv?.beachhead ? <button type="button" className="btn-secondary btn-lg" disabled={busy} onClick={() => { setErr(null); setPreview(included ? "remove" : "add"); }}>
    {included ? "Remove from plan" : "Add to plan"}</button> : undefined;
  return (
    <div className="app">
      <ProjectHeader brand={p.brief?.brand_name || p.site} site={p.site} intent={goalLine(p)} briefVersion={p.brief?.version}
        running={running} usage={usageLine(p)} demo={p.demo} onBrief={() => navigate(`/p/${id}`)}
        onActivity={() => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> })} />
      <StageNav stages={stagesFor(p)} current={2} />
      <ContextStrip p={p} onEdit={() => drawers.push({ key: "ctx", kicker: "YOUR GOAL", render: () => <ContextEdit p={p} reload={() => { reload(); reloadProject(); }} /> })} />
      <main id="main" className="pl-page">
        <div className="page page-tight"><Link to={`/p/${id}/plan/${city}`} className="link">← Back to the plan</Link></div>
        <DecisionBar kicker={["PLAN", v.city.toUpperCase(), v.label.toUpperCase(), shown && ROLE_LABEL[shown.role].toUpperCase()].filter(Boolean).join(" · ")}
          title={v.draft?.concept.name || d?.concept.name || `How ${v.label.toLowerCase()} could work in ${v.city}`}
          subtitle={v.draft?.concept.line || d?.concept.line || "A specialist looks for real candidates, checks each claim against its page, and says what is still unknown."}
          secondary={action} />
        <div className="page stack">
          {err && !preview && <p className="field-error" role="alert">{err}</p>}
          {next && !running && <div className="md-next" role="status"><b>Recommended next step.</b> {next}</div>}
          {v.ruled_by_note && <Notice kind="changed">Not run: your note rules this out (“{v.ruled_by_note}”). <Link className="link" to={`/p/${id}/opportunities/research`}>Edit your note</Link></Notice>}
          {!v.researched && <Notice kind="info">Research {v.city} first; the specialist builds on that research.</Notice>}
          {running && <RunningState v={v} act={(fn) => { act(fn); }} busy={busy} />}
          {v.can_continue && !running && <div className="notice notice-changed" role="status">
            <span>{v.run?.message || "Stopped."} What was checked is kept; what wasn't is unknown, not absent.</span>
            <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={() => act(() => api45.resume(id, city, method))}>Retry</button></div>}
          {d?.stale.length ? <Notice kind="changed">{d.stale.join(" · ")}. This investigation is kept and readable.</Notice> : null}
          {v.develop_run && ["failed", "partial"].includes(v.develop_run.status) &&
            <Notice kind="changed">The draft wasn't written: {v.develop_run.message || "the step failed"}. Prepare this plan drafts it again.</Notice>}
          {!d && !running && !v.ruled_by_note && v.researched && <div className="md-card">
            <b>Not investigated yet</b><span className="steel">An investigation reuses what the city research already read. Nobody is contacted.</span>
            <span><button type="button" className="btn-secondary" disabled={busy} onClick={investigate}>Investigate {v.label.toLowerCase()}</button></span></div>}
          {d && <MethodBody p={p} v={v} d={d} drawers={drawers} act={act} busy={busy} />}
        </div>
        {preview && pv && m && <ChangePreview v={pv} m={m} op={preview} busy={busy} err={err} onCancel={() => { setPreview(null); setErr(null); }}
          onConfirm={async () => { if (await act(() => changePlan(id, city, pv, m, preview))) setPreview(null); }} />}
      </main>
      <DrawerStack d={drawers} />
    </div>
  );
}

/** What we found · what supports it · what remains unknown · how we looked (v4 09). */
function MethodBody({ p, v, d, drawers, act, busy }: { p: Project; v: InvestigationView; d: Dossier; drawers: Drawers; act: Act; busy: boolean }) {
  const b = d[d.method as "retail"] || {};
  const rows: any[] = d.method === "retail" ? b.shortlist : d.method === "popup" ? b.venues : d.method === "creator" ? b.creators  // eslint-disable-line @typescript-eslint/no-explicit-any
    : d.method === "community" ? b.groups : [b.channel?.primary].filter(Boolean);
  const proposed = new Set((v.draft?.partners ?? []).map((x) => x.name));
  const rejected = d.candidates.filter((c) => c.decision === "REJECTED");
  const claimOf = (key: string) => d.candidates.find((c) => c.ref === key)?.claim_ids[0];
  const ev = (sid?: string | null, cid?: string | null) => (sid && cid ? () => openEvidence(drawers, sid, cid) : undefined);
  const leads: QlooLead[] = b.leads ?? [];
  const whyOf = (name: string) => (qlooOf(d).qloo_why ?? []).filter((w) => w.subject === name && w.about !== "lead").map((w) => w.text);
  const supports: Row[] = d.method === "retail" ? rows.flatMap((s) => s.assortment_evidence.slice(0, 1).map((e: any) =>  // eslint-disable-line @typescript-eslint/no-explicit-any
      ({ chip: "SOURCE", text: `${s.name} already carries ${e.brand_or_category}`, open: ev(e.source_id, claimOf(s.lead_key)) })))
    : d.method === "popup" ? rows.flatMap((x) => x.event_use.map((u: any) => ({ chip: "SOURCE", text: `${x.name}: ${L(u.kind).toLowerCase()}`, open: ev(u.source_id, u.claim_id) })))  // eslint-disable-line @typescript-eslint/no-explicit-any
    : d.method === "creator" ? rows.flatMap((x) => x.content_evidence.slice(0, 1).map((e: any) => ({ chip: "OBSERVED", text: e.theme.startsWith(x.name) ? e.theme : `${x.name}: ${e.theme}`, href: e.url })))  // eslint-disable-line @typescript-eslint/no-explicit-any
    : d.method === "community" ? rows.flatMap((g) => g.activities.map((a: any) => ({ chip: L(a.recurrence), text: `${g.name}: ${a.text}`, open: ev(a.source_id, a.claim_id) })))  // eslint-disable-line @typescript-eslint/no-explicit-any
    : b.message?.claim_ids?.length ? [{ chip: "SOURCE", text: `The message comes from your own site: “${b.message.line}”` }] : [];
  return (
    <>
      <section className="md-section" aria-label="What we found">
        <h2 className="label steel">What we found · {rows.length} {rows.length === 1 ? NOUN[d.method] : PLURAL[d.method]} found{proposed.size ? ` · ${proposed.size} proposed` : ""}
          {leads.length ? ` · ${leads.length} ${leads.length === 1 ? "lead" : "leads"} to confirm` : ""}</h2>
        {d.method === "retail" && <RetailCards p={p} d={d} rows={rows} proposed={proposed} drawers={drawers} ev={ev} claimOf={claimOf} />}
        {d.method === "popup" && <PopupCards p={p} d={d} rows={rows} proposed={proposed} drawers={drawers} ev={ev} />}
        {d.method === "creator" && <CreatorCards rows={rows} proposed={proposed} whyOf={whyOf} />}
        {d.method === "community" && <CommunityCards p={p} rows={rows} b={b} drawers={drawers} ev={ev} whyOf={whyOf} />}
        {d.method === "digital" && <DigitalFlow id={p.id} d={d} />}
        {rows.length === 0 && d.method !== "digital" && <div className="md-card"><b>No {NOUN[d.method]} we could check fits yet</b>
          <span className="steel">{leads.length ? "The leads below were named by a search or a page; none is confirmed yet, so none is used as evidence."
            : "What was tried is under How we looked. Add one you know there, or investigate again."}</span></div>}
        {leads.length > 0 && <LeadList leads={leads} />}
        {rejected.length > 0 && <p className="small steel">Rejected: {rejected.map((c) => `${c.name} (${L(c.reason_code).toLowerCase()})`).join("; ")}.</p>}
      </section>
      {(d.method === "community" || d.method === "creator") && <QlooSection d={d} />}
      {supports.length > 0 && <section className="md-section" aria-label="What supports it"><h2 className="label steel">What supports it</h2>
        <div className="md-list">{supports.map((r, i) => <div key={i} className="md-row"><Chip label={r.chip} /><span>{r.text}</span>
          {r.open ? <button type="button" className="link" onClick={r.open}>Evidence</button>
            : r.href ? <a className="link" href={r.href} target="_blank" rel="noreferrer noopener">Evidence</a> : <span />}</div>)}</div></section>}
      <AskSection d={d} ak={asksOf(d, v)} supports={supports} />
      <HowWeLooked p={p} v={v} d={d} drawers={drawers} act={act} busy={busy} />
    </>
  );
}

const known = (route?: string | null) => !!route && route !== "UNKNOWN";

/** A card's unknown fields as one line of things to ask, instead of a NOT KNOWN chip per field. */
function AskThem({ items }: { items: (string | false)[] }) {
  const xs = items.filter(Boolean);
  return xs.length ? <span className="md-q"><span className="steel">Ask them: </span>{xs.join(", ")}</span> : null;
}

/** Replaces "What remains unknown": what's established for the partner first (with its source), then the open requirements
 * as questions, the founder's own checks on one line, and whether the drafted message already asks them. */
function AskSection({ d, ak, supports }: { d: Dossier; ak: Asks; supports: Row[] }) {
  const heading = d.method === "digital" ? "Before you spend" : `Questions to ask ${ak.partner ?? `the ${NOUN[d.method]}`}`;
  const knownRows = [...supports.filter((r) => ak.partner && r.text.startsWith(ak.partner)).slice(0, 2), ...ak.known];
  const inMsg = ak.ask.filter((a) => a.inMsg).length;
  if (!ak.ask.length && !ak.check.length && !knownRows.length) return null;
  const to = ak.msg?.partner ? ` to ${ak.msg.partner}` : "";
  return (
    <section className="md-section" aria-label={heading}><h2 className="label steel">{heading}</h2>
      <div className="md-list">
        {knownRows.map((r, i) => <div key={`k${i}`} className="md-row md-row-2"><span>{r.text}</span><Chip label={r.chip} onClick={r.open} /></div>)}
        {ak.ask.length > 0 && <ol className="dt-asks">{ak.ask.map((a) => <li key={a.key}><span>{a.q}</span>{a.inMsg && <Chip label="IN YOUR MESSAGE" />}</li>)}</ol>}
        {ak.check.length > 0 && <div className="md-row md-row-2"><span><span className="steel">Check yourself: </span>{ak.check.map((a) => a.q).join("; ")}</span>
          <Chip label="TO CHECK" /></div>}
      </div>
      <p className="small steel">{d.method === "digital" ? "Not checked yet, not absent." : "Unknown here means not asked yet, not absent."}{" "}
        {ak.msg && ak.ask.length > 0 && (inMsg === ak.ask.length ? `These are in your message${to}.`
          : inMsg ? `${inMsg} of these ${inMsg === 1 ? "is" : "are"} in your message${to}; add the rest before you send it.`
          : `Your message${to} doesn't ask these yet; add them before you send it.`)}</p>
    </section>
  );
}

function RetailCards({ p, d, rows, proposed, drawers, ev, claimOf }: { p: Project; d: Dossier; rows: any[]; proposed: Set<string>; drawers: Drawers;  // eslint-disable-line @typescript-eslint/no-explicit-any
  ev: (s?: string | null, c?: string | null) => (() => void) | undefined; claimOf: (k: string) => string | undefined }) {
  const asks = (d.retail.commercial_questions ?? []).map((q: { text: string }) => ASK[q.text.toLowerCase()]?.short ?? q.text).slice(0, 2);
  return <div className="stack" style={{ gap: 12 }}>{rows.map((s) => {
    const e = s.assortment_evidence[0];
    return (
      <article key={s.lead_key} className={`md-card ${proposed.has(s.name) ? "md-proposed" : ""}`} aria-label={s.name}>
        <div className="md-card-head"><span><b>{s.lead_id ? <button type="button" className="link" onClick={() => openPartner(drawers, p, s.lead_id)}>{s.name}</button> : s.name}</b><br />
          <span className="small steel">{s.location?.address || "Not placed on a map"}{s.distance_km != null ? ` · ≈ ${s.distance_km} km from the city center` : ""}</span></span>
          {proposed.has(s.name) && <Chip label="PROPOSED" />}</div>
        <div className="md-c3">
          <div><span className="label steel">Who they are</span><span>{s.shop_categories?.length ? `A shop selling ${s.shop_categories.join(", ")}` : "Not established from its pages"}</span>
            <span><Chip label={L(s.axes.grounding.identity)} /></span></div>
          <div><span className="label steel">Why work with them</span>
            <span>{e ? `Already carries ${e.brand_or_category}: “${e.quote.length > 160 ? `${e.quote.slice(0, 160)}…` : e.quote}”` : "No outside brands seen on its pages"}</span>
            <span>{e ? <Chip label="SOURCE" onClick={ev(e.source_id, claimOf(s.lead_key))} /> : <Chip label="NOT FOUND" />}</span></div>
          <div><span className="label steel">Overlap with what you sell</span><span>{s.category_overlap ? L(s.category_overlap).toLowerCase() : "Not established"}</span>
            {s.category_overlap && <span><Chip label="INTERPRETATION" /></span>}</div>
        </div>
        <div className="md-foot">{known(s.stockist_route) && <span><span className="steel">Contact: </span>{L(s.stockist_route).toLowerCase()}</span>}
          <AskThem items={["a shelf trial", ...asks, !known(s.stockist_route) && "buyer contact"]} /></div>
      </article>);
  })}</div>;
}

function PopupCards({ p, d, rows, proposed, drawers, ev }: { p: Project; d: Dossier; rows: any[]; proposed: Set<string>; drawers: Drawers; ev: (s?: string | null, c?: string | null) => (() => void) | undefined }) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  return (
    <>
      {d.proximity && d.proximity.placed.length > 0 && <div className="md-card"><ProximityView px={d.proximity} /></div>}
      <div className="md-cards">{rows.map((x) => (
        <article key={x.lead_key} className={`md-card ${proposed.has(x.name) ? "md-proposed" : ""}`} aria-label={x.name}>
          <div className="md-card-head"><b>{x.lead_id ? <button type="button" className="link" onClick={() => openPartner(drawers, p, x.lead_id)}>{x.name}</button> : x.name}</b>
            {proposed.has(x.name) && <Chip label="PROPOSED" />}</div>
          {(x.location?.address || d.proximity) && <span className="small steel">{[x.location?.address,
            d.proximity?.distances.find((y) => y.lead_key === x.lead_key)?.label].filter(Boolean).join(" · ")}</span>}
          {x.event_use.map((u: any, i: number) => <span key={i}>{L(u.kind).charAt(0) + L(u.kind).slice(1).toLowerCase()} <Chip label="SOURCE" onClick={ev(u.source_id, u.claim_id)} />  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
            {u.event_dates.map((e: any) => <span key={e.text}><br />{e.text} <Chip label={L(e.when)} /></span>)}</span>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
          {x.capacity && <span>About {x.capacity.value} people</span>}
          {known(x.inquiry_route) && <span className="steel">Booking: {L(x.inquiry_route).toLowerCase()}</span>}
          <AskThem items={[!x.capacity && "size", !known(x.inquiry_route) && "booking"]} />
        </article>))}</div>
      <p className="small steel">A past event is not availability. Being near each other is logistics, never fit.</p>
    </>
  );
}

function CreatorCards({ rows, proposed, whyOf }: { rows: any[]; proposed: Set<string>; whyOf: (name: string) => string[] }) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  return <div className="md-cards">{rows.map((x) => {
    const c = x.creator;
    const read = x.content_evidence[0]?.observed_at;
    const followers = c.metrics.find((m: any) => /follow|subscri/i.test(m.name));  // eslint-disable-line @typescript-eslint/no-explicit-any
    return (
      <article key={x.lead_key} className="md-card" aria-label={x.name}>
        <div className="md-card-head"><b>{c.display_name}</b>{proposed.has(x.name) && <Chip label="PROPOSED" />}</div>
        <span className="steel">{c.platform.charAt(0).toUpperCase() + c.platform.slice(1)} · {followers ? `${followers.value} ${followers.name}` : c.handle}</span>
        {read && <span className="mono steel">{followers ? followers.source : "page"}, read {new Date(read).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</span>}
        <a className="md-hatch" href={c.canonical_url} target="_blank" rel="noreferrer noopener">Recent post · link out</a>
        <span>{c.location.text || "Unknown"} <Chip label={L(c.location.state)} /></span>
        {x.content_evidence.slice(0, 3).map((e: any) => <span key={e.url + e.theme} className="small"><span className="steel">Seen on: </span>{e.title || pathOf(e.url)}</span>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
        {whyOf(x.name).map((t) => <span key={t} className="small"><span className="steel">Why: Qloo · </span>{t}</span>)}
        <AskThem items={["rate", "audience location", "reuse rights"]} />
      </article>);
  })}</div>;
}

function CommunityCards({ p, rows, b, drawers, ev, whyOf }: { p: Project; rows: any[]; b: any; drawers: Drawers; ev: (s?: string | null, c?: string | null) => (() => void) | undefined;  // eslint-disable-line @typescript-eslint/no-explicit-any
  whyOf: (name: string) => string[] }) {
  return <div className="stack" style={{ gap: 12 }}>{rows.map((g) => (
    <article key={g.lead_key} className="md-card" aria-label={g.name}>
      <div className="md-c3">
        <div><span className="label steel">Organizer</span><b>{g.lead_id ? <button type="button" className="link" onClick={() => openPartner(drawers, p, g.lead_id)}>{g.name}</button> : g.name}</b>
          <span className="small steel">{g.role}{g.location?.address ? ` · ${g.location.address}` : ""}</span>
          {whyOf(g.name).map((t) => <span key={t} className="small"><span className="steel">Why: Qloo · </span>{t}</span>)}</div>
        {g.activities.slice(0, 2).map((a: any) => <div key={a.claim_id}><span className="label steel">Activity</span><b>{a.text}</b>  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
          <span><Chip label={L(a.recurrence)} onClick={ev(a.source_id, a.claim_id)} /> {a.next_date ? `Next ${a.next_date}` : ""}{a.possibly_stale ? " · possibly stale" : ""}</span>
          {a.event_dates.filter((e: any) => e.when === "PAST").length > 0 && <span className="small steel">Past dates: not counted</span>}</div>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
      </div>
      <div className="md-foot">{known(g.contact_route) && <span><span className="steel">Contact </span>{L(g.contact_route).toLowerCase()}</span>}
        <AskThem items={[g.activities.length ? "a brand partner" : "what they run and a brand partner", !known(g.contact_route) && "best contact"]} />
        {b.participation_assumptions.slice(0, 1).map((x: any) => <span key={x.text}><span className="steel">Participation </span>{x.text} <Chip label="ASSUMED" /></span>)}</div>  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
    </article>))}</div>;
}

/** Names a search or a page gave that no check confirmed yet: shown so the founder can confirm one, never used as evidence. */
function LeadList({ leads }: { leads: QlooLead[] }) {
  return (
    <div className="md-list" aria-label="Leads to confirm">{leads.map((l) => <div key={l.name} className="md-row">
      <Chip label="LEAD" />
      <span><b>{l.name}</b> · {l.found}, {l.reason}{l.why_qloo && <><br /><span className="small"><span className="steel">Why: Qloo · </span>{l.why_qloo}</span></>}</span>
      {l.url ? <a className="link" href={l.url} target="_blank" rel="noreferrer noopener">Where named</a> : <span />}</div>)}</div>
  );
}

/** "Why: Qloo" (owner, 27 Oct 2026): the Qloo values behind a community or creator investigation, each a relationship,
 * then where in the city on the street map: the heatmap's top cells under the places Qloo relates to the audiences. */
function QlooSection({ d }: { d: Dossier }) {
  const { qloo: q, qloo_why: why = [] } = qlooOf(d);
  const [failed, setFailed] = useState(() => typeof navigator !== "undefined" && navigator.onLine === false);
  if (!q && !why.length) return null;
  const own = d.proximity?.placed ?? [];
  const near = (v: { lat: number | null; lon: number | null }) => !!q && v.lat != null && v.lon != null &&  // ≈ km, keeps the map on the city
    Math.hypot((v.lat - q.center.lat) * 111, (v.lon - q.center.lon) * 111 * Math.cos((q.center.lat * Math.PI) / 180)) <= 6;
  const venues = (q?.audiences ?? []).flatMap((a) => (a.flat ? [] : a.venues.filter(near).slice(0, 2)))
    .filter((v) => !own.some((x) => x.name === v.name))
    .map((v) => ({ lead_key: `qloo-${v.code}`, name: v.name, lat: v.lat!, lon: v.lon!, address: v.address, source: "QLOO" }));
  const px: Proximity | null = !q ? null : { center: q.center, placed: [...own, ...venues.filter((v, i) => venues.findIndex((y) => y.name === v.name) === i)],
    unplaced: [], distances: [], anchor: null, caveat: "" };
  const shown = why.filter((w) => !["group", "creator", "lead", "venue"].includes(w.about));
  return (
    <section className="md-section" aria-label="Why: Qloo">
      <h2 className="label steel">Why: Qloo</h2>
      {shown.length > 0 && <div className="md-list">{shown.map((w) => <div key={w.text} className="md-row md-row-2"><span>{w.text}</span><Chip label="QLOO" /></div>)}</div>}
      {q && px && q.cells.length > 0 && <div className="md-card stack">
        <b>Where in {q.city}</b>
        {failed ? <p className="small steel">The street map couldn't load. {q.neighbourhoods.length ? `Qloo's top cells are in ${q.neighbourhoods.map((n) => n.name).join(", ")}.` : ""}</p>
          : <StreetMap px={px} sel={null} onFail={() => setFailed(true)} cells={q.cells} />}
        <p className="small steel"><svg width="12" height="12" aria-hidden="true" style={{ verticalAlign: "-1px" }}><rect width="12" height="12" rx="2" style={{ fill: "var(--lf-marigold-deep)" }} opacity="0.5" /></svg>{" "}
          Qloo taste concentration. The top {q.cells.length} of {q.cell_count} heatmap cells;
          dots are places Qloo relates to {q.audiences.map((a) => a.name).join(", ") || "the audiences"}{own.length ? " and the groups we placed" : ""}.</p>
      </div>}
      {q && q.failed.length > 0 && <p className="small steel">Qloo didn't answer: {q.failed.join(" · ")}. Unknown, not absent.</p>}
    </section>
  );
}

/** B08: the four steps as numbered cards, each with its knowledge chip; the measure reads the plan's scenario. */
function DigitalFlow({ id, d }: { id: string; d: Dossier }) {
  const b = d.digital;
  return (
    <div className="md-flow" aria-label="Audience, message, channels, measure">
      <div className="md-card"><span className="label steel">1 · Audience</span>{b.targeting.map((t: any) => <span key={t.element}>{t.element} <Chip label={L(t.basis)} /></span>)}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
        {noCaption(b.caveat) && <span className="small steel">{noCaption(b.caveat)}</span>}</div>
      <div className="md-card"><span className="label steel">2 · Message</span><span>{b.message.line || "Not written yet"}</span>
        <span><Chip label={b.message.claim_ids?.length ? "SOURCE" : "NOT KNOWN"} /></span></div>
      <div className="md-card"><span className="label steel">3 · Channels</span><span>{b.channel.primary}{b.channel.alternative ? ` (or ${b.channel.alternative})` : ""}</span>
        <span className="small">{L(b.objective).toLowerCase()} · {b.geography.radius_km_hypothesis ? `city + ${Math.round((b.geography.radius_km_hypothesis / 1.609344) * 10) / 10} mi` : "the city"}</span>
        <span><Chip label="INTERPRETATION" /></span></div>
      <div className="md-card"><span className="label steel">4 · Measure</span><span>{b.measurement.primary_metric || "Not set yet"}</span>
        {b.measurement.stop_rule && <span className="small">Stop rule: {b.measurement.stop_rule} <Chip label="HYPOTHESIS" /></span>}
        <ScenarioLine id={id} city={d.city_id} /></div>
    </div>
  );
}

function ScenarioLine({ id, city }: { id: string; city: string }) {
  const { data: v } = useLive(id, (pid) => api65.plan(pid, city));
  if (!v) return null;
  const inPlan = v.methods.some((m) => m.method === "digital" && m.role && ["MAIN", "SUPPORTS"].includes(m.role.role));
  const s = v.scenario;
  if (!inPlan) return <span className="small steel">Add digital ads to the plan to set a budget.</span>;
  if (!s?.total_daily) return <span className="small steel">No daily budget yet: set it under "Digital ads scenario" on the plan.</span>;
  const imp = s.results?.aggregate.impressions;
  return <span className="small">{s.media_spend && `${money(s.media_spend)} over ${s.horizon_days} days`}{imp ? ` · ${imp.low.toLocaleString()}–${imp.high.toLocaleString()} impressions` : ""} <Chip label="ESTIMATE" /></span>;
}

/** "How we looked": the tool uses, the pages that couldn't be read (once, unknown not absent), and the founder's own
 * levers (preferences, a creator they know, include-anyway, investigate again), kept out of the read-only page. */
function HowWeLooked({ p, v, d, drawers, act, busy }: { p: Project; v: InvestigationView; d: Dossier; drawers: Drawers; act: Act; busy: boolean }) {
  const at = d.workspace?.attempts || [];
  const failed = at.filter((a) => a.outcome === "FAILED").length;
  const rejected = d.candidates.filter((c) => c.decision === "REJECTED");
  const run = (fn: () => Promise<unknown>) => { act(fn); };
  return (
    <details className="md-how">
      <summary className="link">How we looked</summary>
      <div className="stack" style={{ marginTop: 12 }}>
        <p className="small">{at.length} tool uses · {at.filter((a) => a.outcome === "EMPTY").length} searches found nothing (not "none exist")
          {failed ? ` · ${failed} page${failed === 1 ? "" : "s"} couldn't be read: unknown, not absent` : ""}.</p>
        {d.missing.length > 0 && <div><p className="small"><b>Research gaps</b> <span className="steel">· what our search didn't find; not found is not absent</span></p>
          <ul className="small dt-gaps">{d.missing.map((x) => <li key={x.text}>{x.text}</li>)}</ul></div>}
        {d.faults.length > 0 && <p className="small steel">Left out by the checks: {d.faults.map((f) => f.reason).join(" · ")}</p>}
        {d.method === "retail" && d.proximity && (d.proximity.placed.length > 0 || d.proximity.unplaced.length > 0) &&
          <ProximityView px={d.proximity} onChoose={chooser(d, p, run, busy)} />}
        {rejected.length > 0 && v.preferences && <div className="stack">{rejected.filter((c) => c.reason_code !== "FOUNDER_EXCLUDED" && !v.preferences!.include.includes(c.name)).map((c) =>
          <p key={c.ref} className="small">{c.name}: {c.reason || L(c.reason_code).toLowerCase()} <button type="button" className="link" disabled={busy}
            onClick={() => act(() => api45.editPrefs(p.id, v.preferences!.workspace_id, v.preferences!.version, { include: [...v.preferences!.include, c.name] }))}>Include anyway next time</button></p>)}</div>}
        {d.method === "creator" && <AddCreator p={p} city={d.city_id} v={v} act={run} busy={busy} />}
        {v.preferences && <Preferences p={p} v={v} act={run} busy={busy} />}
        {d.products.length > 0 && <p className="small">Your products it used: {d.products.map((x) => <button key={x.id} type="button" className="link" onClick={() => drawers.push({ key: `pr-${x.id}`,
          kicker: "PRODUCT", render: () => <ProductDrawer pid={p.id} prid={x.id} version={x.version} /> })}>{x.title}</button>)}</p>}
        <span><button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api45.investigate(p.id, d.city_id, d.method))}>Investigate again</button></span>
      </div>
    </details>
  );
}

export type { Candidate45 };
