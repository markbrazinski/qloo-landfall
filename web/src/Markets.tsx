import { Fragment, useEffect, useRef, useState } from "react";
import { api, type Card35, type CityRow, type Markets, type Notice as NoticeT, type PlaceDetail, type Project, useLive, useProject } from "./api";
import { ActivityDrawer, stagesFor, usageLine } from "./Brand";
import { AudiencePanel, GoalSentence, HomeQuestion, PresencePanel, StandInsPanel, goalLine } from "./Goal";
import { Chip, DecisionBar, DrawerStack, Link, Notice, ProjectHeader, Skeleton, StageNav, navigate, useDrawers } from "./ui";
import { STATUS_LABEL, WorldMap } from "./WorldMap";

const NEXT_BUILD = "City research arrives in the next build";
const ROUTE_LINE: Record<string, (brand: string) => string> = {
  brand_entity: (b) => `Qloo brand profile for ${b}`,
  proposed_profile: () => "a profile of Qloo tags you confirmed",
  description_profile: () => "a profile of Qloo tags from your description",
};
const REASON_CHIP: Record<string, string> = {
  ALREADY_HERE: "YOU'RE ALREADY HERE", OUTSIDE_GOAL: "OUTSIDE YOUR GOAL", NOT_RANKED: "NOT RANKED BY QLOO",
  LEFT_OUT_BY_YOU: "YOU LEFT THIS OUT", NOT_PRIORITISED: "NOT PRIORITISED",
};
const DEFERRED_DEFS: [string, string][] = [
  ["YOU'RE ALREADY HERE", "Qloo lists one of your stores here, your site names it, it's home, or you said so. Left out of the ranking."],
  ["OUTSIDE YOUR GOAL", "Not in the scope you chose on the first page (internationally, domestically or both)."],
  ["NOT RANKED BY QLOO", "Qloo's ranking gave this city no position: no relationship returned, a failed page, or no Qloo destination. Unknown, not rejected."],
  ["NOT PRIORITISED", "Ranked, but not among the first for your goal."],
  ["YOU LEFT THIS OUT", "You asked us to leave this city or country out."],
];

type Drawers = ReturnType<typeof useDrawers>;

export default function MarketsPage({ id, view }: { id: string; view: "auto" | "scan" | "all" }) {
  const { project: p, reload: reloadProject } = useProject(id);
  const { data: m, error, reload } = useLive(id, api.markets);
  const drawers = useDrawers();
  if (error && !m) return <div className="app"><main className="page"><div className="error-panel card">{error} <a className="link" href="/">Start again</a></div></main></div>;
  if (!p || !m) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const brand = p.brief?.brand_name || p.site;
  const both = () => { reload(); reloadProject(); };
  const openActivity = () => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> });
  const scanning = ["queued", "running"].includes(m.run?.status || "");
  const mode = !m.generation ? "start" : view === "all" ? "all" : view === "scan" || scanning ? "scan" : "shortlist";
  return (
    <div className="app">
      <ProjectHeader brand={brand} site={p.site} intent={goalLine(p)}
        briefVersion={p.brief?.version} running={scanning || ["queued", "running"].includes(m.shortlist_run?.status || "")}
        usage={usageLine(p)} demo={p.demo} onBrief={() => navigate(`/p/${id}`)} onActivity={openActivity} />
      <StageNav stages={stagesFor(p)} current={1} />
      <main id="main">
        {mode === "start" && <StartPanel p={p} m={m} reload={reload} />}
        {mode === "scan" && <ScanView p={p} m={m} brand={brand} drawers={drawers} reload={both} />}
        {mode === "shortlist" && <ShortlistView p={p} m={m} brand={brand} drawers={drawers} reload={both} />}
        {mode === "all" && <AllCities p={p} m={m} drawers={drawers} reload={both} />}
      </main>
      <DrawerStack d={drawers} />
    </div>
  );
}

function StartPanel({ p, m, reload }: { p: Project; m: Markets; reload: () => void }) {
  return (
    <DecisionBar kicker="MARKETS" title="Explore 80 cities for places related to your brand"
      subtitle="The Market scout asks Qloo, once per city, for places related to your discovery profile, and Qloo ranks your candidate cities. Nothing else is needed from you."
      primary={m.can_start && !p.markets_reason ? { label: "Start exploring markets", onClick: () => api.scan(p.id).then(() => { navigate(`/p/${p.id}/markets/scan`); reload(); }) }
        : { label: "Start exploring markets", disabledReason: p.markets_reason || "Confirm your discovery profile first." }} />
  );
}

// ---- shared bits ----
function coverageLine(m: Markets) {
  const c = m.coverage!;
  return `${c.catalog} in catalog · ${c.attempted} attempted · ${c.returned} returned · ${c.empty} empty · ${c.failed} failed · ${c.unresolved} unresolved`;
}

function routeLine(m: Markets, brand: string) {
  return (ROUTE_LINE[m.generation!.route] || ROUTE_LINE.proposed_profile)(brand);
}

function findingText(c: CityRow) {
  if (c.status === "returned") return `Qloo returned ${c.finding}.`;
  if (c.status === "empty") return "Qloo returned no related places in this search. That describes the search, not the market.";
  if (c.status === "unresolved") return `Qloo couldn't search this city area (${c.reason}). Unknown, not rejected.`;
  if (c.status === "failed") return `The search failed: ${c.reason}${c.attempts ? ` · ${c.attempts} attempt${c.attempts > 1 ? "s" : ""}` : " · not sent"}.`;
  return c.status === "searching" ? "Searching…" : "Queued";
}

function openPlace(drawers: Drawers, pid: string, qloo_id: string, city: string) {
  drawers.push({ key: `place-${qloo_id}`, kicker: "QLOO PLACE", render: () => <PlaceDrawer pid={pid} qloo_id={qloo_id} city={city} /> });
}

const ACTION_LABEL: Record<string, (n: NoticeT) => string> = {
  retry_scan: () => "Retry failed cities", retry_shortlist: () => "Try again", rescan: () => "Re-scan",
  retry_ranking: () => "Retry ranking · 1–2 Qloo requests",
  update: (n) => `Update the recommendations · ${n.rerank ? "re-ranks with 1–2 Qloo requests, no place scan" : "no new Qloo searches"}`,
};

function ActionNotices({ p, m, reload }: { p: Project; m: Markets; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try { await fn(); reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const call: Record<string, () => Promise<unknown>> = {
    retry_scan: () => api.retryScan(p.id), retry_shortlist: () => api.retryShortlist(p.id), update: () => api.updateShortlist(p.id),
    rescan: () => api.scan(p.id), retry_ranking: () => api.retryRanking(p.id),
  };
  return (
    <>
      {m.notices.map((n, i) => n.action === "home" && p.goal ? <HomeQuestion key={i} p={p} goal={p.goal} reload={reload} /> : (
        <div key={i} className={`notice ${n.kind === "failed" ? "notice-failed" : "notice-changed"}`} role="status">
          <Chip label={n.kind === "failed" ? "FAILED" : "CHANGED"} />
          <span>{n.text}</span>
          {n.action && call[n.action] && (
            <button type="button" className="btn-secondary notice-action" disabled={busy} onClick={() => act(call[n.action!])}>
              {ACTION_LABEL[n.action](n)}
            </button>
          )}
        </div>
      ))}
      {err && <p className="field-error">{err}</p>}
    </>
  );
}

function rankingRow(m: Markets) {
  const r = m.ranking;
  const pend = m.ranking_pending;
  if (pend?.needs_home) return "Qloo ranks your candidate cities once you say which country is home.";
  if (r && !pend?.running) return r.state === "ranked" ? `Qloo ranked your ${r.n_candidates} candidate cities in one query for ${r.signal_label}.` : r.text;
  if (pend?.running) return `Qloo is ranking your ${pend.n_candidates} candidate cities for ${pend.signal_label || "your brand"}.`;
  return "Qloo ranks your candidate cities alongside the scan.";
}

// ---- S04 scan (LF-ScanProgress, LF-WorldMap, LF-EmergingFindings, LF-ScanCompleteBanner) ----
function ScanView({ p, m, brand, drawers, reload }: { p: Project; m: Markets; brand: string; drawers: Drawers; reload: () => void }) {
  const c = m.coverage!;
  const done = c.returned + c.empty + c.failed + c.unresolved;
  const [sel, setSel] = useState<string | null>(null);
  const active = ["queued", "running"].includes(m.run?.status || "");
  const finished = !active && ["completed", "partial"].includes(m.run?.status || "");
  const writing = ["queued", "running"].includes(m.shortlist_run?.status || "");
  const recent = m.cities.filter((x) => !["queued", "searching"].includes(x.status)).reverse();
  const selected = m.cities.find((x) => x.city_id === sel);
  const n = m.assessment?.cards.length ?? 0;
  return (
    <>
      <section className="decision-bar scan-bar">
        <div className="scan-head">
          <div className="kicker">MARKETS · BROAD SCAN · {done} OF {c.catalog} CITIES</div>
          <h1>Scanning {c.catalog} cities for places related to {brand}</h1>
          <p className="subtitle">Discovery route: {routeLine(m, brand)}. Related places suggest where research is worth effort; they don't show demand. Nothing needs your input.</p>
          <div className="scan-progress">
            <div className="progress" role="progressbar" aria-label="Cities searched" aria-valuemin={0} aria-valuemax={c.catalog} aria-valuenow={done}>
              <span style={{ width: `${(done / c.catalog) * 100}%` }} />
            </div>
            <span className="mono steel">{done} of {c.catalog} · {Math.round((done / c.catalog) * 100)}%</span>
          </div>
          <p className="ranking-row" aria-live="polite"><Chip label={m.ranking_pending?.running ? "RUNNING" : m.ranking ? (m.ranking.state === "ranked" ? "QLOO" : "FAILED") : "QUEUED"} /> {rankingRow(m)}</p>
        </div>
      </section>
      {!active && m.notices.length > 0 && <div className="page page-tight"><ActionNotices p={p} m={m} reload={reload} /></div>}
      <div className="page scan-layout">
        <div className="card">
          <h2 className="heading-sm">{c.catalog} cities · places related to {brand}, from Qloo</h2>
          <WorldMap cities={m.cities} home={m.home} variant="scan"
            selected={sel} onSelect={setSel} labels={m.cities.filter((x) => x.pick?.included).map((x) => x.city_id)} />
          <p className="mono coverage" aria-live="polite">{coverageLine(m)}</p>
          <ul className="map-legend">
            {(["returned", "searching", "queued", "empty", "failed", "unresolved"] as const).map((s) => (
              <li key={s}><span className={`legend-dot dot-${s}`} aria-hidden />{STATUS_LABEL[s].toLowerCase()} <strong>{c[s]}</strong></li>
            ))}
            <li><span className="legend-home" aria-hidden />home</li>
          </ul>
          <p className="small steel">A city fills in only when its Qloo request returns. The number of related places describes what each search returned; it is never used as a score.</p>
          {selected && <CityDetail p={p} c={selected} drawers={drawers} />}
        </div>
        <div className="card">
          <div className="card-head"><h2 className="heading-sm">Emerging</h2><span className="mono steel">newest first</span></div>
          {recent.length === 0 && <p className="small steel">Findings appear as each city returns.</p>}
          <ul className="findings">
            {recent.slice(0, 14).map((x) => (
              <li key={x.city_id}>
                <div className="finding-head"><strong>{x.name}</strong><Chip label={STATUS_LABEL[x.status]} /></div>
                <p className="small">{findingText(x)}</p>
              </li>
            ))}
          </ul>
          {recent.length > 14 && <p className="small steel">and {recent.length - 14} more · see all {c.catalog} after the scan</p>}
          {m.usage && !p.demo && <p className="mono steel">Qloo {m.usage.sent} sent · {m.usage.lookups} lookups ({m.usage.cached} from cache)</p>}
        </div>
      </div>
      {finished && (
        <div className="scan-banner" role="status">
          <p><strong>Scan finished. {writing ? "The recommendations are being written." : m.assessment
            ? `${n} cit${n === 1 ? "y is" : "ies are"} recommended for closer research.`
            : "The recommendations didn't finish."}</strong> Nothing has moved. Open them when you are ready.</p>
          <button type="button" className="btn-primary" onClick={() => navigate(`/p/${p.id}/markets`)}>
            {writing ? "See the recommendations as they arrive" : "See the recommendations"}
          </button>
        </div>
      )}
    </>
  );
}

function CityDetail({ p, c, drawers, onAdd }: { p: Project; c: CityRow; drawers: Drawers; onAdd?: () => void }) {
  return (
    <div className="city-detail" aria-live="polite">
      <div className="finding-head"><h3 className="subheading">{c.name}</h3><Chip label={STATUS_LABEL[c.status]} />
        {c.pick?.included && <Chip label={c.pick.added_by_founder ? "ADDED BY YOU" : "RECOMMENDED"} />}</div>
      <p className="small steel">{c.region} · {c.fit.distance_km != null ? `${c.fit.distance_km.toLocaleString()} km from home` : "distance not known"}</p>
      <p className="small">{findingText(c)}</p>
      <div className="row-inline"><Chip label={c.entry_label} /><span className="small">{c.fit_line}</span></div>
      {c.digest && c.digest.top_places.length > 0 && (
        <>
          <div className="label steel">LOCAL LEADS · NOT CHECKED</div>
          <ul className="lead-list">
            {c.digest.top_places.slice(0, 5).map((pl) => (
              <li key={pl.qloo_id}><button type="button" className="link" onClick={() => openPlace(drawers, p.id, pl.qloo_id, c.city_id)}>{pl.name}</button>
                <span className="small steel"> {pl.genres.join(" · ")}</span></li>
            ))}
          </ul>
        </>
      )}
      {c.deferred && <p className="small"><Chip label={REASON_CHIP[c.deferred.reason] || c.deferred.reason} /> {c.deferred.line}</p>}
      {onAdd && !c.pick?.included && <button type="button" className="btn-secondary" onClick={onAdd}>Research {c.name} too</button>}
    </div>
  );
}

// ---- S05 recommendations ----
const NO_ORDER: Record<string, string> = {
  failed: "Qloo's ranking failed, so there is no order to recommend from.", denied: "Qloo's ranking wasn't sent: the operating allowance is reached.",
  empty: "Qloo's ranking came back empty.", flat: "Qloo's ranking carried no signal: every city had the same value.",
  no_signal: "Qloo doesn't know your brand, and no brand with a similar following is chosen. Add one on your brief.",
  no_candidates: "No city is left to rank: every catalog city is outside your goal, already yours or left out.",
  needs_home: "Tell us which country is home to rank cities at home.",
};

function ShortlistView({ p, m, brand, drawers, reload }: { p: Project; m: Markets; brand: string; drawers: Drawers; reload: () => void }) {
  const [changed, setChanged] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const a = m.assessment;
  const sl = m.shortlist;
  const picks = Object.fromEntries((sl?.cities || []).map((c) => [c.city_id, c]));
  const cards = a?.cards || [];
  const included = sl?.included ?? 0;
  const writing = ["queued", "running"].includes(m.shortlist_run?.status || "");
  const toggle = async (cid: string, on: boolean) => {
    setErr(null);
    try {
      await api.editShortlist(p.id, sl?.version ?? 0, on ? { include: [cid] } : { exclude: [cid] });
      const name = m.cities.find((x) => x.city_id === cid)!.name;
      const k = included + (on ? 1 : -1);
      setChanged(on ? `${name} added back to research. Research now covers ${k} cit${k === 1 ? "y" : "ies"}.`
        : `${name} removed from research. Its evidence stays here; tick it to add it back. Research now covers ${k} cit${k === 1 ? "y" : "ies"}.`);
      reload();
    } catch (e) { setErr((e as Error).message); }
  };
  const added = (sl?.cities || []).filter((c) => c.added_by_founder && !cards.some((x) => x.city_id === c.city_id));
  const n = cards.length;
  const ticked = cards.filter((c) => picks[c.city_id]?.included !== false).length;
  const plus = added.filter((x) => x.included).length;
  const research = `Research ${ticked === n && n > 0 ? `all ${n}` : `${ticked} of ${n}`} recommended cit${n === 1 ? "y" : "ies"}${plus ? ` + ${plus} added` : ""}`;
  const scrollTo = (cid: string) => document.getElementById(`card-${cid}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const editGoal = async (c: { scope?: "international" | "domestic" | "both"; purpose?: "presence" | "stockists" | "test_demand" }) => {
    setErr(null);
    try { setChanged((await api.intent(p.id, p.intent!.version, c)).notice); reload(); } catch (e) { setErr((e as Error).message); }
  };
  const noOrder = a && n === 0 ? NO_ORDER[a.ranking_state] : null;
  return (
    <>
      <DecisionBar kicker="MARKETS · RECOMMENDATIONS · PROVISIONAL"
        title={a ? (n ? `${n} cit${n === 1 ? "y" : "ies"} recommended for research` : "No city recommended yet") : writing ? "Writing the recommendations" : "The recommendations aren't ready"}
        subtitle={a ? `Ordered by one Qloo ranking for ${a.signal.label || brand}. ${included === n && !added.length ? (n ? `All ${n} are selected. Continue as is, or untick any you'd rather skip.` : "") : `${included} of ${n + added.length} selected.`}`
          : writing ? "Qloo is ranking your candidate cities and the Market strategist is explaining them. This takes a minute." : undefined}
        secondary={<Link to={`/p/${p.id}/markets/all`} className="btn-secondary btn-lg">See all {m.coverage!.catalog} cities</Link>}
        primary={{ label: research, disabledReason: NEXT_BUILD }} />
      <div className="page">
        {p.intent && <div className="goal-bar"><GoalSentence scope={p.intent.scope} purpose={p.intent.purpose} onChange={editGoal} /></div>}
        <ActionNotices p={p} m={m} reload={reload} />
        {changed && <Notice kind="you-changed">{changed}</Notice>}
        {err && <p className="field-error">{err}</p>}
        <div className="shortlist-layout">
          <div className="stack">
            {!a && writing && <div className="card"><Skeleton width="70%" /><Skeleton width="50%" /></div>}
            {noOrder && (
              <div className="card empty-state">
                <h2 className="heading-sm">No order to recommend from</h2>
                <p>{noOrder} Nothing is padded or guessed. The scan is kept: see every city and add any you want researched.</p>
                <Link to={`/p/${p.id}/markets/all`} className="btn-secondary">See all {m.coverage!.catalog} cities</Link>
              </div>
            )}
            {a && n > 0 && (
              <section className="card-frame priority" aria-labelledby="rp-h">
                <div className="priority-top"><Chip label="RESEARCH PRIORITY" /><span id="rp-h" className="small steel">Where to look first, from Qloo's ranking. It changes as evidence comes in.</span></div>
                <p className="lead-text">{a.lead.text}</p>
                {a.lead.source === "DETERMINISTIC" && <p className="mono steel">Lead written by Landfall from the ranking{a.model_error ? "; the Market strategist didn't answer" : "; the strategist's lead didn't pass the checks"}.</p>}
                <p className="roster"><span className="label steel">RECOMMENDED · {n}</span>
                  {cards.map((c) => (
                    <span key={c.city_id} className={picks[c.city_id]?.included === false ? "struck" : ""}>
                      <button type="button" className="link-plain" onClick={() => scrollTo(c.city_id)}>{c.name}</button> · {c.role_label.toLowerCase().replace(/^./, (x) => x.toUpperCase())}
                    </span>
                  ))}
                </p>
                <div className="panels">
                  <div><div className="label steel">SIGNALS WE USED</div>
                    <p className="small">Qloo ranked your {a.n_candidates} candidate cities for {a.signal.label}. Places come from one search per city, 15 km around it, with {routeLine(m, brand)}.</p>
                    {a.audience_names.length > 0 && <p className="small"><Chip label="QLOO" /> Audiences: {a.audience_names.join(", ")}. {m.audience.label}.</p>}</div>
                  <div><div className="label steel">WHY THIS ORDER?</div><p className="small">{a.why_order}</p></div>
                  <div><div className="label steel">WHAT SETS THEM APART</div>
                    <ul className="plain">{cards.map((c) => <li key={c.city_id} className="small"><strong>{c.name}:</strong> {c.different}</li>)}</ul></div>
                  <div><div className="label steel">STILL TO ESTABLISH</div><p className="small">{a.still}</p>
                    {a.coverage !== "complete" && a.coverage !== "not_applicable" && <p className="small">{m.presence.coverage_line}: where we couldn't confirm, the card says so.</p>}</div>
                </div>
                <p className="mono steel coverage">{coverageLine(m)}</p>
              </section>
            )}
            {a && a.groups.map((g) => {
              const gc = cards.filter((c) => c.group === g.key);
              return (
                <Fragment key={g.key}>
                  {(a.groups.length > 1 || gc.length > 0) && <h2 className="label steel">{g.label.toUpperCase()} · {gc.length} OF UP TO {g.cap}</h2>}
                  {a.groups.length > 1 && gc.length === 0 && n > 0 && <p className="small card">No city {g.key === "home" ? "at home" : "abroad"} came back ranked, so this group has no recommendation. Nothing is moved over from the other group.</p>}
                  <div className="card-grid">{gc.map((c) => <CityCardView key={c.city_id} p={p} c={c} pick={picks[c.city_id]} drawers={drawers} onToggle={toggle} />)}</div>
                  {gc.length > 0 && gc.length < g.cap && <p className="cap-note">Only {gc.length} cit{gc.length === 1 ? "y" : "ies"} {gc.length === 1 ? "was" : "were"} ranked here{g.available > gc.length ? " after the checks" : ""}. The list isn't padded to {g.cap}.</p>}
                </Fragment>
              );
            })}
            {added.length > 0 && <h2 className="label steel">ADDED BY YOU · {added.length}</h2>}
            <div className="card-grid">{added.map((pk) => {
              const row = m.cities.find((x) => x.city_id === pk.city_id)!;
              return (
                <article key={pk.city_id} id={`card-${pk.city_id}`} className={`card city-card ${pk.included ? "" : "excluded"}`}>
                  <div className="cc-top"><Chip label="ADDED BY YOU" /><IncludeBox name={row.name} on={pk.included} onChange={(v) => toggle(pk.city_id, v)} /></div>
                  <h3 className="heading">{row.name}</h3>
                  <p className="small steel">{row.region} · not recommended: you added it, so it has its search and fit but no explanation</p>
                  <p className="small">{findingText(row)}</p>
                  <div className="row-inline"><Chip label={row.entry_label} /><span className="small">{row.fit_line}</span></div>
                </article>
              );
            })}</div>
            {a && <Deferred m={m} />}
          </div>
          <aside className="stack">
            <div className="card">
              <div className="card-head"><h2 className="heading-sm">Where we looked</h2><Link to={`/p/${p.id}/markets/scan`} className="link">Expand map</Link></div>
              <WorldMap cities={m.cities} home={m.home} variant="shortlist" compact
                labels={m.cities.filter((x) => x.pick?.included).map((x) => x.city_id)} />
              <ul className="region-counts">
                {m.regions.map((r) => {
                  const rs = m.cities.filter((c) => c.region === r);
                  return <li key={r}><span>{r}</span><span className="mono steel">{rs.length} · {rs.filter((c) => c.pick?.included).length} in research · {rs.filter((c) => c.excluded === "ALREADY_HERE").length} already yours</span></li>;
                })}
              </ul>
            </div>
            <div className="card"><h2 className="heading-sm">Where you already are</h2><PresencePanel p={p} pres={m.presence} reload={reload} /></div>
            {m.standins && <div className="card"><h2 className="heading-sm">Brands with a similar following</h2><StandInsPanel p={p} si={m.standins} reload={reload} /></div>}
            <div className="card"><h2 className="heading-sm">Who likes you, according to Qloo</h2><AudiencePanel p={p} aud={m.audience} reload={reload} /></div>
          </aside>
        </div>
      </div>
    </>
  );
}

function IncludeBox({ name, on, onChange }: { name: string; on: boolean; onChange: (v: boolean) => void }) {
  const [shown, setShown] = useState(on);  // answers the click at once; the stored shortlist re-syncs it
  useEffect(() => setShown(on), [on]);
  return (
    <label className="include">
      <input type="checkbox" checked={shown} aria-label={`Research ${name}`}
        onChange={(e) => { setShown(e.target.checked); onChange(e.target.checked); }} /> In research
    </label>
  );
}

function storesText(b: { name: string; stores: number | null; capped: boolean; state: string }) {
  if (b.state !== "ok" || b.stores == null) return `${b.name}: not checked`;
  return `${b.name}: ${b.capped ? `at least ${b.stores}` : b.stores} store${b.stores === 1 ? "" : "s"}`;
}

function CityCardView({ p, c, pick, drawers, onToggle }: {
  p: Project; c: Card35; pick?: { included: boolean; added_by_founder: boolean }; drawers: Drawers; onToggle: (cid: string, on: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const on = pick?.included ?? true;
  const top = c.role === "TOP_RECOMMENDATION";
  const det = Object.values(c.prose_source).includes("DETERMINISTIC");
  return (
    <article id={`card-${c.city_id}`} className={`card city-card ${top ? "featured" : ""} ${on ? "" : "excluded"}`} aria-label={c.name}>
      <div className="cc-top"><span className="tag-chips"><Chip label={c.role_label} />{c.moved_by_purpose && <Chip label="MOVED FOR YOUR GOAL" />}
        {pick?.added_by_founder && <Chip label="ADDED BY YOU" />}</span>
        <IncludeBox name={c.name} on={on} onChange={(v) => onToggle(c.city_id, v)} /></div>
      <h3 className={top ? "title" : "heading"}>{c.name}</h3>
      <p className="small steel">{c.region} · {c.fit.distance_km != null ? `${c.fit.distance_km.toLocaleString()} km from home` : "distance not known"}</p>
      <p className="position-line"><Chip label="QLOO" /> {c.position_line}</p>
      {c.moved_line && <p className="small">{c.moved_line}</p>}
      <p className={top ? "why-lg" : ""}>{c.why}</p>
      {c.scan_missing && <p className="small"><Chip label="NOT KNOWN" /> Qloo's place search didn't return here, so places are unknown; this rests on the ranking.</p>}
      {c.located_check === "failed" && <p className="small"><Chip label="NOT CHECKED" /> We couldn't confirm whether you already have a store here.</p>}
      <p className="small"><strong>Different from the others:</strong> {c.different}</p>
      <dl className="criteria-rows">
        <dt>Related-brand stores</dt><dd>{c.comparables.length ? <><Chip label="QLOO" /> {c.comparables.map(storesText).join(" · ")} within 15 mi</> : "none checked"}</dd>
        <dt>Possible ways in</dt><dd>{c.ways_in}</dd>
        <dt>Entry</dt><dd><Chip label={c.entry_label} /> {c.entry_line}{c.key_unknown ? " · part of the purpose check is not known and counted as no" : ""}</dd>
      </dl>
      <div className="row-inline"><Chip label="COULD CHANGE IT" /><span className="small">{c.could_change}</span></div>
      <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>Why this city?</button>
      {open && (
        <div className="trail-wrap">
          <ol className="trail" aria-label={`Evidence trail for ${c.name}`}>
            <li><div className="label steel">1 · QLOO'S RANKING</div>
              <div className="trail-row"><span className="small">{c.position_line} Qloo's position: {c.qloo_position}; in this list: {c.final_position}.</span><Chip label="QLOO" /></div></li>
            <li><div className="label steel">2 · WHAT QLOO RETURNED HERE</div>
              {c.places.length === 0 && <p className="small steel">No places: the search {c.scan_status === "empty" ? "returned none" : "failed"}.</p>}
              {c.places.map((pl) => <div key={pl.qloo_id} className="trail-row"><span className="small">{pl.name}{pl.genres.length ? ` · ${pl.genres.slice(0, 2).join(", ")}` : ""}</span>
                <Chip label="QLOO" onClick={() => openPlace(drawers, p.id, pl.qloo_id, c.city_id)} title="A cultural relationship, not demand" /></div>)}</li>
            <li><div className="label steel">3 · RELATED-BRAND STORES</div>
              {c.comparables.map((b) => <div key={b.name} className="trail-row"><span className="small">{storesText(b)}</span><Chip label={b.state === "ok" ? "QLOO" : "NOT CHECKED"} /></div>)}</li>
            <li><div className="label steel">4 · THE EXPLANATION</div>
              <div className="trail-row"><span className="small">{det ? "Some text is Landfall's own, from the evidence above (the model's didn't pass the checks or didn't come)." : "Written by the Market strategist from the evidence above and checked."}</span><Chip label={det ? "DETERMINISTIC" : "TEAM READ"} /></div></li>
          </ol>
          <p className="mono steel">QLOO = what Qloo returned · TEAM READ = Landfall's interpretation · a ranking position is a cultural relationship, not demand</p>
        </div>
      )}
    </article>
  );
}

function Deferred({ m }: { m: Markets }) {
  const rows = m.cities.filter((c) => c.deferred);
  const order = ["NOT_PRIORITISED", "NOT_RANKED", "ALREADY_HERE", "LEFT_OUT_BY_YOU", "OUTSIDE_GOAL"];
  const shown = order.flatMap((r) => rows.filter((c) => c.deferred!.reason === r)).slice(0, 24);
  return (
    <details className="card deferred">
      <summary className="subheading">Why these cities over the others?</summary>
      <ul className="plain">
        {shown.map((c) => <li key={c.city_id}><strong>{c.name}</strong> <Chip label={REASON_CHIP[c.deferred!.reason] || c.deferred!.reason} /> <span className="small">{c.deferred!.line}</span></li>)}
      </ul>
      {rows.length > shown.length && <p className="small steel">{rows.length - shown.length} more: see all {m.catalog} cities.</p>}
      <dl className="checks defs">{DEFERRED_DEFS.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
    </details>
  );
}

// ---- S06 all 80 (LF-AllCitiesList, LF-UnresolvedPanel, LF-CityDetail) ----
const FILTERS = [["all", "All 80"], ["proposed", "Recommended"], ["not", "Not recommended"], ["here", "Already yours"], ["unresolved", "Unresolved"]] as const;

function AllCities({ p, m, drawers, reload }: { p: Project; m: Markets; drawers: Drawers; reload: () => void }) {
  const initial = new URLSearchParams(window.location.search).get("filter");
  const [filter, setFilter] = useState<string>(FILTERS.some(([k]) => k === initial) ? initial! : "all");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => { searchRef.current?.focus(); }, []);
  const sl = m.shortlist;
  const add = async (c: CityRow) => {
    setErr(null);
    try {
      await api.editShortlist(p.id, sl?.version ?? 0, { add: [c.city_id] });
      setNote(c.pick ? `${c.name} is back in the research.`
        : `${c.name} added to the research. It's marked ADDED BY YOU; it never becomes a recommendation, and it has its search and fit but no explanation.`);
      reload();
    } catch (e) { setErr((e as Error).message); }
  };
  const match = (c: CityRow) => (!q || `${c.name} ${c.region}`.toLowerCase().includes(q.toLowerCase())) && (
    filter === "all" || (filter === "proposed" && c.proposed) || (filter === "not" && !c.proposed && c.status !== "unresolved")
    || (filter === "here" && c.excluded === "ALREADY_HERE") || (filter === "unresolved" && c.status === "unresolved"));
  const rows = m.cities.filter(match);
  const groups = m.regions.map((r) => [r, rows.filter((c) => c.region === r)] as const).filter(([, rs]) => rs.length);
  const included = sl?.included ?? 0;
  const unresolved = m.cities.filter((c) => c.status === "unresolved");
  const selected = m.cities.find((c) => c.city_id === sel);
  return (
    <>
      <DecisionBar kicker={`MARKETS · ALL ${m.catalog} CITIES`} title="Every city we scanned"
        subtitle="Including cities where you already are, outside your goal, or not ranked by Qloo. Add any of them to the research."
        secondary={<Link to={`/p/${p.id}/markets`} className="btn-secondary btn-lg">Back to the recommendations</Link>}
        primary={{ label: `Research ${included} cit${included === 1 ? "y" : "ies"}`, disabledReason: NEXT_BUILD }} />
      <div className="page">
        {note && <Notice kind="you-changed">{note}</Notice>}
        {err && <p className="field-error">{err}</p>}
        <div className="shortlist-layout">
          <div className="stack">
            <div className="filter-row">
              <input ref={searchRef} className="input" type="search" placeholder={`Search ${m.catalog} cities`} aria-label={`Search ${m.catalog} cities`}
                value={q} onChange={(e) => setQ(e.target.value)} />
              <div className="pills" role="group" aria-label="Filter cities">
                {FILTERS.map(([k, l]) => <button key={k} type="button" className={`pill ${filter === k ? "pill-on" : ""}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>)}
              </div>
            </div>
            {filter === "unresolved" && (
              <div className="card unresolved-panel">
                <div className="finding-head"><Chip label="UNRESOLVED" /><strong>{unresolved.length === 0 ? "Every city could be searched" : `${unresolved.length} cit${unresolved.length === 1 ? "y" : "ies"} Qloo couldn't search`}</strong></div>
                <p className="small">{unresolved.length === 0 ? "Qloo returned an answer for every city area in this scan." : "Nothing came back for these city areas, so they are unknown, not rejected. Add one and the team researches it by another route, without Qloo's places."}</p>
              </div>
            )}
            {groups.length === 0 && <p className="card">No city matches '{q}'.</p>}
            {groups.map(([r, rs]) => (
              <section key={r} className="card region" aria-label={r}>
                <div className="card-head"><h2 className="heading-sm">{r}</h2><span className="mono steel">{rs.length} shown</span></div>
                <ul className="city-rows">
                  {rs.map((c) => {
                    const chip = c.pick?.added_by_founder ? "ADDED BY YOU" : c.proposed ? "RECOMMENDED" : c.deferred ? (REASON_CHIP[c.deferred.reason] || c.deferred.reason) : STATUS_LABEL[c.status];
                    return (
                      <li key={c.city_id} className={sel === c.city_id ? "row-selected" : ""}>
                        <button type="button" className="link-plain city-name" onClick={() => setSel(c.city_id)}>{c.name}</button>
                        <Chip label={chip} />
                        <span className="small">{c.deferred?.line && !c.pick?.included ? c.deferred.line : findingText(c)}</span>
                        {c.pick?.included ? <span className="mono steel">in research</span>
                          : <button type="button" className="link" onClick={() => add(c)} aria-label={`Research ${c.name} too`}>
                            {c.pick ? "Add back to research" : c.status === "unresolved" ? "Try another route" : "Research this too"}</button>}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
          <aside className="stack">
            <div className="card">
              <h2 className="heading-sm">Where we looked</h2>
              <WorldMap cities={m.cities} home={m.home} variant="shortlist" compact selected={sel}
                labels={m.cities.filter((x) => x.pick?.included).map((x) => x.city_id)} onSelect={setSel} />
              {selected ? <CityDetail p={p} c={selected} drawers={drawers} onAdd={() => add(selected)} />
                : <p className="small steel">Select a city to see what Qloo returned there.</p>}
            </div>
          </aside>
        </div>
      </div>
    </>
  );
}

// ---- Qloo place drawer ----
function PlaceDrawer({ pid, qloo_id, city }: { pid: string; qloo_id: string; city: string }) {
  const [d, setD] = useState<PlaceDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.place(pid, qloo_id, city).then(setD).catch((e) => setErr(e.message)); }, [pid, qloo_id, city]);
  if (err) return <p className="field-error">{err}</p>;
  if (!d) return <Skeleton width="60%" />;
  const pl = d.place;
  return (
    <div className="evidence">
      <div className="label steel">QLOO PLACE · {d.city.name.toUpperCase()}</div>
      <h2 className="heading">{pl.name}</h2>
      <span className="tag-chips"><Chip label="QLOO" /><Chip label="NOT CHECKED" /></span>
      <dl className="checks">
        <dt>Kind</dt><dd>{pl.genres.map((g) => g.name).join(" · ") || "no category from Qloo"} ({d.class})</dd>
        <dt>Address</dt><dd>{pl.address || "not given"}</dd>
        <dt>Website</dt><dd>{pl.website ? <a className="source-url" href={pl.website} target="_blank" rel="noreferrer noopener">{pl.website}</a> : "not given"}</dd>
        <dt>In this search</dt><dd>{d.request.rank_note}{pl.distance_m != null ? ` · ${(pl.distance_m / 1000).toFixed(1)} km from the city point` : ""}</dd>
        <dt>Searched with</dt><dd>{d.request.signals.map((s) => s.name).join(", ")} · {d.request.radius_m / 1000} km</dd>
        <dt>Fetched</dt><dd>{new Date(d.request.fetched_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })} · {d.request.origin === "replay" ? "recorded" : d.request.origin}</dd>
      </dl>
      <div className="means">
        <p className="small">A cultural relationship, not demand: Qloo relates this place to your discovery signals. It says nothing about sales, audience size or whether the place would work with you. Nobody has checked it yet.</p>
      </div>
    </div>
  );
}
