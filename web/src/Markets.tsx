import { Fragment, type ReactNode, useEffect, useState } from "react";
import { api, type Card35, type CityRow, type Markets, type Notice as NoticeT, type PlaceDetail, type Project, useLive, useProject } from "./api";
import { ActivityDrawer, stagesFor, usageLine } from "./Brand";
import { ContextEdit, ContextStrip, HomeQuestion, goalLine } from "./Goal";
import { Chip, DecisionBar, DrawerStack, Link, ProjectHeader, Skeleton, StageNav, navigate, useDrawers } from "./ui";
import { STATUS_LABEL, WorldMap } from "./WorldMap";
import { once, useMinPlay } from "./pace";
import "./markets.css";

const REASON_CHIP: Record<string, string> = {
  ALREADY_HERE: "YOU'RE ALREADY HERE", OUTSIDE_GOAL: "OUTSIDE YOUR GOAL", NOT_RANKED: "NOT RANKED BY QLOO",
  LEFT_OUT_BY_YOU: "YOU LEFT THIS OUT", NOT_PRIORITISED: "NOT PRIORITISED",
};

type Drawers = ReturnType<typeof useDrawers>;
const SCAN_MIN_MS = 8000;  // owner, 28 Oct 2026: the scan plays at least 8 s even when its work was done in advance

export default function MarketsPage({ id, view }: { id: string; view: "auto" | "scan" | "all" | "compare" }) {
  const { project: p, reload: reloadProject } = useProject(id);
  const { data: m, error, reload } = useLive(id, api.markets);
  const drawers = useDrawers();
  // once per scan in a session, until a beachhead is chosen: arriving at Markets plays the scan first
  const t = useMinPlay(m?.generation && `scan-${m.generation.id}-v${m.generation.version}`, SCAN_MIN_MS, !m?.beachhead);
  const replay = view === "auto" && !!m?.generation && t < 1;
  useEffect(() => {  // it stays on the scan, finished banner and all, once the play ends
    if (!replay) return;
    window.history.replaceState({}, "", `/p/${id}/markets/scan`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, [replay, id]);
  if (error && !m) return <div className="app"><main className="page"><div className="error-panel card">{error} <a className="link" href="/">Start again</a></div></main></div>;
  if (!p || !m) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const brand = p.brief?.brand_name || p.site;
  const both = () => { reload(); reloadProject(); };
  const openActivity = () => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> });
  const scanning = ["queued", "running"].includes(m.run?.status || "");
  const mode = !m.generation ? "start" : view === "all" || view === "compare" ? view : view === "scan" || scanning || replay ? "scan" : "shortlist";
  const editContext = () => drawers.push({ key: "ctx", kicker: "YOUR GOAL", render: () => <ContextEdit p={p} reload={both} /> });
  return (
    <div className="app">
      <ProjectHeader brand={brand} site={p.site} intent={goalLine(p)}
        briefVersion={p.brief?.version} running={scanning || ["queued", "running"].includes(m.shortlist_run?.status || "")}
        usage={usageLine(p)} demo={p.demo} onBrief={() => navigate(`/p/${id}`)} onActivity={openActivity} />
      <StageNav stages={stagesFor(p)} current={1} />
      <ContextStrip p={p} onEdit={editContext} />
      <main id="main" className="mk">
        {mode === "start" && <StartPanel p={p} m={m} reload={reload} />}
        {mode === "scan" && <ScanView p={p} m={m} t={t} brand={brand} drawers={drawers} reload={both} />}
        {mode === "shortlist" && <ShortlistView p={p} m={m} brand={brand} drawers={drawers} reload={both} />}
        {mode === "all" && <AllCities p={p} m={m} drawers={drawers} />}
        {mode === "compare" && <CompareView p={p} m={m} />}
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
/** v4 decision bar: text left, actions right (stacked at 390); an optional "← Back to Markets" link above. */
function Bar({ back, kicker, title, subtitle, children }: { back?: string; kicker: string; title: string; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <>
      {back && <div className="mk-back"><Link to={back} className="link">← Back to Markets</Link></div>}
      <section className="decision-bar mk-bar">
        <div className="mk-bar-text">
          <div className="kicker">{kicker}</div>
          <h1>{title}</h1>
          {subtitle && <p className="subtitle">{subtitle}</p>}
        </div>
        {children && <div className="mk-bar-actions">{children}</div>}
      </section>
    </>
  );
}


function findingText(c: CityRow) {
  if (c.status === "returned") return `Qloo returned ${c.finding}.`;
  if (c.status === "empty") return "Qloo returned no related places in this search. That describes the search, not the market.";
  if (c.status === "unresolved") return `Qloo couldn't search this city area (${c.reason}). Unknown, not rejected.`;
  if (c.status === "failed") return `The search failed: ${c.reason}${c.attempts ? ` · ${c.attempts} attempt${c.attempts > 1 ? "s" : ""}` : " · not sent"}. Unknown, not absent.`;
  return c.status === "searching" ? "Searching…" : "Still to check.";
}

const GOAL_PURPOSE: Record<string, string> = { presence: "build presence", stockists: "find stockists", test_demand: "test demand" };
const GOAL_SCOPE: Record<string, string> = { international: "abroad", domestic: "at home", both: "at home or abroad" };
const goalPhrase = (m: Markets) => `${GOAL_PURPOSE[m.goal.purpose]} ${GOAL_SCOPE[m.goal.scope]}`;

/** Card row 1: why it is on the list at all (owner wording, 28 Oct 2026). */
function audienceOf(a: NonNullable<Markets["assessment"]>) {
  const names = a.signal.names ?? [];
  return names.length ? `${names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0]}${a.signal.kind === "standins" ? " (brands like yours)" : ""}`
    : (a.signal.label || "your brand").replace(/^people who like /, "");
}

/** "What we found": where Qloo ranks the city. "Fits because" is the Qloo places it returned there (card.why) (owner, 28 Oct 2026). */
function rankLine(c: Card35, m: Markets) {
  const a = m.assessment!;
  return `Qloo ranks it #${c.qloo_position} of ${a.n_candidates} candidate cities for the audience who would like ${audienceOf(a)}${c.moved_by_purpose ? "; moved for your goal" : ""}`;
}


/** For your goal: scope first, then what the purpose looks at. Deterministic, from the entry facts and the scan. */
function goalVerdict(m: Markets, row: CityRow, c?: Card35) {
  const g = m.goal, same = row.fit.same_country;
  const where = same == null ? "home country not known" : g.scope === "international" ? (same ? "not abroad" : "abroad")
    : g.scope === "domestic" ? (same ? "at home" : "not at home") : (same ? "at home" : "abroad");
  const outside = (g.scope === "international" && same) || (g.scope === "domestic" && same === false);
  const entry: Record<string, string> = {
    "SAME COUNTRY": "no import step", "SAME TRADE BLOC": "same trade bloc, so an easier test", "SHARED LANGUAGE": "a shared language, so an easier test",
    "IMPORT, NEW LANGUAGE": "import and a new language to handle", "FIT NOT KNOWN": "entry fit not known",
  };
  const shops = row.digest?.channels.retail;
  const how = g.purpose === "stockists" ? (shops == null ? "shops not known" : `${shops} shop${shops === 1 ? "" : "s"} among the places Qloo returned`)
    : entry[row.entry_label] || row.entry_label.toLowerCase();
  const lead = outside ? "Outside your goal" : c?.final_position === 1 ? "Preferred" : null;
  return `${lead ? `${lead}: ` : ""}${where}; ${how}${c?.key_unknown ? "; part of the check not known" : ""}`;
}

function openPlace(drawers: Drawers, pid: string, qloo_id: string, city: string) {
  drawers.push({ key: `place-${qloo_id}`, kicker: "QLOO PLACE", render: () => <PlaceDrawer pid={pid} qloo_id={qloo_id} city={city} /> });
}

function openCity(drawers: Drawers, p: Project, m: Markets, c: CityRow) {
  const r = RESULT[bucketOf(c)];
  drawers.push({ key: `city-${c.city_id}`, kicker: `CITY · ${r.long.toUpperCase()}`, render: () => <CityDrawer p={p} m={m} c={c} drawers={drawers} /> });
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

const NO_ORDER: Record<string, string> = {
  failed: "Qloo's ranking failed, so there is no order to recommend from.", denied: "Qloo's ranking wasn't sent: the operating allowance is reached.",
  empty: "Qloo's ranking came back empty.", flat: "Qloo's ranking carried no signal: every city had the same value.",
  no_signal: "Qloo doesn't know your brand, and no brand with a similar following is chosen. Add one on your brief.",
  no_candidates: "No city is left to rank: every catalog city is outside your goal, already yours or left out.",
  needs_home: "Tell us which country is home to rank cities at home.",
};

const byPosition = (cards: Card35[]) => [...cards].sort((x, y) => x.final_position - y.final_position);

// ---- S04 scan (v4 04): finished banner on top, a progress rule, five outcome counts that add to the catalog, the map ----
function ScanView({ p, m, t, brand, drawers, reload }: { p: Project; m: Markets; t: number; brand: string; drawers: Drawers; reload: () => void }) {
  const [sel, setSel] = useState<string | null>(null);
  // while it plays (t < 1), each city shows its outcome at its own moment (a golden-ratio spread over the map), the
  // next few as searching; the counts are the cities shown so far
  const pacing = t < 1;
  const shown: CityRow[] = !pacing ? m.cities : m.cities.map((x, i) => {
    const at = (i * 0.618034) % 1;
    return at < t ? x : { ...x, status: at < t + 0.05 ? "searching" : "queued" };
  });
  const tally = (s: CityRow["status"]) => shown.filter((x) => x.status === s).length;
  const c = !pacing ? m.coverage! : { ...m.coverage!, returned: tally("returned"), empty: tally("empty"), failed: tally("failed"), unresolved: tally("unresolved") };
  const active = pacing || ["queued", "running"].includes(m.run?.status || "");
  const finished = !active && ["completed", "partial"].includes(m.run?.status || "");
  const writing = ["queued", "running"].includes(m.shortlist_run?.status || "");
  const counts: [string, number, string][] = [["Cities with leads", c.returned, "returned"], ["Nothing relevant", c.empty, "empty"],
    [active ? "Failed · retrying" : "Failed · retry", c.failed, "failed"], ["Couldn’t match", c.unresolved, "unresolved"]];
  const done = counts.reduce((s, x) => s + x[1], 0);
  counts.push(["Still to check", c.catalog - done, "queued"]);  // the remainder, so the five always add to the catalog
  const selected = m.cities.find((x) => x.city_id === sel);
  const a = m.assessment;
  const groupOne = a ? byPosition(a.cards.filter((x) => x.group === a.groups[0]?.key)) : [];
  const [top] = groupOne;
  const n = a?.cards.length ?? 0;
  // owner, 28 Oct 2026: once the scan has played and the recommendation is written, it moves on by itself (once a scan)
  const ready = !!m.auto && finished && !writing && !!a && !m.beachhead;
  useEffect(() => {
    if (ready && once(`advance-${m.generation!.id}`)) navigate(`/p/${p.id}/markets`);
  }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <Bar kicker="MARKETS · SCANNING" title={`Looking at ${c.catalog} cities for people who share ${brand}’s taste`}
        subtitle={ruledOut(m.cities, finished) || "Unfinished cities are still to check, not rejected."} />
      {finished && (
        <div className="mk-banner" role="status">
          <span className="mk-banner-text">
            <b>{writing ? "Scan finished · writing the recommendation" : top ? `Scan finished · ${n} cit${n === 1 ? "y" : "ies"} shortlisted` : "Scan finished · no recommendation yet"}</b>
            <span>{writing ? "Qloo is ranking your candidate cities and the Market strategist is explaining them."
              : top ? `Ranked for people who like ${brand}; see which city leads.`
              : a ? NO_ORDER[a.ranking_state] || "No city came back ranked." : "The recommendation didn't finish."}</span>
          </span>
          <button type="button" className="btn-primary" onClick={() => navigate(`/p/${p.id}/markets`)}>
            See recommended markets
          </button>
        </div>
      )}
      {!active && m.notices.length > 0 && <ActionNotices p={p} m={m} reload={reload} />}
      <div className="mk-progress" role="progressbar" aria-label="Cities searched" aria-valuemin={0} aria-valuemax={c.catalog} aria-valuenow={done}>
        <span style={{ width: `${(done / c.catalog) * 100}%` }} />
      </div>
      <ul className="mk-counts" aria-label="Scan outcomes" aria-live="polite">
        {counts.map(([label, k, dot]) => <li key={dot}><span className={`legend-dot dot-${dot}`} aria-hidden /><b>{k}</b><span>{label}</span></li>)}
      </ul>
      <div className="mk-map">
        <WorldMap cities={shown} home={m.home} variant="scan" selected={sel} onSelect={setSel}
          labels={pacing ? [] : m.cities.filter((x) => x.pick?.included).map((x) => x.city_id)} />
      </div>
      {selected && <div className="card mk-detail"><CityDetail p={p} c={selected} drawers={drawers} /></div>}
    </>
  );
}

function CityDetail({ p, c, drawers }: { p: Project; c: CityRow; drawers: Drawers }) {
  return (
    <div className="city-detail" aria-live="polite">
      <div className="finding-head"><h3 className="subheading">{c.name}</h3><Chip label={STATUS_LABEL[c.status]} /></div>
      <p className="small steel">{c.region} · {c.fit.distance_km != null ? `${c.fit.distance_km.toLocaleString()} km from home` : "distance not known"}</p>
      <p className="small">{findingText(c)}</p>
      <LocalLeads p={p} c={c} drawers={drawers} />
      {c.deferred && <p className="small"><Chip label={REASON_CHIP[c.deferred.reason] || c.deferred.reason} /> {c.deferred.line}</p>}
    </div>
  );
}

function LocalLeads({ p, c, drawers }: { p: Project; c: CityRow; drawers: Drawers }) {
  if (!c.digest?.top_places.length) return null;
  return (
    <>
      <div className="label steel">LOCAL LEADS · NOT CHECKED</div>
      <ul className="lead-list">
        {c.digest.top_places.slice(0, 5).map((pl) => (
          <li key={pl.qloo_id}><button type="button" className="link" onClick={() => openPlace(drawers, p.id, pl.qloo_id, c.city_id)}>{pl.name}</button>
            <span className="small steel"> {pl.genres.join(" · ")}</span></li>
        ))}
      </ul>
    </>
  );
}

// ---- S05 recommendation (v4 05): 1 recommended + 2 alternatives, more on request; one primary that follows the selection ----
const MAX_SELECT = 3;
const SHOWN = 3;

/** Compare selection lives in the URL (?sel=a,b), so a refresh or Back keeps it (UI.3). */
function useSelection(): [string[], (ids: string[]) => void] {
  const read = () => (new URLSearchParams(window.location.search).get("sel") || "").split(",").filter(Boolean);
  const [sel, setSel] = useState(read);
  const set = (ids: string[]) => {
    const q = new URLSearchParams(window.location.search);
    if (ids.length) q.set("sel", ids.join(",")); else q.delete("sel");
    window.history.replaceState({}, "", `${window.location.pathname}${q.size ? `?${q}` : ""}`);
    setSel(ids);
  };
  return [sel, set];
}

/** M2: the one primary action. Records the beachhead (adding the city to the shortlist if needed), then opens its plan. */
/** Opens the plan at once, then records the pick (owner, 28 Oct 2026: a press must always visibly do something). The plan
 * page shows the build as soon as the pick lands; if it can't be recorded, the plan page says so and offers Markets. */
export async function buildPlan(pid: string, cid: string) {
  navigate(`/p/${pid}/plan/${cid}`);
  const m = await api.markets(pid);
  if (m.beachhead?.city_id !== cid) await api.editShortlist(pid, m.shortlist_version ?? 0, { beachhead: cid });
}

function ShortlistView({ p, m, brand, drawers, reload }: { p: Project; m: Markets; brand: string; drawers: Drawers; reload: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [more, setMore] = useState<Record<string, boolean>>({});
  const [sel, setSel] = useSelection();
  const a = m.assessment;
  const cards = a?.cards || [];
  const writing = ["queued", "running"].includes(m.shortlist_run?.status || "");
  const added = (m.shortlist?.cities || []).filter((c) => (c.added_by_founder || c.kept_as_beachhead) && !cards.some((x) => x.city_id === c.city_id));
  const n = cards.length;
  const name = (cid: string) => m.cities.find((c) => c.city_id === cid)?.name || cid;
  const top = a ? byPosition(cards.filter((x) => x.group === a.groups[0]?.key))[0] : undefined;
  const toggle = (cid: string, on: boolean) => setSel(on ? [...sel, cid].slice(0, MAX_SELECT) : sel.filter((x) => x !== cid));
  const build = async (cid: string) => {
    setErr(null);
    try { await buildPlan(p.id, cid); } catch (e) { setErr((e as Error).message); }
  };
  const primary = sel.length >= 2 ? { label: `Compare ${sel.map(name).join(", ")}`, onClick: () => navigate(`/p/${p.id}/markets/compare?c=${sel.join(",")}`) }
    : sel.length === 1 ? { label: `Build a plan for ${name(sel[0])}`, onClick: () => build(sel[0]) }
    : top ? { label: `Build a plan for ${top.name}`, onClick: () => build(top.city_id) } : null;
  const noOrder = a && n === 0 ? NO_ORDER[a.ranking_state] : null;
  const row = (cid: string) => m.cities.find((x) => x.city_id === cid)!;
  const card = (c: Card35, i: number) => (
    <CityCard key={c.city_id} c={c} m={m} role={i === 0 ? "RECOMMENDED" : i < 3 ? "ALTERNATIVE" : null}
      selected={sel.includes(c.city_id)} full={sel.length >= MAX_SELECT} onToggle={(on) => toggle(c.city_id, on)}
      onWhy={() => openCity(drawers, p, m, row(c.city_id))} />
  );
  const tieBreak = { presence: "", stockists: ", how many shops Qloo returned in each city to break near-ties",
    test_demand: ", shared language, trade bloc and working hours to break near-ties" }[m.goal.purpose];
  return (
    <>
      <Bar kicker={`MARKETS · ${m.catalog} CITIES SCANNED${a ? ` · ${n} SHORTLISTED` : ""}`}
        title={a ? (top ? `${top.name} looks like the strongest first city for ${brand}` : "No city recommended yet") : writing ? "Writing the recommendation" : "The recommendation isn't ready"}
        subtitle={a && top ? rankLine(top, m).replace(/\.?$/, ".")
          : writing ? "Qloo is ranking your candidate cities and the Market strategist is explaining them. This takes a minute." : undefined}>
        {primary && <button type="button" className="btn-primary" onClick={primary.onClick}>{primary.label}</button>}
        {sel.length >= 2 && top && <button type="button" className="link" onClick={() => build(top.city_id)}>or build a plan for {top.name}</button>}
      </Bar>
      <ActionNotices p={p} m={m} reload={reload} />
      {err && <p className="field-error">{err}</p>}
      {!a && writing && <div className="card"><Skeleton width="70%" /><Skeleton width="50%" /></div>}
      {noOrder && (
        <div className="card empty-state">
          <h2 className="heading-sm">No order to recommend from</h2>
          <p>{noOrder} Nothing is padded or guessed. The scan is kept: see every city and build a plan for any of them.</p>
          <Link to={`/p/${p.id}/markets/all`} className="btn-secondary">See all {m.catalog} cities</Link>
        </div>
      )}
      {n > 0 && (
        <p className="mk-hint">Select one city to plan in, or two or three to compare.
          {sel.length > 0 && <button type="button" className="link" onClick={() => setSel([])}>Clear selection</button>}</p>
      )}
      {a && a.groups.map((g) => {
        const gc = byPosition(cards.filter((c) => c.group === g.key));
        const rest = gc.slice(SHOWN);
        const open = more[g.key] || rest.some((c) => sel.includes(c.city_id));
        return (
          <Fragment key={g.key}>
            {a.groups.length > 1 && <h2 className="label steel mk-group">{g.label.toUpperCase()}</h2>}
            {a.groups.length > 1 && gc.length === 0 && n > 0 && <p className="small card">No city {g.key === "home" ? "at home" : "abroad"} came back ranked, so this group has no recommendation. Nothing is moved over from the other group.</p>}
            <div className="mk-grid">{gc.slice(0, SHOWN).map(card)}</div>
            {rest.length > 0 && (
              <div className="mk-more">
                <button type="button" className="mk-more-head" aria-expanded={open} onClick={() => setMore({ ...more, [g.key]: !open })}>
                  <span><b>{rest.length} more shortlisted</b><span className="steel"> · {rest.map((c) => c.name).join(", ")}</span></span>
                  <span className="mk-more-lab">{open ? "Hide" : "Show"}</span>
                </button>
                {open && <div className="mk-grid mk-more-body">{rest.map((c, i) => card(c, SHOWN + i))}</div>}
              </div>
            )}
            {gc.length > 0 && gc.length < g.cap && <p className="cap-note">Only {gc.length} cit{gc.length === 1 ? "y" : "ies"} {gc.length === 1 ? "was" : "were"} ranked here{g.available > gc.length ? " after the checks" : ""}. The list isn't padded to {g.cap}.</p>}
          </Fragment>
        );
      })}
      {added.length > 0 && <div className="mk-grid mk-added">{added.map((pk) => {
        const r = row(pk.city_id);
        return (
          <article key={pk.city_id} id={`card-${pk.city_id}`} className="mk-card" aria-label={r.name}>
            <div className="mk-card-top">
              <span className="mk-card-name"><SelectBox name={r.name} selected={sel.includes(pk.city_id)} full={sel.length >= MAX_SELECT} onToggle={(on) => toggle(pk.city_id, on)} />
                <h3 className="mk-city">{r.name}</h3></span>
              <Chip label={pk.kept_as_beachhead ? "YOUR BEACHHEAD" : "ADDED BY YOU"} />
            </div>
            <dl className="mk-rows">
              <dt>Fits because</dt><dd>{pk.kept_as_beachhead ? "Your beachhead; no longer in the recommendation" : "You added it; it isn't in the recommendation, so it has no explanation"}</dd>
            </dl>
            <button type="button" className="link mk-why" onClick={() => openCity(drawers, p, m, r)}>Why {r.name}</button>
          </article>
        );
      })}</div>}
      {a && (
        <div className="mk-informed">
          <p><b>What informed this:</b> your goal ({goalPhrase(m)}), Qloo’s ranking of your {a.n_candidates} candidate cities for the audience who would like {audienceOf(a)}{tieBreak},
            the cities where you already sell (left out), and the places Qloo returned in each of the {m.catalog} cities.
            {a.coverage !== "complete" && a.coverage !== "not_applicable" ? ` ${m.presence.coverage_line}: where we couldn't confirm, the card says so.` : ""}{" "}
            <Link to={`/p/${p.id}/markets/all`} className="link">Explore all {m.catalog} cities</Link></p>
          <div className="mk-map mk-map-sm">
            <WorldMap cities={m.cities} home={m.home} variant="shortlist" compact
              labels={m.cities.filter((x) => x.pick?.included).map((x) => x.city_id)}
              onSelect={(cid) => openCity(drawers, p, m, row(cid))} />
          </div>
        </div>
      )}
    </>
  );
}

/** A real checkbox (the v4 prototype's weren't exposed as checkboxes; UI.2 gap 5). */
function SelectBox({ name, selected, full, onToggle }: { name: string; selected: boolean; full: boolean; onToggle: (on: boolean) => void }) {
  const off = full && !selected;
  return (
    <input type="checkbox" className="mk-box" checked={selected} disabled={off} aria-label={`Select ${name}`}
      title={off ? `Compare up to ${MAX_SELECT} cities at a time` : undefined} onChange={(e) => onToggle(e.target.checked)} />
  );
}

function CityCard({ c, m, role, selected, full, onToggle, onWhy }: {
  c: Card35; m: Markets; role: "RECOMMENDED" | "ALTERNATIVE" | null; selected: boolean; full: boolean; onToggle: (on: boolean) => void; onWhy: () => void;
}) {
  return (
    <article id={`card-${c.city_id}`} className={`mk-card ${role === "RECOMMENDED" ? "mk-card-rec" : ""}`} aria-label={c.name}>
      <div className="mk-card-top">
        <span className="mk-card-name"><SelectBox name={c.name} selected={selected} full={full} onToggle={onToggle} /><h3 className="mk-city">{c.name}</h3></span>
        {role && <span className={`mk-role ${role === "RECOMMENDED" ? "mk-role-rec" : ""}`}>{role}</span>}
      </div>
      <dl className="mk-rows">
        <dt>Fits because</dt><dd>{c.why}</dd>
        <dt>What we found</dt><dd>{rankLine(c, m)}.</dd>
      </dl>
      <button type="button" className="link mk-why" onClick={onWhy}>Why {c.name}</button>
    </article>
  );
}

// ---- M3 · Compare (v4 06): the same five criteria per city, Remove per column, Add a city ----
function CompareView({ p, m }: { p: Project; m: Markets }) {
  const read = () => (new URLSearchParams(window.location.search).get("c") || "").split(",").filter((c) => m.cities.some((x) => x.city_id === c)).slice(0, MAX_SELECT);
  const [ids, setIds] = useState(read);
  const [err, setErr] = useState<string | null>(null);
  const setCols = (next: string[]) => {
    window.history.replaceState({}, "", `${window.location.pathname}?c=${next.join(",")}`);
    setIds(next);
  };
  const cardOf = (cid: string) => m.assessment?.cards.find((c) => c.city_id === cid);
  const cols = ids.map((cid) => ({ row: m.cities.find((c) => c.city_id === cid)!, card: cardOf(cid) }));
  const build = async (cid: string) => { setErr(null); try { await buildPlan(p.id, cid); } catch (e) { setErr((e as Error).message); } };
  const back = `/p/${p.id}/markets?sel=${ids.join(",")}`;
  if (cols.length < 2) return <><Bar back={back} kicker="MARKETS · COMPARE" title="Compare cities for a first test" subtitle="Select two or three cities on Markets to compare them." /></>;
  type Col = (typeof cols)[number];
  const NK = <span className="steel">Not known</span>;
  const rows: [string, (c: Col) => ReactNode][] = [
    ["Cultural and audience fit", (c) => c.card ? c.card.why : <>Not in Qloo’s recommendation<span className="steel"> · {c.row.fit_line}</span></>],
    ["What we found", (c) => c.card ? `${rankLine(c.card, m)}.` : NK],
    ["Practical constraints", (c) => <>{c.card ? c.card.entry_line : c.row.fit_line}{c.row.fit.distance_km != null && <span className="steel"> · {c.row.fit.distance_km.toLocaleString()} km from home</span>}</>],
    ["Evidence coverage", (c) => <>{c.row.status === "returned" && c.row.digest ? `Qloo returned ${c.row.digest.returned_count} places · ${c.row.digest.evidence_strength === "MODERATE" ? "none checked yet" : "too few to judge"}` : findingText(c.row)}
      {c.row.research ? " · city research done" : ""}{c.card?.located_check === "failed" ? " · store check failed" : ""}</>],
    [`For your goal: ${goalPhrase(m)}`, (c) => goalVerdict(m, c.row, c.card) || NK],
  ];
  const top = [...cols].sort((x, y) => (x.card?.final_position ?? 99) - (y.card?.final_position ?? 99))[0];
  const role = (c: Col) => !c.card ? null : c.card.final_position === 1 ? "RECOMMENDED" : "ALTERNATIVE";
  const addable = [...(m.assessment ? byPosition(m.assessment.cards) : []).map((c) => c.city_id),
    ...(m.shortlist?.cities || []).filter((x) => x.added_by_founder || x.kept_as_beachhead).map((x) => x.city_id)]
    .filter((cid, i, all) => !ids.includes(cid) && all.indexOf(cid) === i);
  return (
    <>
      <Bar back={back} kicker="MARKETS · COMPARE" title={`Compare ${cols.length} cities for a first test`}
        subtitle="The same criteria for each. Evidence coverage is judged separately; limited evidence isn’t a weak market.">
        <button type="button" className="btn-primary" onClick={() => build(top.row.city_id)}>Build a plan for {top.row.name}</button>
      </Bar>
      {err && <p className="field-error">{err}</p>}
      <div className="mk-compare-card">
        <table className="mk-compare" style={{ minWidth: `calc(var(--mk-label-w) + ${cols.length} * var(--mk-col-min))` }}>
          <thead><tr><th scope="col"><span className="sr-only">Criterion</span></th>
            {cols.map((c) => {
              const r = role(c);
              return (
                <th key={c.row.city_id} scope="col">
                  <span className="mk-col-name">{c.row.name}</span>
                  {r && <span className={`mk-role mk-role-block ${r === "RECOMMENDED" ? "mk-role-rec" : ""}`}>{r}</span>}
                  <span className="mk-col-actions">
                    <button type="button" className="btn-secondary mk-btn-sm" onClick={() => build(c.row.city_id)}>Build a plan here</button>
                    <button type="button" className="link" aria-label={`Remove ${c.row.name}`}
                      onClick={() => ids.length > 2 ? (setErr(null), setCols(ids.filter((x) => x !== c.row.city_id))) : setErr("Compare at least 2 cities.")}>Remove</button>
                  </span>
                </th>
              );
            })}</tr></thead>
          <tbody>{rows.map(([label, cell]) => <tr key={label}><th scope="row">{label}</th>{cols.map((c) => <td key={c.row.city_id}>{cell(c)}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {ids.length < MAX_SELECT && addable.length > 0 && (
        <label className="mk-add">Add a city
          <select className="mk-select" value="" onChange={(e) => e.target.value && setCols([...ids, e.target.value])}>
            <option value="">Choose…</option>
            {addable.map((cid) => <option key={cid} value={cid}>{m.cities.find((x) => x.city_id === cid)?.name || cid}</option>)}
          </select>
        </label>
      )}
    </>
  );
}

// ---- M4 · All 80 cities (v4 07): search, region and result filters, 20 a page, each row opens the city drawer ----
/** v4's six results, plus two live-only ones shown only when non-zero, so the totals always add to the catalog. */
const RESULT: Record<string, { label: string; long: string }> = {
  shortlisted: { label: "Shortlisted", long: "Shortlisted" }, researched: { label: "Researched", long: "Researched, not shortlisted" },
  empty: { label: "Nothing relevant", long: "Nothing relevant returned" }, failed: { label: "Failed · retry", long: "Search failed" },
  unresolved: { label: "Couldn’t match", long: "Couldn’t match" }, here: { label: "Already sell here", long: "You already sell here" },
  outside: { label: "Outside your goal", long: "Outside your goal" }, pending: { label: "Still to check", long: "Still to check" },
};
const PAGE = 20;

/** Each city counts once, by this fixed precedence, so the totals always sum to the catalog. */
export function bucketOf(c: CityRow): string {
  if (c.excluded === "ALREADY_HERE") return "here";
  if (c.excluded === "OUTSIDE_GOAL") return "outside";
  if (c.pick?.included) return "shortlisted";
  if (c.status === "returned") return "researched";
  if (c.status === "empty" || c.status === "failed" || c.status === "unresolved") return c.status;
  return "pending";
}

function rowFinding(m: Markets, c: CityRow) {
  const b = bucketOf(c);
  const card = m.assessment?.cards.find((x) => x.city_id === c.city_id);
  if ((b === "here" || b === "outside") && c.deferred) return c.deferred.line;
  if (card) return card.why;
  return `${findingText(c)}${c.deferred ? ` ${c.deferred.line}` : ""}`;
}

function useQuery(keys: string[]): [Record<string, string>, (k: string, v: string) => void, () => void] {
  const read = () => Object.fromEntries(keys.map((k) => [k, new URLSearchParams(window.location.search).get(k) || ""]));
  const [q, setQ] = useState(read);
  const write = (u: URLSearchParams) => { window.history.replaceState({}, "", `${window.location.pathname}${u.size ? `?${u}` : ""}`); setQ(read()); };
  const set = (k: string, v: string) => {
    const u = new URLSearchParams(window.location.search);
    if (v) u.set(k, v); else u.delete(k);
    if (k !== "page") u.delete("page");
    write(u);
  };
  return [q, set, () => write(new URLSearchParams())];
}

function AllCities({ p, m, drawers }: { p: Project; m: Markets; drawers: Drawers }) {
  const [q, set, clear] = useQuery(["q", "region", "result", "page"]);
  const counts = Object.fromEntries(Object.keys(RESULT).map((k) => [k, m.cities.filter((c) => bucketOf(c) === k).length]));
  const shown = Object.keys(RESULT).filter((k) => counts[k] || ["shortlisted", "researched", "empty", "failed", "unresolved", "here"].includes(k));
  const rows = m.cities.filter((c) => (!q.q || `${c.name} ${c.country}`.toLowerCase().includes(q.q.toLowerCase()))
    && (!q.region || c.region === q.region) && (!q.result || bucketOf(c) === q.result));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const page = Math.min(pages, Math.max(1, Number(q.page) || 1));
  const from = (page - 1) * PAGE;
  const total = m.cities.length;
  return (
    <>
      <Bar back={`/p/${p.id}/markets`} kicker="MARKETS · ALL CITIES" title={`All ${m.catalog} cities`}
        subtitle={`${total} cities = ${shown.map((k) => `${counts[k]} ${RESULT[k].label.toLowerCase()}`).join(" · ")}`} />
      <div className="mk-filters">
        <input className="mk-input mk-search" type="search" placeholder="Search cities" aria-label="Search cities"
          value={q.q} onChange={(e) => set("q", e.target.value)} />
        <select className="mk-select" aria-label="Region" value={q.region} onChange={(e) => set("region", e.target.value)}>
          <option value="">All regions</option>{m.regions.map((r) => <option key={r} value={r}>{r}</option>)}</select>
        <select className="mk-select" aria-label="Result" value={q.result} onChange={(e) => set("result", e.target.value)}>
          <option value="">All results</option>{shown.map((k) => <option key={k} value={k}>{RESULT[k].label} · {counts[k]}</option>)}</select>
        <button type="button" className="link" onClick={clear}>Clear</button>
      </div>
      <div className="mk-list">
        {rows.length === 0 ? <p className="mk-empty">No city matches. <button type="button" className="link" onClick={clear}>Clear the filters</button></p> : (
          <ul className="city-rows mk-rows-list" aria-label="Cities">
            {rows.slice(from, from + PAGE).map((c) => {
              const b = bucketOf(c);
              return (
                <li key={c.city_id}>
                  <button type="button" className="mk-row" onClick={() => openCity(drawers, p, m, c)}>
                    <span className="mk-row-name"><b>{c.name}</b><span className="steel">{c.region}</span></span>
                    <span className="mk-row-result"><span className={`mk-res mk-res-${b}`}>{RESULT[b].label}</span><span className="steel">{rowFinding(m, c)}</span></span>
                    <span className="mk-row-details">Details</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {rows.length > 0 && (
        <div className="mk-pager">
          <span className="mono steel">{from + 1}–{Math.min(rows.length, from + PAGE)} of {rows.length}{rows.length < total ? ` (filtered from ${total})` : ""}</span>
          <span className="mk-pager-btns">
            <button type="button" className="btn-secondary mk-btn-sm" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>‹ Previous</button>
            <button type="button" className="btn-secondary mk-btn-sm" disabled={page >= pages} onClick={() => set("page", String(page + 1))}>Next ›</button>
          </span>
        </div>
      )}
    </>
  );
}

const Sec = ({ h, children }: { h: string; children: ReactNode }) => <section className="mk-dsec"><h3 className="label steel">{h}</h3>{children}</section>;

// ---- M5 · City drawer (v4 07 drawer): intro, What we found, Coverage, then Build a plan ----
function CityDrawer({ p, m, c, drawers }: { p: Project; m: Markets; c: CityRow; drawers: Drawers }) {
  const [err, setErr] = useState<string | null>(null);
  const card = m.assessment?.cards.find((x) => x.city_id === c.city_id);
  const here = bucketOf(c) === "here" || m.presence.cities.some((x) => x.city_id === c.city_id && !x.excluded);
  const can = !here && c.status !== "unresolved";
  const bh = m.beachhead && m.beachhead.city_id !== c.city_id ? m.beachhead.name : null;
  return (
    <div className="mk-drawer">
      <h2 className="heading">{c.name}</h2>
      <p className="steel">{rowFinding(m, c)}</p>
      {card ? (
        <>
          <Sec h="WHAT WE FOUND"><p className="mk-ditem">{rankLine(card, m)}.</p></Sec>
          <Sec h="COVERAGE"><p className="mk-ditem"><span>{findingText(c)}{card.moved_line ? ` ${card.moved_line}` : ""}</span></p></Sec>
        </>
      ) : (
        <>
          {c.deferred && <Sec h="WHAT WE FOUND">
            {c.deferred && <p className="mk-ditem"><Chip label={REASON_CHIP[c.deferred.reason] || c.deferred.reason} /> <span>{c.deferred.line}</span></p>}</Sec>}
          {can && <Sec h="IF YOU PLAN HERE"><p className="mk-ditem"><span>Its uncertainty labels travel into Plan.{bh ? ` Your ${bh} plan is kept.` : ""}</span></p></Sec>}
        </>
      )}
      {can ? (
        <>
          <div><button type="button" className="btn-primary" onClick={async () => { setErr(null); try { drawers.close(); await buildPlan(p.id, c.city_id); } catch (e) { setErr((e as Error).message); } }}>
            Build a plan for {c.name}</button></div>
          <p className="small steel">Selects a city to plan in. It commits no money. Other plans are kept.</p>
        </>
      ) : <p className="small">{here ? "You already sell here, so it isn't offered as a new market." : "Qloo couldn't match this city area; research by another route isn't available yet."}</p>}
      {err && <p className="field-error">{err}</p>}
    </div>
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
        <p className="small">Qloo relates this place to your discovery signals. It says nothing about sales, audience size or whether the place would work with you. Nobody has checked it yet.</p>
      </div>
    </div>
  );
}

/** "43 to rank · 24 where you already sell · 11 at home …": which cities the ranking leaves out, and why (owner, 28 Oct 2026). */
function ruledOut(cities: { excluded?: string | null }[], finished: boolean): string | null {
  const by = new Map<string, number>();
  // "Qloo can't place" is only known once the scan is done; the rest is known before it starts
  for (const x of cities) if (x.excluded && (finished || x.excluded !== "NOT_RANKED")) by.set(x.excluded, (by.get(x.excluded) ?? 0) + 1);
  if (!by.size) return null;
  const out = [...by.values()].reduce((a, b) => a + b, 0);
  const words: Record<string, string> = { ALREADY_HERE: "where you already sell", OUTSIDE_GOAL: "outside your goal (home)",
    LEFT_OUT_BY_YOU: "you left out", NOT_RANKED: "Qloo can't place" };
  return [`${cities.length - out} to rank`, ...[...by].map(([k, n]) => `${n} ${words[k] ?? "not ranked"}`)].join(" · ");
}
