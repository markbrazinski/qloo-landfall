import { useEffect, useRef, useState } from "react";
import { api, type Brief, type Claim, type Project, type SourceDetail, pathOf, shortDate, useProject } from "./api";
import type { BasisFact } from "./api";
import { Chip, DecisionBar, DrawerStack, EvidenceLink, Notice, ProjectHeader, Skeleton, StageNav, useDrawers } from "./ui";

const NEXT = [
  ["Market scout", "Explores 80 cities using Qloo's cultural relationships and proposes a shortlist for your brand."],
  ["You", "Review the shortlist. Continue with our suggestions or adjust which cities we investigate."],
  ["Launch specialists", "Investigate suitable ways to enter each market, from retail partnerships and creators to pop-ups, events and digital campaigns."],
  ["Launch strategist & analyst", "Compare the strongest launch options, estimate costs and recommend a first test."],
];
const LEGEND = [
  ["SOURCE", "Read from a public page; opens the source"], ["YOU SAID", "Your input"],
  ["INFERRED", "Our interpretation or estimate"], ["TO CHECK", "A quote we could not confirm on its page"],
  ["UNKNOWN", "Not established; never counted as zero"],
];

export default function BrandPage({ id }: { id: string }) {
  const { project: p, error, reload } = useProject(id);
  const drawers = useDrawers();
  const [showBrief, setShowBrief] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const sawReading = useRef(false);

  // Auto-advance: the brief opens ~1 s after reading finishes (no "Review your brief" button).
  useEffect(() => {
    if (!p) return;
    if (!p.brief) {
      sawReading.current = true;
      setShowBrief(false);
      return;
    }
    if (!sawReading.current) return setShowBrief(true);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const t = setTimeout(() => setShowBrief(true), reduce ? 0 : 1000);
    return () => clearTimeout(t);
  }, [p?.brief?.version, p?.brief === null]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error && !p) return <div className="app"><main className="page"><div className="error-panel card">{error} <a className="link" href="/">Start again</a></div></main></div>;
  if (!p) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;

  const running = p.run?.status === "running" || p.run?.status === "queued";
  const brief = p.brief;
  const name = brief?.brand_name || p.site;
  const outcome = brief ? `${name} · brief v${brief.version}` : running ? "Reading the site" : "Reading stopped";
  const usage = p.demo ? "recorded" : `${p.usage.model_calls} calls · $${p.usage.est_usd.toFixed(3)}`;

  const openEvidence = (c: Claim) =>
    drawers.push({ key: `ev-${c.id}`, kicker: "EVIDENCE", render: () => <EvidenceDrawer claim={c} demo={p.demo} /> });
  const openInference = (c: Claim) =>
    drawers.push({ key: `inf-${c.id}`, kicker: "INFERENCE", render: () => <InferenceDrawer claim={c} onEvidence={openEvidence} /> });
  const openActivity = () => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> });
  const openBrief = () =>
    brief && drawers.push({ key: "brief", kicker: "BRIEF", render: () => <BriefGroups brief={brief} p={p} onEvidence={openEvidence} onInference={openInference} /> });

  let body;
  if (brief && showBrief) {
    body = (
      <>
        <DecisionBar kicker={`BRAND · YOUR BRIEF V${brief.version}`} title={`Here's what we understand about ${name}`}
          subtitle="Correct anything important. Nothing else is needed to start: next, the Market scout looks across 80 cities for places related to your brand."
          primary={{ label: "Start exploring markets", disabledReason: "The market scan arrives in the next build." }} />
        <div className="page">
          {notice && <Notice kind="you-changed">{notice}</Notice>}
          <div className="brief-layout">
            <BriefGroups brief={brief} p={p} onEvidence={openEvidence} onInference={openInference} editable
              onCorrect={async (claim, value) => {
                const r = await api.correct(p.id, brief.version, claim.id, value);
                setNotice(r.notice);
                await reload();
              }} />
            <aside className="brief-side">
              <div className="card">
                <h2 className="heading-sm">What happens next</h2>
                <ol className="next-list">
                  {NEXT.map(([r, d], i) => <li key={r}><span className="mono steel">{i + 1}</span><div><strong>{r}</strong><p className="small">{d}</p></div></li>)}
                </ol>
              </div>
              <div className="card card-fog">
                <h3 className="subheading">How to read the labels</h3>
                {LEGEND.map(([c, d]) => <div className="legend-row" key={c}><Chip label={c} /><span className="small">{d}</span></div>)}
              </div>
            </aside>
          </div>
        </div>
      </>
    );
  } else if (p.run?.status === "needs_input" && !brief) {
    body = (
      <>
        <DecisionBar kicker={`BRAND · READING ${p.site.toUpperCase()}`} title="We couldn't read the site" subtitle="Nothing is lost. Choose how to continue." />
        <div className="page reading-layout">
          <ReaderCard p={p} stopped />
          <div className="stack">
            <div className="card"><h2 className="heading-sm">What we understand so far</h2></div>
            <UnreadablePanel p={p} reason={p.run.message.replace(/^unreadable: /, "")} />
          </div>
        </div>
      </>
    );
  } else {
    const failed = !running && !brief;
    body = (
      <>
        <DecisionBar kicker={`BRAND · READING ${p.site.toUpperCase()}`}
          title={failed ? "Reading stopped" : `Reading ${p.site}`}
          subtitle={failed ? p.run?.message : "Your Brand reader is going through your public pages. Your brief opens as soon as it's ready."}
          secondary={running && p.run ? <button type="button" className="btn-secondary" onClick={() => api.cancel(p.run!.id)}>Stop reading</button>
            : failed ? <button type="button" className="btn-secondary" onClick={() => api.retry(p.id)}>Try again</button> : undefined} />
        <div className="page reading-layout">
          <ReaderCard p={p} />
          <div className="card">
            <h2 className="heading-sm">What we understand so far</h2>
            {brief ? <UnderstandingRows brief={brief} /> : (
              <div className="rows">{[0, 1, 2, 3].map((i) => <div className="row" key={i}><Skeleton width="30%" /><Skeleton width="55%" /></div>)}</div>
            )}
          </div>
        </div>
      </>
    );
  }

  return (
    <div className="app">
      <ProjectHeader brand={name} site={p.site} intent={p.intent?.objective || "Explore where to expand for me"}
        briefVersion={brief?.version} running={running} usage={usage} demo={p.demo} onBrief={openBrief} onActivity={openActivity} />
      <StageNav brandOutcome={outcome} />
      <main id="main">{body}</main>
      <DrawerStack d={drawers} />
    </div>
  );
}

function ReaderCard({ p, stopped }: { p: Project; stopped?: boolean }) {
  const done = p.reading.filter((r) => ["DONE", "SKIPPED", "FAILED"].includes(r.status)).length;
  const failed = p.reading.some((r) => r.status === "FAILED");
  return (
    <div className="card">
      <div className="card-head"><h2 className="heading-sm">Brand reader</h2><span className="mono steel">{stopped ? "stopped" : `${done} of ${p.reading.length}`}</span></div>
      <div className={`progress ${failed ? "progress-failed" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={p.reading.length} aria-valuenow={done}>
        <span style={{ width: `${(done / p.reading.length) * 100}%` }} />
      </div>
      <ul className="task-rows">
        {p.reading.map((r) => (
          <li key={r.key}>
            <Chip label={r.status} />
            <div><div>{r.label}</div>{r.detail && <div className="small steel">{r.detail}</div>}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function UnderstandingRows({ brief }: { brief: Brief }) {
  return (
    <div className="rows">
      {[...brief.facts.slice(0, 3), ...brief.interpretation.slice(0, 1)].map((c) => (
        <div className="row row-3" key={c.id}>
          <span className="row-label">{c.label}</span><span>{c.value}</span>
          <Chip label={c.state === "SUPPORTED" ? "SOURCE" : c.state.replace("_", " ")} />
        </div>
      ))}
    </div>
  );
}

function UnreadablePanel({ p, reason }: { p: Project; reason: string }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="error-panel">
      <Chip label="SITE UNREADABLE" />
      <h2 className="heading">We couldn't read {p.site}</h2>
      <p>{reason ? `${reason.charAt(0).toUpperCase()}${reason.slice(1)}. ` : ""}Your URL is kept. Retry, or tell us in a sentence what you sell and we'll start from that.</p>
      <label className="field-title small" htmlFor="desc">What you sell</label>
      <textarea id="desc" className="input" rows={3} value={text} onChange={(e) => setText(e.target.value)}
        placeholder="e.g. Sparkling cold-brewed tea in cans, sold through neighbourhood shops in Portland" />
      {err && <p className="field-error">{err}</p>}
      <div className="btn-row">
        <button type="button" className="btn-primary" disabled={text.trim().length < 8}
          onClick={() => api.describe(p.id, text).catch((e) => setErr(e.message))}>Continue with my description</button>
        <button type="button" className="btn-secondary" onClick={() => api.retry(p.id)}>Retry reading the site</button>
      </div>
    </div>
  );
}

function BriefGroups(props: { brief: Brief; p: Project; onEvidence: (c: Claim) => void; onInference: (c: Claim) => void; editable?: boolean; onCorrect?: (c: Claim, v: string) => Promise<void> }) {
  const { brief, p } = props;
  const site = brief.route === "site";
  return (
    <div className="stack">
      <section className="card group">
        <div className="group-head"><h2 className="subheading">{site ? "From your site" : "From your description"}</h2>
          <span className="small steel">{site ? `Read ${shortDate(brief.read_at)} · ${brief.pages_read} public pages` : "Site facts not read"}</span></div>
        {brief.facts.map((c) => <BriefRow key={c.id} c={c} {...props} />)}
        {brief.facts.length === 0 && <p className="small steel">No facts could be confirmed from the pages read.</p>}
      </section>
      <section className="card group">
        <div className="group-head"><h2 className="subheading">Our interpretation</h2><span className="small steel">Correct anything that's wrong</span></div>
        {brief.interpretation.map((c) => <BriefRow key={c.id} c={c} {...props} />)}
      </section>
      <section className="card group">
        <div className="group-head"><h2 className="subheading">Not known yet</h2><span className="small steel">We ask only if a decision needs it</span></div>
        {brief.unknowns.map((u) => (
          <div className="row row-3" key={u.label}>
            <span className="row-label">{u.label}</span>
            <span>{brief.unknown_basis}<div className="mono steel prov">{u.why_it_matters}</div></span>
            <Chip label="UNKNOWN" />
          </div>
        ))}
      </section>
      <section className="card group">
        <div className="group-head"><h2 className="subheading">Your intent</h2><span className="small steel">{p.intent?.objective === "Explore where to expand for me" ? "Defaults" : "Your choices"}</span></div>
        <div className="row row-3">
          <span className="row-label">Intent</span>
          <span>{p.intent?.objective} · {p.intent?.ceiling_usd ? `up to $${p.intent.ceiling_usd.toLocaleString()}` : "no budget"} · {p.intent?.include || p.intent?.exclude ? [p.intent.include && `include ${p.intent.include}`, p.intent.exclude && `leave out ${p.intent.exclude}`].filter(Boolean).join(" · ") : "no location preference"}
            <div className="mono steel prov">change in Advanced at the start</div></span>
          <Chip label="YOU SAID" />
        </div>
      </section>
    </div>
  );
}

function inferredFromText(c: Claim) {
  const f = c.inferred_from;
  if (!f) return c.basis || "";
  const parts = [...f.facts.map((x) => x.label), ...f.pages.map((x) => pathOf(x.url))];
  return parts.length ? `Inferred from: ${[...new Set(parts)].join(" · ")}` : "Inferred from: nothing we could trace";
}

function BriefRow({ c, onEvidence, onInference, editable, onCorrect }: { c: Claim; onEvidence: (c: Claim) => void; onInference: (c: Claim) => void; editable?: boolean; onCorrect?: (c: Claim, v: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(c.value);
  const [err, setErr] = useState<string | null>(null);
  const inferred = c.state === "INFERRED" && !c.corrected;
  const prov = c.corrected ? "corrected by you" : c.source ? pathOf(c.source.url) : inferred ? inferredFromText(c) : c.basis || (c.state === "STATED" ? "your description" : "");
  return (
    <div className="row-wrap">
      <div className="row row-3">
        <span className="row-label">{c.label}</span>
        <span>{c.value}{prov && <div className="mono steel prov">{prov}</div>}</span>
        <span className="row-actions">
          {c.corrected || c.vc === "U" ? <Chip label="YOU SAID" />
            : inferred ? (
              <>
                {c.inferred_from?.changed.length ? <Chip label="REVIEW · FACT CHANGED" /> : null}
                {c.inferred_from && !c.inferred_from.established ? <Chip label="BASIS NOT ESTABLISHED" /> : null}
                <Chip label="INFERRED" onClick={() => onInference(c)} title="See what this is inferred from" />
              </>
            ) : <EvidenceLink state={c.state} onOpen={() => onEvidence(c)} />}
          {editable && !editing && <button type="button" className="link" onClick={() => setEditing(true)}>Correct</button>}
        </span>
      </div>
      {editing && (
        <form className="correct-form" onSubmit={async (e) => {
          e.preventDefault();
          try { await onCorrect?.(c, value); setEditing(false); } catch (x) { setErr((x as Error).message); }
        }}>
          <label className="row-label" htmlFor={`fix-${c.id}`}>{c.label}</label>
          <input id={`fix-${c.id}`} className="input" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
          <button type="submit" className="btn-dark" disabled={!value.trim() || value === c.value}>Save correction</button>
          <button type="button" className="link" onClick={() => { setEditing(false); setValue(c.value); }}>Cancel</button>
          {err && <p className="field-error">{err}</p>}
        </form>
      )}
    </div>
  );
}

function EvidenceDrawer({ claim, demo }: { claim: Claim; demo: boolean }) {
  const [src, setSrc] = useState<SourceDetail | null>(null);
  useEffect(() => { if (claim.source) api.source(claim.source.id, claim.id).then(setSrc); }, [claim.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const state = claim.state.replace("_", " ");
  return (
    <div className="evidence">
      <div className="label steel">{claim.label}</div>
      <h2 className="heading">{claim.label}: {claim.value}</h2>
      <Chip label={state} />
      <div className="label steel">SOURCE</div>
      {claim.source ? (
        <div className="source-card">
          <div><Chip label="YOUR SITE" /> <span className="mono steel">Read {src ? new Date(src.fetched_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "…"}{demo || src?.replayed ? " · recorded" : ""}</span></div>
          <a className="source-url" href={claim.source.url} target="_blank" rel="noreferrer noopener">{pathOf(claim.source.url)}</a>
          {claim.quote && <blockquote>{claim.quote}</blockquote>}
        </div>
      ) : <p className="small steel">No source page. {claim.state === "STATED" ? "This is what you told us." : ""}</p>}
      {claim.source && (
        <dl className="checks">
          <dt>Quote on the page</dt><dd>{claim.quote_found ? "Found" : "Not found"}</dd>
          <dt>Quote supports this claim</dt><dd>{claim.support ? claim.support.replace("_", " ").toLowerCase() : "not judged"}</dd>
        </dl>
      )}
      <div className="means">
        <div className="label steel">{claim.state === "SUPPORTED" ? "WHY THIS IS SUPPORTED" : "WHAT NEEDS CHECKING"}</div>
        <p>{claim.reason || "—"}</p>
        {claim.state !== "SUPPORTED" && <p className="small steel">Until it's confirmed, treat this as unknown. It is never counted as a fact.</p>}
      </div>
      <div className="mono steel">Website reader · Brand reader{claim.state === "SUPPORTED" ? " · quote found on the page and judged to support the claim" : ""}</div>
    </div>
  );
}

function asClaim(f: BasisFact): Claim {
  return { ...f, vc: "F", basis: null, corrected: false, checker: null };
}

function InferenceDrawer({ claim, onEvidence }: { claim: Claim; onEvidence: (c: Claim) => void }) {
  const f = claim.inferred_from;
  return (
    <div className="evidence">
      <div className="label steel">{claim.label}</div>
      <h2 className="heading">{claim.label}: {claim.value}</h2>
      <Chip label="INFERRED" />
      {f?.changed.length ? (
        <div className="notice notice-changed" role="status"><Chip label="REVIEW · FACT CHANGED" />
          <span>You corrected {f.changed.join(" and ")} after we made this reading. Review it, or correct it.</span></div>
      ) : null}
      <div className="label steel">INFERRED FROM</div>
      {!f || (!f.facts.length && !f.pages.length) ? (
        <p className="small">Nothing we could trace. Treat this reading as unsupported until you confirm or correct it.</p>
      ) : (
        <ul className="basis-list">
          {f.facts.map((x) => (
            <li key={x.id}>
              <div><strong>{x.label}</strong>: {x.value}</div>
              <span className="row-actions">
                {x.state === "SUPPORTED" && x.source ? (
                  <button type="button" className="chip chip-sourced chip-link" onClick={() => onEvidence(asClaim(x))}>View source ↗</button>
                ) : <Chip label={x.state.replace("_", " ")} onClick={x.source ? () => onEvidence(asClaim(x)) : undefined} />}
              </span>
            </li>
          ))}
          {f.pages.map((pg) => (
            <li key={pg.id}>
              <div>Wording on <a className="source-url" href={pg.url} target="_blank" rel="noreferrer noopener">{pathOf(pg.url)}</a></div>
              <span className="mono steel">page read {new Date(pg.fetched_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</span>
            </li>
          ))}
        </ul>
      )}
      {f && !f.established && (f.facts.length > 0) && (
        <p className="small steel">None of the facts above is established yet, so this reading isn't either.</p>
      )}
      <div className="means">
        <div className="label steel">WHY WE READ IT THIS WAY</div>
        <p>{f?.reasoning || "—"}</p>
        <p className="small steel">This is Landfall's interpretation, not something the site states. Correct it if it's wrong.</p>
      </div>
      {f?.dropped.length ? <p className="small steel">{f.dropped.length} reference{f.dropped.length > 1 ? "s" : ""} could not be matched to what was read and {f.dropped.length > 1 ? "were" : "was"} ignored.</p> : null}
    </div>
  );
}

function ActivityDrawer({ p }: { p: Project }) {
  const toCheck = p.brief ? p.brief.facts.filter((f) => f.state !== "SUPPORTED" && f.vc !== "U").length : 0;
  return (
    <div className="activity">
      <h2 className="heading">What your team has done</h2>
      <p className="steel">Driven by the same events as the screens: nothing appears here before it happens.</p>
      <div className="tiles">
        <div className="tile"><div className="label steel">MODEL CALLS</div><div className="figure">{p.usage.model_calls}</div>
          <div className="small steel">{p.demo ? "replayed from a recording" : `≈ $${p.usage.est_usd.toFixed(4)} operating cost`}</div></div>
        <div className="tile"><div className="label steel">PAGES FETCHED</div><div className="figure">{p.usage.pages_read}</div><div className="small steel">of {p.usage.pages_fetched} attempted, your site</div></div>
        <div className="tile"><div className="label steel">TO CHECK</div><div className="figure">{toCheck}</div><div className="small steel">kept as unknown</div></div>
      </div>
      <div className="act-stage">01 · Brand</div>
      <ul className="act-list">
        {p.activity.map((a) => (
          <li key={a.seq}>
            <Chip label={a.kind === "founder" ? "YOU SAID" : a.status || "DONE"} />
            <div><strong>{a.task}</strong><div className="small">{a.detail}</div>
              <div className="mono steel">{a.kind === "founder" ? "You" : `${a.role} · ${a.tool}`}</div></div>
          </li>
        ))}
      </ul>
      <p className="small steel">Operating cost is Landfall's own provider spend, separate from any launch budget of yours.</p>
    </div>
  );
}
