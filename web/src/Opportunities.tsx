import { useEffect, useState } from "react";
import { api, type Approach, type CityClaim, type CityEvidence, type Investigation, type Lead, type Opportunities, type Project,
  type ResearchCity, pathOf, useLive, useProject } from "./api";
import { ActivityDrawer, stagesFor, usageLine } from "./Brand";
import { goalLine } from "./Goal";
import { Chip, DecisionBar, DrawerStack, Notice, ProjectHeader, Skeleton, StageNav, navigate, useDrawers } from "./ui";

type Drawers = ReturnType<typeof useDrawers>;
const METHOD: Record<string, string> = { retail: "RETAIL", creator: "CREATOR", popup: "POP-UP", community: "COMMUNITY", digital: "DIGITAL" };
const STATUS: Record<string, string> = {
  READY_TO_COMPARE: "READY TO COMPARE", HELD: "HELD", NEEDS_RESEARCH: "NEEDS RESEARCH", NOT_SUITABLE_NOW: "NOT SUITABLE NOW",
  RULED_OUT_BY_YOU: "RULED OUT BY YOU", CHECK_FAILED: "CHECK FAILED", NOT_ASSESSED_STOPPED: "NOT ASSESSED · STOPPED",
};
const MATRIX: Record<string, string> = { ...STATUS, READY_TO_COMPARE: "DEVELOPED", HISTORY: "KEPT AS HISTORY" };
const CITY_STATUS: Record<string, string> = {
  queued: "QUEUED", running: "RUNNING", stopped: "STOPPED", complete: "DONE", partial: "PARTIAL", failed: "CHECK FAILED", not_started: "QUEUED",
};
const PRESENCE: Record<string, string> = { VERIFIED: "PRESENCE VERIFIED", NOT_CHECKED: "NOT CHECKED", SOURCES_DIFFER: "SOURCES DIFFER", CHECK_FAILED: "CHECK FAILED" };
const CLAIM: Record<string, string> = { SUPPORTED: "SUPPORTED", TO_CHECK: "TO CHECK", CONTESTED: "SOURCES DIFFER", STATED: "YOU SAID" };

export default function OpportunitiesPage({ id, view }: { id: string; view: "auto" | "research" }) {
  const { project: p, reload: reloadProject } = useProject(id);
  const { data: o, error, reload } = useLive(id, api.opportunities);
  const drawers = useDrawers();
  if (error && !o) return <div className="app"><main className="page"><div className="error-panel card">{error} <a className="link" href="/">Start again</a></div></main></div>;
  if (!p || !o) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const brand = p.brief?.brand_name || p.site;
  const both = () => { reload(); reloadProject(); };
  const active = !!o.run?.active;
  const mode = view === "research" || (active && view === "auto" && !o.cities.some((c) => Object.keys(c.matrix).length)) ? "research" : "approaches";
  return (
    <div className="app">
      <ProjectHeader brand={brand} site={p.site} intent={goalLine(p)} briefVersion={p.brief?.version} running={active}
        usage={usageLine(p)} demo={p.demo} onBrief={() => navigate(`/p/${id}`)}
        onActivity={() => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> })} />
      <StageNav stages={stagesFor(p)} current={-1} />
      <main id="main">
        {!o.researched ? <NotStarted p={p} />
          : mode === "research" ? <ResearchView p={p} o={o} drawers={drawers} reload={both} />
          : <ApproachesView p={p} o={o} drawers={drawers} reload={both} />}
      </main>
      <DrawerStack d={drawers} />
    </div>
  );
}

function NotStarted({ p }: { p: Project }) {
  return (
    <DecisionBar kicker="OPPORTUNITIES" title="No city is being researched yet"
      subtitle="Choose the cities on your recommendations, then start the research there."
      primary={{ label: "Back to the recommendations", onClick: () => navigate(`/p/${p.id}/markets`) }} />
  );
}

function useAct(reload: () => void) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try { await fn(); reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return { busy, err, act };
}

// ---- S07 research underway ----
function ResearchView({ p, o, drawers, reload }: { p: Project; o: Opportunities; drawers: Drawers; reload: () => void }) {
  const { busy, err, act } = useAct(reload);
  const cities = o.cities.filter((c) => c.version);
  const done = cities.filter((c) => ["complete", "partial", "failed"].includes(c.status)).length;
  const active = !!o.run?.active;
  const resumable = cities.filter((c) => c.can.includes("continue")).map((c) => c.city_id);
  return (
    <>
      <section className="decision-bar">
        <div>
          <div className="kicker">OPPORTUNITIES · {active ? "RESEARCH UNDERWAY" : "RESEARCH"} · {done} OF {cities.length} CITIES</div>
          <h1>{active ? `Checking leads and drafting what you could try in ${cities.length} cit${cities.length === 1 ? "y" : "ies"}` : "Research finished for now"}</h1>
          <p className="subtitle">The Researcher asks Qloo for places related to your brand, reads public pages, and checks each claim against the page it quotes. Findings appear as checks finish; a failed city or check holds up nothing else.</p>
          <div className="scan-progress">
            <div className="progress" role="progressbar" aria-label="Cities researched" aria-valuemin={0} aria-valuemax={cities.length} aria-valuenow={done}>
              <span style={{ width: `${cities.length ? (done / cities.length) * 100 : 0}%` }} /></div>
            <span className="mono steel">{done} of {cities.length}</span>
          </div>
        </div>
        <div className="db-actions">
          {active && o.run && <button type="button" className="btn-secondary btn-lg" disabled={busy} onClick={() => act(() => api.cancel(o.run!.id))}>Stop</button>}
          {!active && resumable.length > 0 && <button type="button" className="btn-secondary btn-lg" disabled={busy}
            onClick={() => act(() => api.continueResearch(p.id, resumable))}>Continue research</button>}
          <button type="button" className={active ? "btn-secondary btn-lg" : "btn-primary"} onClick={() => navigate(`/p/${p.id}/opportunities`)}>
            {active ? "See what's ready so far" : "Review approaches"}</button>
        </div>
      </section>
      <div className="page">
        {err && <p className="field-error">{err}</p>}
        {o.run && !active && o.run.message && <Notice kind="info">{o.run.message}</Notice>}
        <ContextNote p={p} o={o} reload={reload} />
        <div className="research-grid" aria-live="polite">
          {cities.map((c) => <CityStream key={c.city_id} p={p} c={c} active={active} drawers={drawers} act={act} busy={busy} />)}
        </div>
      </div>
    </>
  );
}

function ContextNote({ p, o, reload }: { p: Project; o: Opportunities; reload: () => void }) {
  const [text, setText] = useState(o.context || "");
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setText(o.context || ""), [o.context]);
  const save = async () => {
    setErr(null);
    try { setNote((await api.researchNote(p.id, o.intent_version, text)).notice); reload(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card context-note">
      <label htmlFor="lf-ctx" className="field-title small">Do you know anyone in these cities, or a way in you won't use? <span className="steel">Optional · YOU SAID</span></label>
      <div className="row-inline">
        <input id="lf-ctx" className="input" value={text} placeholder="e.g. We won't do pop-ups this year" onChange={(e) => setText(e.target.value)} />
        <button type="button" className="btn-secondary" disabled={text === (o.context || "")} onClick={save}>Save note</button>
      </div>
      <p className="small steel">Cities that haven't started use your newest note. A city that already started keeps the note it began with, and says so.</p>
      {note && <Notice kind="you-changed">{note}</Notice>}
      {err && <p className="field-error">{err}</p>}
    </div>
  );
}

function CityStream({ p, c, active, drawers, act, busy }: {
  p: Project; c: ResearchCity; active: boolean; drawers: Drawers; act: (fn: () => Promise<unknown>) => void; busy: boolean;
}) {
  const waiting = c.status === "queued" && active;
  return (
    <section className="card city-stream" aria-label={c.name}>
      <div className="finding-head"><h2 className="subheading">{c.name}</h2><Chip label={CITY_STATUS[c.status]} />
        {c.selection === "ADDED_BY_YOU" && <Chip label="ADDED BY YOU" />}</div>
      {waiting && <><Skeleton width="80%" /><Skeleton width="60%" /><span className="small steel">Queued</span></>}
      <ul className="stream">
        {c.stream.filter((e) => e.kind !== "UNAVAILABLE").map((e) => ( /* failures live in Activity (owner, 28 Oct 2026) */
          <li key={e.seq}>
            {e.claim_id && e.source_id
              ? <Chip label={e.kind} onClick={() => openEvidence(drawers, e.source_id!, e.claim_id!)} />
              : <Chip label={e.kind} />}
            <span className="small">{e.text}</span>
          </li>
        ))}
      </ul>
      {c.status === "failed" && (
        <div className="notice notice-failed" role="status"><Chip label="CHECK FAILED" />
          <span className="small">{c.error || "The research failed here."} The other cities are kept.</span>
          {c.can.includes("retry") && <button type="button" className="btn-secondary notice-action" disabled={busy}
            onClick={() => act(() => api.retryResearch(p.id, [c.city_id]))}>Retry {c.name}</button>}</div>
      )}
      {c.can.includes("continue") && <button type="button" className="btn-secondary" disabled={busy}
        onClick={() => act(() => api.continueResearch(p.id, [c.city_id]))}>Continue research</button>}
      {c.counters && <p className="mono steel">{c.counters.model} model calls · {c.counters.search} searches · {c.counters.page + c.counters.presence} page reads · Qloo {c.counters.qloo}</p>}
    </section>
  );
}

// ---- S08 approaches ----
function ApproachesView({ p, o, drawers, reload }: { p: Project; o: Opportunities; drawers: Drawers; reload: () => void }) {
  const { busy, err, act } = useAct(reload);
  const cities = o.cities.filter((c) => c.visible && Object.keys(c.matrix).length);
  const hidden = o.cities.filter((c) => !c.visible && c.version);
  const active = !!o.run?.active;
  const t = o.title;
  const title = `${t.approaches} approach${t.approaches === 1 ? "" : "es"} across ${t.cities} cit${t.cities === 1 ? "y" : "ies"}, using ${t.methods} different way${t.methods === 1 ? "" : "s"} in`;
  const notResearched = o.start.new;
  const bh = p.plan?.city_id ? o.cities.find((c) => c.city_id === p.plan!.city_id) ?? null : null;  // S5-26: the comparison lives in the Plan
  return (
    <>
      <DecisionBar kicker={`OPPORTUNITIES · ${active ? "SO FAR · RESEARCH UNDERWAY" : "APPROACHES READY"}`} title={title}
        subtitle="How you could enter each city, and what's still unknown. Every way in is assessed in every city; nothing is padded. Willingness to work with you is unknown everywhere until you ask."
        secondary={<button type="button" className="btn-secondary btn-lg" onClick={() => navigate(`/p/${p.id}/opportunities/research`)}>
          {active ? "Back to the research" : "See the research"}</button>}
        primary={bh ? { label: `Build a plan for ${bh.name}`, onClick: () => navigate(`/p/${p.id}/plan/${bh.city_id}`) }
          : { label: "Build a plan", disabledReason: "Choose a beachhead in Markets" }} />
      <div className="page stack">
        {err && <p className="field-error">{err}</p>}
        {notResearched.length > 0 && !active && (
          <div className="notice notice-changed" role="status"><Chip label="CHANGED" />
            <span>{notResearched.length} cit{notResearched.length === 1 ? "y in your shortlist isn't" : "ies in your shortlist aren't"} researched yet.</span>
            <button type="button" className="btn-secondary notice-action" disabled={busy}
              onClick={() => act(async () => { await api.research(p.id, { city_ids: notResearched }); navigate(`/p/${p.id}/opportunities/research`); })}>
              Research {notResearched.length} more cit{notResearched.length === 1 ? "y" : "ies"}</button></div>
        )}
        {hidden.length > 0 && <p className="small steel">{hidden.map((c) => c.name).join(", ")}: researched, but unticked in your shortlist, so hidden here. Tick it again to see it; nothing is re-run.</p>}
        {cities.length === 0 && <div className="card empty-state"><h2 className="heading-sm">Nothing is ready yet</h2><p>The research is still running; approaches appear as each city finishes.</p></div>}
        {cities.length > 0 && (
          <section className="card-frame" aria-label="What we learned">
            <h2 className="subheading">What we learned</h2>
            {cities.map((c) => <p key={c.city_id} className="learned"><strong>{c.name}:</strong> {c.learned || "Not established yet."}
              {c.learned_source === "DETERMINISTIC" && <span className="mono steel"> · written by Landfall from the checked claims</span>}</p>)}
            <div className="key-row"><span><Chip label="SUPPORTED" /> a quote checked on a page Landfall read</span>
              <span><Chip label="QLOO" /> Qloo’s view of related tastes</span><span><Chip label="HYPOTHESIS" /> a proposal</span>
              <span><Chip label="UNKNOWN" /> not established, never zero</span></div>
          </section>
        )}
        {cities.length > 0 && <Matrix o={o} cities={cities} />}
        {cities.map((c) => <CitySection key={c.city_id} p={p} o={o} c={c} drawers={drawers} act={act} busy={busy} active={active} />)}
      </div>
    </>
  );
}

function Matrix({ o, cities }: { o: Opportunities; cities: ResearchCity[] }) {
  return (
    <section className="card" aria-label="Ways in, by city">
      <div className="card-head"><h2 className="subheading">Five ways in, by city</h2><span className="small steel">Landfall proposes the ways in. You can rule any out.</span></div>
      <div className="matrix-scroll">
        <table className="matrix">
          <thead><tr><th scope="col">City</th>{o.methods.map((m) => <th key={m.key} scope="col">{m.label}</th>)}</tr></thead>
          <tbody>{cities.map((c) => (
            <tr key={c.city_id}><th scope="row">{c.name}</th>
              {o.methods.map((m) => { const cell = c.matrix[m.key]; return (
                <td key={m.key}>{cell ? <><Chip label={MATRIX[cell.status] || cell.status} /><div className="small">{cell.reason}</div></> : <Skeleton width="60%" />}</td>); })}
            </tr>))}</tbody>
        </table>
      </div>
      <p className="small steel">To assess all five in another city, add it in Markets and research it.</p>
    </section>
  );
}

function CitySection({ p, o, c, drawers, act, busy, active }: {
  p: Project; o: Opportunities; c: ResearchCity; drawers: Drawers; act: (fn: () => Promise<unknown>) => void; busy: boolean; active: boolean;
}) {
  const rerun = () => act(async () => { await api.research(p.id, { city_ids: [c.city_id], refresh: true }); navigate(`/p/${p.id}/opportunities/research`); });
  const s = c.input_summary;
  return (
    <section className="city-section" aria-label={c.name}>
      <div className="finding-head"><h2 className="heading">{c.name}</h2>
        {c.selection && <Chip label={c.selection === "ADDED_BY_YOU" ? "ADDED BY YOU" : "RECOMMENDED"} />}
        {c.status !== "complete" && <Chip label={CITY_STATUS[c.status]} />}</div>
      {s && <p className="small steel">{s.position_line || "Added by you: no recommendation, so no ranking line."} Searched with {s.signal}.
        {s.context ? ` Your note when it started: “${s.context}”.` : ""}</p>}
      {c.fenced && (
        <div className="notice notice-changed" role="status"><Chip label="CHANGED" />
          <span>Your change arrived while {c.name} was being researched ({c.fenced_reason}). This result is kept as history and isn't counted.</span>
          {!active && <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={rerun}>Re-research {c.name}</button>}</div>
      )}
      {!c.fenced && c.stale.map((x) => (
        <div key={x} className="notice notice-changed" role="status"><Chip label="CHANGED" /><span>{x}. This research is kept and readable.</span>
          {!active && <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={rerun}>Re-research {c.name}</button>}</div>
      ))}
      {c.notes.map((x) => <p key={x} className="small"><Chip label="YOU SAID" /> {x}</p>)}
      {c.status === "failed" && <div className="notice notice-failed"><Chip label="CHECK FAILED" /><span>{c.error}</span>
        {c.can.includes("retry") && <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={() => act(() => api.retryResearch(p.id, [c.city_id]))}>Retry {c.name}</button>}</div>}
      <div className="card-grid">
        {c.approaches.map((a) => <ApproachCard key={a.id} p={p} o={o} c={c} a={a} drawers={drawers} act={act} busy={busy} />)}
      </div>
      {c.history.length > 0 && (
        <details className="card considered">
          <summary className="subheading">Kept as history · {c.history.length} ways in from before your change</summary>
          {c.history.map((a) => (
            <div key={a.id} className="considered-row">
              <div className="tag-chips"><Chip label={METHOD[a.method]} /><Chip label={STATUS[a.status] || a.status} /></div>
              <div><strong>{a.name}</strong>{a.reason && <p className="small">{a.reason}</p>}</div>
            </div>
          ))}
        </details>
      )}
      {c.approaches.length === 0 && c.status !== "failed" && !c.fenced && <p className="small card">No way in is ready to compare in {c.name} yet. Each method below says why; none was stretched to fit.</p>}
      {c.considered.length > 0 && (
        <div className="card considered">
          <h3 className="subheading">Also considered in {c.name}</h3>
          <p className="small steel">Investigated and not developed. Each says why; none was stretched to fit.</p>
          {c.considered.map((a) => (
            <div key={a.id} className="considered-row">
              <div className="tag-chips"><Chip label={METHOD[a.method]} /><Chip label={STATUS[a.status] || a.status} />{a.reason_source === "YOU_SAID" && <Chip label="YOU SAID" />}</div>
              <div><strong>{a.name}</strong><p className="small">{a.reason}</p>
                {a.claims.map((cl) => <ClaimRow key={cl.id} cl={cl} drawers={drawers} />)}
                {inv(c, a.method) && <button type="button" className="link small" onClick={() => openInvestigation(drawers, inv(c, a.method)!)}>See the investigation</button>}
                {a.status === "RULED_OUT_BY_YOU" && <button type="button" className="link small" disabled={busy} onClick={() => act(() => api.ruleOut(p.id, a.id, a.decision_version, true))}>Undo</button>}
                {a.status !== "RULED_OUT_BY_YOU" && <SpecialistRow p={p} o={o} city={c.city_id} method={a.method} />}
              </div>
            </div>
          ))}
        </div>
      )}
      {c.previous && <p className="mono steel">Showing research v{c.previous}; a newer version is underway.</p>}
    </section>
  );
}

function inv(c: ResearchCity, method: string): Investigation | undefined {
  return c.investigations.find((i) => i.method === method);
}

function ApproachCard({ p, o, c, a, drawers, act, busy }: {
  p: Project; o: Opportunities; c: ResearchCity; a: Approach; drawers: Drawers; act: (fn: () => Promise<unknown>) => void; busy: boolean;
}) {
  const [why, setWhy] = useState(false);
  const ruled = a.status === "RULED_OUT_BY_YOU";
  const i = inv(c, a.method);
  return (
    <article className={`card approach ${ruled ? "excluded" : ""}`} aria-label={a.name}>
      <div className="preview-text"><span className="label steel">PREVIEW UNAVAILABLE · DESCRIPTION</span>
        <span className="small">{a.concept?.line || a.name}</span></div>
      <div className="tag-chips"><Chip label={STATUS[a.status]} /><Chip label={METHOD[a.method]} /></div>
      <h3 className="heading-sm">{a.name}</h3>
      {a.concept && <div className="concept"><span className="label steel">CAMPAIGN CONCEPT · DRAFT</span><strong>{a.concept.name}</strong><span className="small">{a.concept.line}</span></div>}
      {a.partners.map((pt) => {
        const lead = o.leads[pt.id];
        return (
          <div key={pt.id} className="partner-row">
            <button type="button" className="link" onClick={() => openPartner(drawers, p, pt.id)}>{pt.name}</button>
            <Chip label={PRESENCE[pt.presence]} />{pt.chain && <Chip label="CHAIN" />}<Chip label="WILLINGNESS UNKNOWN" />
            {pt.presence === "CHECK_FAILED" && (
              <div className="notice notice-failed" role="status"><span className="small">{lead?.presence_check.reason || "The presence check failed."} It stays unknown.</span>
                <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={() => act(() => api.leadCheck(p.id, pt.id))}>Retry check</button></div>
            )}
          </div>
        );
      })}
      {a.open_requirements.map((r) => <div key={r.text} className="row-inline"><Chip label="UNKNOWN" /><span className="small">{r.text}</span></div>)}
      {i && <div className="inv-row"><span className="label steel">INVESTIGATED BY</span><span className="mono inv-chip">{i.role} · {i.tools_used.length} tool use{i.tools_used.length === 1 ? "" : "s"}</span>
        <button type="button" className="link small" onClick={() => openInvestigation(drawers, i)}>See in Activity</button></div>}
      <button type="button" className="link" aria-expanded={why} onClick={() => setWhy(!why)}>{why ? "Hide why this idea" : "Why this idea?"}</button>
      {why && (
        <div className="trail-wrap">
          <div className="label steel">WHAT IT RESTS ON</div>
          {a.claims.map((cl) => <ClaimRow key={cl.id} cl={cl} drawers={drawers} />)}
          {a.hypotheses.map((h) => <div key={h} className="trail-row"><span className="small">{h}</span><Chip label="HYPOTHESIS" /></div>)}
        </div>
      )}
      <SpecialistRow p={p} o={o} city={c.city_id} method={a.method} />
      {ruled ? <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api.ruleOut(p.id, a.id, a.decision_version, true))}>Undo</button>
        : <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api.ruleOut(p.id, a.id, a.decision_version))}>Rule out</button>}
    </article>
  );
}

const READY45: Record<string, string> = { EVIDENCED: "DEVELOPED", HYPOTHESIS_LED: "HYPOTHESIS-LED", NOT_YET: "NEEDS RESEARCH" };

/** The approach card's slice-4.5 strip: the investigation's state and a way into it (Investigate, cost shown first). */
function SpecialistRow({ p, o, city, method }: { p: Project; o: Opportunities; city: string; method: string }) {
  const s = o.specialists?.[city]?.[method];
  const go = () => navigate(`/p/${p.id}/opportunities/${city}/${method}`);
  if (!s) return <button type="button" className="btn-secondary" onClick={go}>Investigate {METHOD[method].toLowerCase()}</button>;
  return (
    <div className="inv-row">
      {s.readiness && <Chip label={READY45[s.readiness]} />}
      {s.draft_label && <Chip label="DRAFTED" title={s.draft_label} />}
      {["queued", "running"].includes(s.status) && <Chip label="RUNNING" />}
      <button type="button" className="link small" onClick={go}>Open the investigation</button>
    </div>
  );
}

function ClaimRow({ cl, drawers }: { cl: CityClaim; drawers: Drawers }) {
  const label = cl.state === "SUPPORTED" ? "SUPPORTED" : CLAIM[cl.state] || cl.state;
  return (
    <div className="trail-row">
      <span className="small"><strong>{cl.subject_name}:</strong> {cl.text}</span>
      {cl.source ? <Chip label={label} onClick={() => openEvidence(drawers, cl.source!.id, cl.id)} title="Open the evidence" /> : <Chip label={label} />}
    </div>
  );
}

// ---- S09 partner and evidence drawers ----
export function openEvidence(drawers: Drawers, sid: string, claim: string) {
  drawers.push({ key: `ev-${claim}`, kicker: "EVIDENCE", render: () => <EvidenceDrawer sid={sid} claim={claim} /> });
}

export function openPartner(drawers: Drawers, p: Project, lid: string) {
  drawers.push({ key: `lead-${lid}`, kicker: "PARTNER", render: () => <PartnerDrawer p={p} lid={lid} drawers={drawers} /> });
}

function openInvestigation(drawers: Drawers, i: Investigation) {
  drawers.push({ key: `inv-${i.title}`, kicker: "BEHIND THIS PROPOSAL", render: () => (
    <div className="activity">
      <h2 className="heading">{i.title}</h2>
      <p className="steel">{i.role} · the tools it used, in order. Every call is also in the project's Activity.</p>
      <ul className="act-list">{i.tools_used.map((t) => (
        <li key={t.n}><Chip label={["failed", "unavailable", "CHECK_FAILED"].includes(t.outcome) ? "FAILED" : "DONE"} />
          <div><strong>{t.tool}</strong><div className="small">{t.outcome}</div></div></li>))}</ul>
    </div>) });
}

function PartnerDrawer({ p, lid, drawers }: { p: Project; lid: string; drawers: Drawers }) {
  const [l, setL] = useState<Lead | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [contact, setContact] = useState("");
  const load = () => api.lead(p.id, lid).then(setL).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [lid]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <p className="field-error">{err}</p>;
  if (!l) return <Skeleton width="60%" />;
  const save = async () => {
    setErr(null);
    try { await api.leadContact(p.id, lid, l.contact_version, contact); setContact(""); load(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="evidence">
      <div className="label steel">PARTNER · {l.type.toUpperCase()}</div>
      <h2 className="heading">{l.name}</h2>
      <span className="tag-chips">{l.origin === "QLOO" && <Chip label="QLOO" />}{l.chain && <Chip label="CHAIN" />}{l.listing && <Chip label="LISTING" title="Qloo gave no taste order" />}</span>
      <dl className="checks">
        <dt>Address</dt><dd>{l.address ? <>{l.address} <Chip label="QLOO" /></> : "not known"}</dd>
        <dt>Website</dt><dd>{l.website ? <a className="source-url" href={l.website} target="_blank" rel="noreferrer noopener">{pathOf(l.website)}</a> : "not known"}</dd>
        <dt>Contact</dt><dd>{l.founder_contact ? <>{l.founder_contact.value} <Chip label="YOU SAID" /></>
          : l.contact ? <>{l.contact.value} <Chip label="SOURCE" title={`From its own site, ${l.own_site}`} /></> : <><Chip label="UNKNOWN" /> no contact on its own site</>}</dd>
        <dt>Presence</dt><dd><Chip label={PRESENCE[l.presence]} /> {l.presence_check.reason}</dd>
        <dt>Willingness</dt><dd><Chip label="UNKNOWN" /> unknown until you ask</dd>
      </dl>
      <div className="label steel">FINDINGS</div>
      {l.claims.length === 0 && <p className="small steel">No claim about it was checked.</p>}
      {l.claims.map((cl) => <ClaimRow key={cl.id} cl={cl} drawers={drawers} />)}
      <div className="label steel">ADD A CONTACT YOU HAVE</div>
      <div className="row-inline">
        <input className="input" aria-label={`A contact you have for ${l.name}`} value={contact} onChange={(e) => setContact(e.target.value)} placeholder="Name, email or phone" />
        <button type="button" className="btn-secondary" disabled={!contact.trim()} onClick={save}>Save · YOU SAID</button>
      </div>
      <p className="small steel">Landfall never contacts anyone. A contact is shown only from the partner's own site, or from you.</p>
    </div>
  );
}

function EvidenceDrawer({ sid, claim }: { sid: string; claim: string }) {
  const [e, setE] = useState<CityEvidence | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.cityEvidence(sid, claim).then(setE).catch((x) => setErr(x.message)); }, [sid, claim]);
  if (err) return <p className="field-error">{err}</p>;
  if (!e) return <Skeleton width="60%" />;
  const sources = e.differ || [e];
  return (
    <div className="evidence">
      <div className="label steel">{e.claim.label}</div>
      <h2 className="heading">{e.claim.value}</h2>
      <Chip label={CLAIM[e.claim.state] || e.claim.state} />
      {e.differ && <p className="small">Sources differ. Both are shown, newest first. Neither settles it, so anything resting on it is held.</p>}
      {sources.map((s) => (
        <div key={s.claim.id} className="source-card">
          <div><Chip label="SOURCE" /> <span className="mono steel">Read {new Date(s.fetched_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}{s.replayed ? " · recorded" : ""}{s.rendered ? " · rendered" : ""}</span></div>
          <a className="source-url" href={s.final_url} target="_blank" rel="noreferrer noopener">{pathOf(s.final_url)}</a>
          {e.differ && <p className="small"><strong>{s.claim.value}</strong></p>}
          <blockquote>{s.claim.quote}</blockquote>
          <dl className="checks">
            <dt>Quote on the page</dt><dd>{s.claim.quote_found ? "Found" : "Not found"}</dd>
            <dt>Passage places it in the city</dt><dd>{s.claim.locality ? "Yes" : "No"}</dd>
            <dt>Support judgment</dt><dd>{s.claim.support.verdict ? s.claim.support.verdict.toLowerCase() : "not judged"}{s.claim.support.reason ? ` · ${s.claim.support.reason}` : ""}</dd>
          </dl>
          {s.snapshot && <details><summary className="small">The passage it was checked against (snapshot)</summary><p className="mono snapshot">…{s.snapshot}…</p></details>}
        </div>
      ))}
      <div className="means"><div className="label steel">{e.claim.state === "SUPPORTED" ? "WHY THIS IS SUPPORTED" : "WHAT NEEDS CHECKING"}</div>
        <p>{e.claim.reason}</p>
        {e.claim.state !== "SUPPORTED" && <p className="small steel">Until it's confirmed, treat this as unknown. It never grounds an approach.</p>}</div>
    </div>
  );
}
