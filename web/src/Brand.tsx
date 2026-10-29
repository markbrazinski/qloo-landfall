import React, { useEffect, useRef, useState } from "react";
import { api, api45, type Brief, type Claim, type ProductSnapshot, type Project, type SourceDetail, enrichOf, pathOf, photoOf, readOf, shortDate, useProject } from "./api";
import type { BasisFact } from "./api";
import "./brand.css";
import { AudienceCard, IdentityChooser, ProfileEditor, QlooCard } from "./Discovery";
import { HomeQuestion, PresencePanel, StandInsPanel, goalLine } from "./Goal";
import { Chip, DecisionBar, DrawerStack, Notice, ProjectHeader, Skeleton, type Stage, StageNav, navigate, useDrawers } from "./ui";

const LEGEND = [
  ["SOURCE", "Read from a public page; opens the source"], ["YOU SAID", "Your input"],
  ["INTERPRETATION", "Our reading, with what it’s inferred from"], ["TO CHECK", "Couldn’t confirm on its page"],
  ["UNKNOWN", "Not established; never counted as zero"], ["QLOO", "Qloo’s view of related tastes"],
  ["FEED", "From your store's own product feed"], ["WEB", "From a page a web search found; its quote was checked"],
];

/** Brand and Markets in the stage nav; Markets links once it has something to show. */
export function stagesFor(p: Project): Stage[] {
  const name = p.brief?.brand_name || p.site;
  const brand = p.brief ? `${name} · brief v${p.brief.version}` : "Reading the site";
  const m = p.markets;
  const bh = p.beachhead;
  const outcome = m.stage === "scanning" ? "Scanning 80 cities" : m.stage === "scanned"
    ? (bh ? `Beachhead: ${bh.name}` : "80 cities scanned · choose a city") : "Not started";
  const plan = p.plan && bh && p.plan.city_id === bh.city_id ? p.plan : null;
  const noCity = "Choose a beachhead city in Markets first";
  const noPlan = "Prepare this plan first";
  const c = plan?.counts || {};
  const goneOut = ["SENT", "TEST_SENT", "CREATED_PAUSED", "MARKED_SENT_BY_YOU", "MARKED_CREATED_BY_YOU"].reduce((n, k) => n + (c[k] || 0), 0);
  const needYou = Object.values(c).reduce((a, b) => a + b, 0) - goneOut;  // Launch: each thing that goes out, until it has
  // Brand · Markets · Plan · Launch (owner, 28 Oct 2026: Prepare and Act & adapt are one checklist; v4's open decision)
  return [{ label: "Brand", outcome: p.brief ? `Expansion brief v${p.brief.version}` : brand, href: `/p/${p.id}` },
    { label: "Markets", outcome, href: m.stage === "scanning" || m.stage === "scanned" ? `/p/${p.id}/markets` : undefined },
    { label: "Plan", outcome: !bh ? "Waiting for a city" : !plan?.version ? "Building" : `Plan v${plan.version} · ${plan.methods ?? 0} method${plan.methods === 1 ? "" : "s"}`,
      href: bh ? `/p/${p.id}/plan/${bh.city_id}` : undefined, locked: bh ? undefined : noCity },
    { label: "Launch", outcome: plan?.signed_off ? (needYou ? `${needYou} to sign off` : "All signed off") : "Not started",
      href: bh && plan?.signed_off ? `/p/${p.id}/launch/${bh.city_id}` : undefined, locked: !bh ? noCity : plan?.signed_off ? undefined : noPlan }];
}

export function usageLine(p: Project) {
  return (p.demo ? "recorded" : `${p.usage.model_calls} calls`) + ` · Qloo ${p.usage.qloo_sent} sent`;
}

export default function BrandPage({ id }: { id: string }) {
  const { project: p, error, reload } = useProject(id);
  const drawers = useDrawers();
  const latest = useRef(p);
  latest.current = p;  // drawers render from the latest project, not the one they opened with
  const [showBrief, setShowBrief] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [exploreErr, setExploreErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const sawReading = useRef(false);
  const retried = useRef(false);

  const running = p?.run?.status === "running" || p?.run?.status === "queued";
  // v4 02: the founder opens the brief ("See the brief"); a brief that was already there on arrival opens directly.
  useEffect(() => {
    if (!p) return;
    if (!p.brief || running) { sawReading.current = true; setShowBrief(false); return; }
    if (!sawReading.current) setShowBrief(true);
  }, [p?.brief?.version, running]); // eslint-disable-line react-hooks/exhaustive-deps

  // Explore waits only while Qloo is being asked; then it carries on by itself.
  useEffect(() => {
    if (pending && p && p.identity.status !== "searching" && !p.profile?.proposing) { setPending(false); explore(); }
  }, [p]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error && !p) return <div className="app"><main className="page"><div className="error-panel card">{error} <a className="link" href="/">Start again</a></div></main></div>;
  if (!p) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;

  const brief = p.brief;
  const name = brief?.brand_name || p.site;
  const read = brief && readOf(brief);
  const ident = p.identity;
  const stopped = !running && !brief && ["needs_input", "failed", "cancelled"].includes(p.run?.status || "");
  const unreadable = !running && (stopped || (!!read?.unreadable && brief?.route === "site"));

  const openEvidence = (c: Claim) =>
    drawers.push({ key: `ev-${c.id}`, kicker: "EVIDENCE", render: () => <EvidenceDrawer claim={c} demo={p.demo} site={p.site} /> });
  const openInference = (c: Claim) =>
    drawers.push({ key: `inf-${c.id}`, kicker: "INFERENCE", render: () => <InferenceDrawer claim={c} onEvidence={openEvidence} /> });
  const openActivity = () => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={latest.current!} /> });
  const openProduct = (prid: string, version: number) =>
    drawers.push({ key: `pr-${prid}`, kicker: "PRODUCT", render: () => <ProductDrawer pid={p.id} prid={prid} version={version} /> });
  const openNotYou = () => drawers.push({ key: "identity", kicker: "QLOO IDENTITY", render: () => <IdentityChooser p={latest.current!} onDone={() => { drawers.close(); reload(); }} /> });
  const openSearch = () => drawers.push({ key: "profile", kicker: "DISCOVERY PROFILE", render: () => {
    const q = latest.current!;
    return <div className="stack">{q.profile && <ProfileEditor p={q} onInference={openInference} reload={reload} />}
      {q.goal?.standins && <section className="card group"><h2 className="subheading">Brands with a similar following</h2><StandInsPanel p={q} si={q.goal.standins} reload={reload} /></section>}</div>;
  } });

  /** v4: identity and profile are never a blocking confirmation. Explore settles what Markets needs, then scans. */
  async function explore() {
    setExploreErr(null);
    try {
      // fresh, not the last poll: Markets may have started by themselves a moment ago (owner, 28 Oct 2026)
      const q = await api.project(latest.current!.id).catch(() => latest.current!);
      const m = q.markets;
      if ((m.stage === "scanning" || m.stage === "scanned") && !m.outdated) return navigate(`/p/${q.id}/markets`);
      const st = q.identity.status;
      const prof = q.profile;
      if (!q.markets_reason) { await api.scan(q.id); return navigate(`/p/${q.id}/markets/scan`); }
      if (st === "failed" || st === "not_started") {
        if (retried.current) return setExploreErr(`Qloo still couldn't be reached (${q.identity.failure_reason || "no answer"}). Try again in a moment.`);
        retried.current = true;
        await api.retryIdentity(q.id);
        setPending(true);
        return reload();
      }
      if (st === "searching" || prof?.proposing) return setPending(true);
      if (st === "needs_founder") return openNotYou();
      if (q.goal?.needs_home) return setExploreErr("Tell us which country is home first: your goal includes your home market.");
      if (prof && prof.route !== "brand_entity" && prof.signals.some((x) => x.included)) {
        await api.editProfile(q.id, prof.version, prof.signals.map(({ qloo_id, kind, included }) => ({ qloo_id, kind, included })), true);
        await api.scan(q.id);
        return navigate(`/p/${q.id}/markets/scan`);
      }
      setExploreErr(`${q.markets_reason} Use Adjust on the Qloo card.`);
    } catch (x) {
      setExploreErr((x as Error).message);
    }
  }

  let body;
  if (brief && showBrief && !unreadable) {
    body = (
      <>
        <DecisionBar kicker={`BRAND · YOUR BRIEF V${brief.version}`} title={`Here's what we understand about ${name}`}
          subtitle="Correct anything important. Nothing else is needed to start: next, the Market scout looks across 80 cities."
          primary={p.demo && p.identity.status === "failed" ? { label: "Explore markets", disabledReason: p.markets_reason }  // the recorded demo ends here
            : { label: "Explore markets", onClick: explore }} />
        <div className="page lfb-brief">
          {pending && <p className="small steel" role="status">Asking Qloo about {name}; Markets opens as soon as it answers.</p>}
          {exploreErr && <p className="field-error" role="alert">{exploreErr}</p>}
          {notice && <Notice kind="you-changed">{notice}</Notice>}
          {p.goal?.needs_home && <HomeQuestion p={p} goal={p.goal} reload={reload} />}
          <BriefGroups brief={brief} p={p} onEvidence={openEvidence} onInference={openInference} onProduct={openProduct}
            qloo={<QlooCard p={p} onNotYou={openNotYou} onSearch={openSearch} reload={reload} />} audience={<AudienceCard p={p} reload={reload} />}
            reload={reload}
            onCorrect={async (claim, value) => {
              const r = await api.correct(p.id, brief.version, claim.id, value);
              setNotice(r.notice);
              await reload();
            }} />
        </div>
      </>
    );
  } else {
    const done = !!brief && !running && !unreadable;
    body = (
      <>
        {/* honest variant of v4's "Facts appear as they're read": facts arrive together, after the quote check */}
        <DecisionBar kicker="BRAND · READING" title={`Reading ${p.site}`} subtitle="Facts appear once they're checked against their pages." />
        <div className="page lfb-reading">
          <ReaderProgress p={p} unreadable={unreadable} />
          <div className="lfb-c2">
            <ReaderCard p={p} unreadable={unreadable} />
            <div className="lfb-stack">
              {ident.status === "needs_founder" && ident.name_source === "address" && !unreadable && <IdentityChooser p={p} onDone={reload} />}
              {unreadable ? <UnreadablePanel p={p} reason={stopped ? p.run!.message.replace(/^unreadable: /, "") : null} /> : (
                <div className="lfb-card lfb-card-sm">
                  <span className="label steel">What we understand so far</span>
                  {brief ? brief.facts.slice(0, 3).map((c) => <span key={c.id} className="lfb-line">{c.value}</span>)
                    : <span className="small steel">{ident.status === "needs_founder" ? "Reading continues while Qloo has more than one brand that might be you." : "Nothing checked yet."}</span>}
                </div>
              )}
              {done && (
                <div className="lfb-card lfb-card-done">
                  <span className="lfb-strong">Reading finished</span>
                  <button type="button" className="btn-primary" onClick={() => setShowBrief(true)}>See the brief</button>
                </div>
              )}
            </div>
          </div>
        </div>
      </>
    );
  }

  return (
    <div className="app">
      <ProjectHeader brand={name} site={p.site} intent={goalLine(p)}
        briefVersion={brief?.version} running={running} usage={usageLine(p)} demo={p.demo} onActivity={openActivity} />
      <StageNav stages={stagesFor(p)} current={0} />
      <main id="main">{body}</main>
      <DrawerStack d={drawers} />
    </div>
  );
}

const STATUS_WORD: Record<string, string> = { DONE: "Done", RUNNING: "Reading…", QUEUED: "Queued", SKIPPED: "None found", "NEEDS YOU": "Needs you" };
const SITE_ROWS = ["open", "products", "about", "stockists"];

function rowWord(r: { key: string; status: string }, unreadable: boolean) {
  const site = SITE_ROWS.includes(r.key);
  // a blocked site's "nothing found" is unknown, not absent
  if (r.status === "FAILED" || (unreadable && site && r.status === "SKIPPED")) return { word: site ? "Couldn’t read" : "Failed", bad: true };
  return { word: STATUS_WORD[r.status] || r.status.toLowerCase(), bad: false };
}

/** Rows finished; on an unreadable site, only rows actually read count (v4 B02: "3 of 7"). */
const progressOf = (p: Project, unreadable: boolean) =>
  p.reading.filter((r) => (unreadable ? r.status === "DONE" && !rowWord(r, true).bad : ["DONE", "SKIPPED", "FAILED"].includes(r.status))).length;

function ReaderProgress({ p, unreadable }: { p: Project; unreadable: boolean }) {
  const n = progressOf(p, unreadable);
  return (
    <div className="lfb-progress" role="progressbar" aria-label="Brand reader progress" aria-valuemin={0} aria-valuemax={p.reading.length} aria-valuenow={n}>
      <span style={{ width: `${(n / p.reading.length) * 100}%` }} />
    </div>
  );
}

function ReaderCard({ p, unreadable }: { p: Project; unreadable: boolean }) {
  const waiting = p.reading.find((r) => r.status === "RUNNING" && r.detail.startsWith("waiting for"));
  return (
    <div className="lfb-card lfb-card-sm">
      <span className="label steel">Brand reader · {progressOf(p, unreadable)} of {p.reading.length}</span>
      <ul className="lfb-read-rows">
        {p.reading.map((r) => {
          const w = rowWord(r, unreadable);
          return <li key={r.key} title={r.detail || undefined}><span>{r.label}</span><span className={w.bad ? "lfb-bad" : r.status === "DONE" ? "" : "steel"}>{w.word}</span></li>;
        })}
      </ul>
      {/* a site's Retry-After is honoured once; the product feed, the web and Qloo keep filling in meanwhile */}
      {waiting && <p className="small steel" role="status">{waiting.detail.charAt(0).toUpperCase() + waiting.detail.slice(1)}. Meanwhile we read your store's product feed, the web and Qloo.</p>}
    </div>
  );
}

const WHY: Record<string, string> = { "HTTP 429": "turned our reader away (HTTP 429)", Timeout: "timed out" };

function UnreadablePanel({ p, reason }: { p: Project; reason: string | null }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const read = p.brief && readOf(p.brief);
  const what = reason ? `${reason.charAt(0).toUpperCase()}${reason.slice(1)}.`
    : read ? `${read.pages_failed} of ${read.pages_tried} pages ${read.why ? WHY[read.why] || `couldn’t be read (${read.why})` : "couldn’t be read"}, so nothing on the site could be confirmed.` : "";
  return (
    <div className="lfb-recover" role="region" aria-labelledby="recover-h">
      <b id="recover-h">We couldn’t read {reason ? "" : "most of "}{p.site}</b>
      <span>{what} You can retry, or tell us about the brand in a sentence or two and we’ll build the brief from that.</span>
      {p.brief && enrichOf(p.brief)?.frank && <span className="small steel">{enrichOf(p.brief)!.frank}</span>}
      <textarea rows={3} aria-label="Tell us about the brand" value={text} onChange={(e) => setText(e.target.value)}
        placeholder="e.g. Sparkling cold-brewed tea from Portland, sold in neighborhood grocers" />
      {err && <p className="field-error">{err}</p>}
      <div className="lfb-recover-actions">
        <button type="button" className="btn-secondary" onClick={() => (text.trim().length < 8 ? setErr("Add a sentence about what you sell first.")
          : api.describe(p.id, text).catch((e) => setErr(e.message)))}>Build the brief from this</button>
        <button type="button" className="link" onClick={() => api.retry(p.id).catch((e) => setErr(e.message))}>Retry reading</button>
      </div>
    </div>
  );
}

/** A product and the page snapshot Landfall read it from (P45-I7): shared by the brief, investigations and drafts. */
export function ProductDrawer({ pid, prid, version }: { pid: string; prid: string; version?: number }) {
  const [x, setX] = useState<ProductSnapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api45.productSnapshot(pid, prid, version).then(setX).catch((e) => setErr(e.message)); }, [pid, prid, version]);
  if (err) return <p className="field-error">{err}</p>;
  if (!x) return <Skeleton width="60%" />;
  return (
    <div className="evidence">
      <div className="label steel">PRODUCT · {x.discovered_via === "BRAND_PAGES" ? "FROM YOUR PRODUCT PAGE" : x.discovered_via === "SHOPIFY_FEED" ? "FROM YOUR STORE’S PRODUCT FEED" : "FOUND BY CATALOG SEARCH, READ FROM ITS PAGE"}</div>
      <h2 className="heading">{x.title}</h2>
      <div className="tag-chips">{x.category_terms.map((c) => <Chip key={c} label="SOURCE" title={c} />)}
        <span className="small">{[...x.category_terms, ...x.materials].join(" · ") || "no category or material shown"}</span></div>
      {x.price_text && <p className="small">Shown on the page as {x.price_text} (display text, not used as money)</p>}
      <div className="source-card">
        <div><Chip label={x.discovered_via === "SHOPIFY_FEED" ? "FEED" : "YOUR SITE"} /> <span className="mono steel">Read {new Date(x.snapshot.fetched_at).toLocaleDateString("en-GB",
          { day: "numeric", month: "short", year: "numeric" })}{x.snapshot.replayed ? " · recorded" : ""}</span></div>
        <a className="source-url" href={x.snapshot.final_url || x.snapshot.url} target="_blank" rel="noreferrer noopener">{pathOf(x.snapshot.url)}</a>
        <blockquote className="snapshot">{x.snapshot.excerpt}</blockquote>
      </div>
      <p className="small steel">The snapshot is what Landfall read; the live page may have changed since.</p>
    </div>
  );
}

/** v4 03: What we know · How we read your brand / What Qloo associates · Who likes brands like yours, then one accordion
 *  list (products, presence, unknowns, sources, labels). */
function BriefGroups(props: { brief: Brief; p: Project; onEvidence: (c: Claim) => void; onInference: (c: Claim) => void;
  onProduct: (id: string, version: number) => void; onCorrect: (c: Claim, v: string) => Promise<void>;
  qloo: React.ReactNode; audience: React.ReactNode; reload: () => unknown }) {
  const { brief, p } = props;
  const site = brief.route === "site";
  const read = readOf(brief);
  const items = brief.products?.items ?? [];
  const pres = p.goal?.presence;
  const near = pres?.cities.filter((c) => c.excluded) ?? [];
  const failedWhy = read?.why ? WHY[read.why] || `couldn’t be read (${read.why})` : "couldn’t be read";
  const enr = enrichOf(brief);
  return (
    <>
      <div className="lfb-c2">
        <section className="lfb-card" aria-labelledby="know-h">
          <div className="lfb-card-head"><h2 id="know-h">What we know</h2>
            <span className="mono steel">{site ? `From your site · ${read ? `${read.pages_read} of ${read.pages_tried}` : brief.pages_read} pages · ${shortDate(brief.read_at)}` : "From your description"}</span></div>
          {enr?.frank && <p className="small lfb-frank" role="note">{enr.frank}</p>}
          {groupFacts(brief.facts, enr?.origins).map((g) => <FactSection key={g.title} g={g} {...props} origins={enr?.origins} />)}
          {brief.facts.length === 0 && <p className="small steel">No facts could be confirmed from the pages read.</p>}
        </section>
        {/* owner, 28 Oct 2026: the reading, Qloo and the audience stack in the right column beside what we know */}
        <div className="lfb-col">
          <section className="lfb-card" aria-labelledby="read-h">
            <div className="lfb-card-head"><h2 id="read-h">How we read your brand</h2><span className="mono steel">Our interpretation · correct anything that’s wrong</span></div>
            {brief.interpretation.map((c) => <BriefRow key={c.id} c={c} {...props} />)}
            {brief.interpretation.length === 0 && <p className="small steel">No interpretation yet: there was too little to read.</p>}
          </section>
          {props.qloo}{props.audience}
        </div>
      </div>
      <section className="lfb-acc" aria-label="More about your brief">
        <Acc title="Products" sum={items.length ? `${items.length} · ${brief.products?.route === "SHOPIFY_FEED" ? "from your store’s product feed" : "read from your product pages"}` : "none found yet"}>
          {items.map((x) => {
            const ph = photoOf(x);
            const feed = x.discovered_via === "SHOPIFY_FEED";
            return (
              <div className="lfb-acc-row" key={x.id}>
                <span className="steel lfb-prod">
                  {ph.image ? <img className="lfb-thumb" src={`/api/projects/${p.id}/artifacts/${ph.image.sha256}`} alt={`${x.title}, product photo from your store`} />
                    : ph.image_why && <span className="lfb-nophoto">{ph.image_why}</span>}
                  {x.title}</span>
                <span>{[x.price_text, ...x.category_terms, ...x.materials].filter(Boolean).join(" · ") || "—"}</span>
                <button type="button" className="chip chip-sourced chip-link" aria-label={`${feed ? "Feed" : "Source"} for ${x.title}`} onClick={() => props.onProduct(x.id, x.version)}>{feed ? "FEED" : "SOURCE"}</button></div>
            );
          })}
          {!items.length && <p className="small steel lfb-acc-note">No product page could be read among {brief.products?.attempts.length ?? 0} tried. Unknown, not absent.</p>}
        </Acc>
        <Acc title="Where you already sell" sum={soldSum(near.map((c) => c.name), (pres?.countries ?? []).filter((c) => c.present && c.leave_out).map((c) => c.name))}>
          {pres && <PresencePanel p={p} pres={pres} reload={props.reload} catalog={p.goal!.catalog} />}
          {p.goal?.home_unknown_international && <p className="small steel">Your home country isn't known, so no city is left out as home.</p>}
        </Acc>
        <Acc title="Not known yet" sum={`${brief.unknowns.length} · asked only when a decision needs it`}>
          {brief.unknowns.map((u) => (
            <div className="lfb-acc-row" key={u.label}><span className="steel">{u.label}</span><span>{u.why_it_matters}</span><span className="chip chip-unknown">UNKNOWN</span></div>
          ))}
        </Acc>
        {site && read && (
          <Acc title="Sources and pages read" sum={`${read.pages_read} of ${read.pages_tried}${read.pages_failed ? ` · ${read.pages_failed} ${failedWhy}` : ""}`}>
            {read.pages.map((pg) => (
              <div className="lfb-acc-row" key={pg.url}><span className="steel lfb-wrap">{pathOf(pg.url)}</span><span>{pg.ok ? "Read" : `Not read · ${pg.why}`}</span>
                <span className={`chip ${pg.ok ? "chip-sourced" : "chip-failed"}`}>{pg.ok ? "SOURCE" : "UNAVAILABLE"}</span></div>
            ))}
          </Acc>
        )}
        <Acc title="How to read the labels" sum={LEGEND.map(([c]) => c).join(" · ")}>
          {LEGEND.map(([c, d]) => (
            <div className="lfb-acc-row" key={c}><span className="steel">{c}</span><span>{d}</span>
              <span className={`chip chip-${{ SOURCE: "sourced", "YOU SAID": "founder", INTERPRETATION: "interpretation", "TO CHECK": "unknown", UNKNOWN: "unknown", QLOO: "qloo" }[c]}`}>{c}</span></div>
          ))}
        </Acc>
      </section>
    </>
  );
}

function Acc({ title, sum, children }: { title: string; sum: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="lfb-acc-item">
      <button type="button" className="lfb-acc-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span><b>{title}</b><span className="steel"> · {sum}</span></span><span className="lfb-acc-lab">{open ? "Close" : "Open"}</span>
      </button>
      {open && <div className="lfb-acc-body">{children}</div>}
    </div>
  );
}

function inferredFromText(c: Claim) {
  const f = c.inferred_from;
  if (!f) return c.basis || "";
  const parts = [...f.facts.map((x) => x.label), ...f.pages.map((x) => pathOf(x.url))];
  return parts.length ? `Inferred from: ${[...new Set(parts)].join(" · ")}` : "Inferred from: nothing we could trace";
}

const chipFor = (c: Claim) => c.corrected || c.vc === "U" ? ["YOU SAID", "founder"] : c.state === "INFERRED" ? ["INTERPRETATION", "interpretation"]
  : c.state === "SUPPORTED" ? ["SOURCE", "sourced"] : [c.state.replace("_", " "), c.state === "STATED" ? "founder" : "unknown"];

/** Countries where you sell (from your site or the web) and cities with your stores, left out of the ranking. */
function soldSum(cities: string[], countries: string[]) {
  const names = [...countries, ...cities];
  if (!names.length) return "none known";
  return `${names.slice(0, 4).join(", ")}${names.length > 4 ? ` and ${names.length - 4} more` : ""} · left out of the ranking`;
}

// owner, 28 Oct 2026: "consolidate all of Where and What into sections … top 2–3, the rest fan out"
const SECTIONS: [string, string[]][] = [["What you sell", ["What you sell", "Price points"]], ["Where you sell", ["Where you sell", "Shipping"]], ["Who you are", []]];
const LEAD = ["What you sell", "Where you sell", "Founded", "Based in"];  // each section's own question comes before its asides
const LISTS = ["What you sell", "Where you sell"];  // values that are lists of products or retailers
const TOP = 3;
const ORIGIN_RANK: Record<string, number> = { QLOO: 0, SOURCE: 1, FEED: 2, WEB: 3 };  // Qloo > your site > store feed > web
const wordsOf = (s: string) => new Set(s.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter((w) => w.length > 2));
const itemsOf = (s: string) => s.split(/,|;|\band\b/).map((x) => x.trim()).filter(Boolean);
type Entry = { c: Claim; also: Claim[]; named: string[] };

/** Facts into sections, best first (yours, then by source: Qloo > site > feed > web, then checked, the section's own
 *  question, the more specific).
 *  A fact folds into a better entry with the same label when all its words are there, or, for product and retailer
 *  lists, when a checked fact names one of the same items; its new names show as "Also named", its Source stays reachable. */
export function groupFacts(facts: Claim[], origins?: Record<string, string>) {
  const sec = (c: Claim) => (SECTIONS.find(([, ls]) => ls.includes(c.label)) ?? SECTIONS[2])[0];
  // the source's rank comes before checked/to-check, so your own site's description leads a feed product (owner's order)
  const key = (c: Claim) => [c.corrected ? 0 : 1, ORIGIN_RANK[origins?.[c.id] ?? "SOURCE"] ?? 4,
    ["SUPPORTED", "STATED"].includes(c.state) ? 0 : 1, LEAD.includes(c.label) ? 0 : 1, -wordsOf(c.value).size];
  const cmp = (a: Claim, b: Claim) => { const x = key(a), y = key(b); return x.reduce((d, v, i) => d || v - y[i], 0); };
  return SECTIONS.map(([title]) => {
    const entries: Entry[] = [];
    for (const c of facts.filter((f) => sec(f) === title).sort(cmp)) {
      const w = wordsOf(c.value);
      const items = itemsOf(c.value);
      const into = entries.find((e) => e.c.label === c.label && !c.corrected && (
        (w.size > 0 && [...w].every((x) => wordsOf(e.c.value).has(x)))
        || (LISTS.includes(c.label) && c.state === "SUPPORTED" && items.some((x) => [e.c.value, ...e.named].flatMap(itemsOf).some((y) => y.toLowerCase() === x.toLowerCase())))));
      if (!into) { entries.push({ c, also: [], named: [] }); continue; }
      into.also.push(c);
      const have = new Set([into.c.value, ...into.named].flatMap(itemsOf).map((x) => x.toLowerCase()));
      into.named.push(...items.filter((x) => !have.has(x.toLowerCase()) && !wordsOf(into.c.value).has(x.toLowerCase())));
    }
    return { title, entries };
  }).filter((g) => g.entries.length);
}

function FactSection({ g, origins, ...props }: { g: { title: string; entries: Entry[] }; origins?: Record<string, string>; onEvidence: (c: Claim) => void; onInference: (c: Claim) => void; onCorrect: (c: Claim, v: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const more = g.entries.length - TOP;
  return (
    <div className="lfb-sec" role="group" aria-label={g.title}>
      {(open ? g.entries : g.entries.slice(0, TOP)).map((e, i) => (
        <BriefRow key={e.c.id} c={e.c} origin={origins?.[e.c.id]} k={i === 0 ? `${g.title} · ${g.entries.length}` : ""}
          sub={e.c.label === g.title ? undefined : e.c.label} named={e.named} also={e.also.map((c) => ({ c, origin: origins?.[c.id] }))} {...props} />
      ))}
      {more > 0 && <button type="button" className="link lfb-more" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Show fewer" : `${more} more`}</button>}
    </div>
  );
}

const originChip = (c: Claim, origin?: string) =>
  c.state === "SUPPORTED" && !c.corrected && (origin === "FEED" || origin === "WEB") ? [origin, "sourced"] : chipFor(c);

function BriefRow({ c, origin, k, sub, named = [], also = [], onEvidence, onInference, onCorrect }: { c: Claim; origin?: string; k?: string; sub?: string; named?: string[]; also?: { c: Claim; origin?: string }[];
  onEvidence: (c: Claim) => void; onInference: (c: Claim) => void; onCorrect: (c: Claim, v: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(c.value);
  const [err, setErr] = useState<string | null>(null);
  const inferred = c.state === "INFERRED" && !c.corrected;
  // a checked fact shows where it came from: SOURCE (your page) · FEED (your product feed) · WEB (a page the search found)
  const [chip, cls] = originChip(c, origin);
  const more = [...new Map(also.map((o) => originChip(o.c, o.origin)).filter(([x]) => x !== chip).map((x) => [x[0], x])).values()];
  return (
    <div className="lfb-fact">
      <span className="lfb-fact-k">{k ?? c.label}</span>
      <span className="lfb-fact-v">
        {sub && <span className="mono steel">{sub}</span>}
        {editing ? (
          <form className="lfb-correct" onSubmit={async (e) => {
            e.preventDefault();
            try { await onCorrect(c, value); setEditing(false); } catch (x) { setErr((x as Error).message); }
          }}>
            <input aria-label={`Correct ${c.label}`} value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
            <button type="submit" className="btn-secondary" disabled={!value.trim() || value === c.value}>Save</button>
            <button type="button" className="link" onClick={() => { setEditing(false); setValue(c.value); setErr(null); }}>Cancel</button>
            {err && <span className="field-error">{err}</span>}
          </form>
        ) : <b>{c.value}</b>}
        {inferred && <button type="button" className="lfb-from mono steel" onClick={() => onInference(c)}>{inferredFromText(c)}</button>}
        {named.length > 0 && <span className="small steel">Also named: {named.join(", ")}</span>}
        {c.corrected && <span className="mono steel">corrected by you</span>}
      </span>
      <span className="lfb-fact-a">
        <span className={`chip chip-${cls}`}>{chip}</span>
        {more.map(([x, y]) => <span key={x} className={`chip chip-${y}`}>{x}</span>)}
        {inferred && c.inferred_from?.changed.length ? <span className="chip chip-unknown">REVIEW · FACT CHANGED</span> : null}
        {inferred && c.inferred_from && !c.inferred_from.established ? <span className="chip chip-unknown">BASIS NOT ESTABLISHED</span> : null}
        <span className="lfb-fact-links">
          {!inferred && <button type="button" className="link" onClick={() => onEvidence(c)}>Source</button>}
          {also.map((o, i) => <button key={o.c.id} type="button" className="link" aria-label={`Source for ${o.c.value}`} onClick={() => onEvidence(o.c)}>Source {i + 2}</button>)}
          {!editing && <button type="button" className="link" onClick={() => setEditing(true)}>Correct</button>}
        </span>
      </span>
    </div>
  );
}

export function EvidenceDrawer({ claim, demo, site }: { claim: Claim; demo: boolean; site?: string }) {
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
          <div><Chip label={!site || [site, `www.${site}`].includes(new URL(claim.source.url).hostname) || new URL(claim.source.url).hostname.endsWith(`.${site}`)
            ? "YOUR SITE" : "LINKED FROM YOUR SITE"} title={site ? `Not on ${site}: a page your homepage links to` : undefined} /> <span className="mono steel">Read {src ? new Date(src.fetched_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "…"}{demo || src?.replayed ? " · recorded" : ""}</span></div>
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

export function ActivityDrawer({ p }: { p: Project }) {
  const toCheck = p.brief ? p.brief.facts.filter((f) => f.state !== "SUPPORTED" && f.vc !== "U").length : 0;
  return (
    <div className="activity">
      <h2 className="heading">What your team has done</h2>
      <p className="steel">Driven by the same events as the screens: nothing appears here before it happens.</p>
      <div className="tiles">
        <div className="tile"><div className="label steel">MODEL CALLS</div><div className="figure">{p.usage.model_calls}</div>
          <div className="small steel">{p.demo ? "replayed from a recording" : `${p.usage.model_calls} model calls`}</div></div>
        <div className="tile"><div className="label steel">PAGES FETCHED</div><div className="figure">{p.usage.pages_read}</div><div className="small steel">of {p.usage.pages_fetched} attempted, your site</div></div>
        <div className="tile"><div className="label steel">QLOO REQUESTS SENT</div><div className="figure">{p.usage.qloo_sent}</div>
          <div className="small steel">{p.demo ? "replayed from a recording" : `${p.usage.qloo_lookups} lookups · ${p.usage.qloo_cached} from cache`}</div></div>
        <div className="tile"><div className="label steel">TO CHECK</div><div className="figure">{toCheck}</div><div className="small steel">kept as unknown</div></div>
      </div>
      {(["Brand", "Markets", "Opportunities"] as const).map((stage, i) => {
        const items = p.activity.filter((a) => (a.stage || "Brand") === stage);
        return items.length ? (
          <React.Fragment key={stage}>
            <div className="act-stage">0{i + 1} · {stage}</div>
            <ul className="act-list">
              {items.map((a) => (
                <li key={a.seq}>
                  <Chip label={a.kind === "founder" ? "YOU SAID" : a.status || "DONE"} />
                  <div><strong>{a.task}</strong><div className="small">{a.detail}</div>
                    <div className="mono steel">{a.kind === "founder" ? "You" : `${a.role} · ${a.tool}`}</div></div>
                </li>
              ))}
            </ul>
          </React.Fragment>
        ) : null;
      })}
    </div>
  );
}
