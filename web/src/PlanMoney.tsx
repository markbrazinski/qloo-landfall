import { useState } from "react";
import { acceptEstimate, api65, ccyExp, estimatedTotal, estimateOf, majorText, type CostLine, type DigitalScenario, type Money, type PlanFit, type PlanMethod, type PlanView, type ProposalOption,
  type Rationale, type Role, type ScenarioPlatform } from "./api";
import { Chip } from "./ui";

/** Slice 5 (BUILD-PLAN "Slice 5" §5.8): the Plan's money, the proposal and the digital scenario. Every figure comes from
 * the API; nothing unknown is shown as $0, and fit and fact labels are always text. */

const SYMBOL: Record<string, string> = { USD: "US$", AUD: "A$", CAD: "CA$", NZD: "NZ$", EUR: "€", GBP: "£" };
export const fmt = (m: Money | null | undefined) => {
  if (!m) return "—";
  const e = ccyExp(m.ccy), d = m.minor / 10 ** e, whole = m.minor % 10 ** e === 0;  // like money.display
  const body = d.toLocaleString("en-US", { maximumFractionDigits: whole ? 0 : e, minimumFractionDigits: whole ? 0 : e });
  return SYMBOL[m.ccy] ? `${SYMBOL[m.ccy]}${body}` : `${m.ccy} ${body}`;
};
const major = majorText;
export const FIT: Record<PlanFit, string> = {
  NOT_ASSESSED: "No ceiling set", OVER_CEILING: "Over your ceiling", AFFORDABILITY_UNRESOLVED: "Fit unresolved: some costs aren't known",
  FITS_KNOWN_COSTS: "Within your ceiling",
};
const BASIS: Record<string, string> = { LISTED_RATE: "LISTED RATE", ESTIMATE: "ESTIMATE", ASSUMED: "ASSUMED", YOU_SAID: "YOU SAID",
  NOT_KNOWN: "NOT KNOWN", NOT_COSTED: "NOT COSTED" };
const LABEL: Record<string, string> = { retail: "Retail", creator: "Creator", popup: "Pop-up", community: "Community", digital: "Digital" };
const PLATFORM: Record<string, string> = { META_ADS: "Meta", GOOGLE_ADS: "Google", TIKTOK_ADS: "TikTok" };
type Act = (label: string, fn: () => Promise<unknown>) => void;

/** Whole currency units for the plan's headline figures (v4 says money once, without cents); exact amounts stay on the
 * cost lines. */
export const whole = (m: Money | null | undefined) => (m ? fmt({ minor: Math.round(m.minor / 10 ** ccyExp(m.ccy)) * 10 ** ccyExp(m.ccy), ccy: m.ccy }) : "—");
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** v4 08's one affordability sentence under the strip; the fit itself comes from the API. */
export function fitSentence(m: NonNullable<PlanView["money"]>) {
  const n = m.unknown.length + m.unconverted.length;
  // "costed" = known and labeled estimates alike (cash_out_known counts every line with an amount)
  if (m.fit === "NOT_ASSESSED") return { text: "No ceiling set, so the plan's costs can't be compared with one.", tone: "" };
  if (m.fit === "OVER_CEILING") return { text: `Costed lines are over your ${whole(m.ceiling)} ceiling by ${whole({ minor: m.cash_out_known.minor - m.ceiling!.minor, ccy: "USD" })}.`, tone: "pl-over" };
  if (m.fit === "FITS_KNOWN_COSTS") return { text: "The plan is within your ceiling.", tone: "" };
  return { text: `Costed lines are within your ceiling. Total affordability is unresolved: ${n} ${n === 1 ? "cost isn't" : "costs aren't"} ${m.unconverted.length && !m.unknown.length ? "converted" : "known"} yet.`, tone: "pl-unres" };
}

/** The plan's total against the founder's budget (landfall/plans.py `planned_of`): known (YOU SAID, LISTED RATE) and the
 * rest labeled ESTIMATE or ASSUMED, unknowns counted. */
type Planned = { amount: Money; known: Money; estimated: Money; not_known: number; ceiling: Money | null; over: boolean; text: string };
export const plannedOf = (m: unknown) => (m as { planned?: Planned } | null | undefined)?.planned ?? null;
/** The founder's units × price at an assumed sell-through (landfall/plans.py `assumed_return`), and the net. */
type Assumed = { amount: Money; basis: string; label: string; text: string; note: string };
const returnOf = (m: unknown) => m as { assumed_return?: Assumed | null; if_it_works?: string | null };
type Split = { lever: string; label: string; percent: number; why: string; text?: string };
const splitOf = (o: ProposalOption) => (o as ProposalOption & { split?: Split[] }).split ?? [];

/** LF-CostReturnStrip (v4 08): planned cost against the budget · cash back if it hits the goal · success · if it works,
 * then the fit sentence. Cash back is the founder's own return only; without their price and units the Cash back and
 * If it works columns are hidden: no default share of the cost (owner, 28 Oct 2026). */
export function CostReturnStrip({ v }: { v: PlanView }) {
  const m = v.money;
  const main = v.methods.find((x) => x.role?.role === "MAIN");
  if (!m) return null;
  const g = m.gross_receipts;
  const r = returnOf(m);
  const n = m.unknown.length;
  const fit = fitSentence(m);
  const et = estimatedTotal(m);
  const p = plannedOf(m);
  const back = !!(g || r.assumed_return);
  const title = back ? "What this test costs and returns" : "What this test costs";
  return (
    <section className="pl-strip" aria-label={title}>
      <div className="pl-strip-head"><h2>{title}</h2><span className="mono steel">Illustrative · assumptions labeled</span></div>
      <div className="pl-c4" style={back ? undefined : { gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
        {p ? <div title={`${p.text}. Estimates are Landfall's planning figures, not quotes${et ? ` (${et.range_text} for the costs not known)` : ""}.`}>
          <span className="label steel">Planned cost to run it</span><span className="pl-fig">{whole(p.amount)}</span>
          <span className="small">planned{p.ceiling ? ` of ${whole(p.ceiling)}` : ""}</span>
          <span className="small steel">{p.known.minor ? `${whole(p.known)} known · ${whole(p.estimated)} labeled estimates` : "All labeled estimates"}</span>
          {(p.not_known > 0 || m.unconverted.length > 0) && <span className="pl-unk">{p.not_known > 0 && `${plural(p.not_known, "cost", "costs")} not known · not added`}
            {m.unconverted.length > 0 && `${p.not_known ? " · " : ""}${m.unconverted.length} not converted`}</span>}</div>
        : <div><span className="label steel">Known cost to run it</span><span className="pl-fig">{whole(m.cash_out_known)}</span>
          {(n > 0 || m.unconverted.length > 0) && <span className="pl-unk">{n > 0 && `${plural(n, "cost", "costs")} not known · not added`}
            {m.unconverted.length > 0 && `${n ? " · " : ""}${m.unconverted.length} not converted`}</span>}</div>}
        {back && <div><span className="label steel">Cash back if it hits the goal</span>
          {g ? <><span className="pl-fig">{whole(g.low)}–{whole(g.high)}</span><span className="small steel">{g.label} ({g.price} price) <Chip label="ASSUMED" /></span></>
            : <><span className="pl-fig">{r.assumed_return!.text}</span><span className="small steel">{r.assumed_return!.note} <Chip label="ASSUMED" /></span></>}</div>}
        <div><span className="label steel">Success looks like</span><span className="pl-txt">{main?.draft?.success_measure ?? "Choose a main test first."}</span></div>
        {back && <div><span className="label steel">If it works</span>{r.if_it_works
          ? <><span className="pl-txt">{r.if_it_works}</span><span className="small steel">The planned cost less your return: an assumption, not a forecast.</span></>
          : <span className="pl-txt steel">Not stated by this plan yet.</span>}</div>}
      </div>
      <p className={`pl-fit ${fit.tone}`}>{fit.text}</p>
    </section>
  );
}

/** LF-PlanBudget (v4 08 aside): "Cost lines", collapsed by default; opened, every counted line with its knowledge chip and
 * shared tag, the totals by flow, and (as a secondary, per line) the founder's own amounts. */
export function CostLines({ id, city, v, act, busy }: { id: string; city: string; v: PlanView; act: Act; busy: boolean }) {
  const [open, setOpen] = useState(true);  // open from the start: the breakdown is what founders look for (owner, 28 Oct 2026)
  const m = v.money;
  const pv = v.plan?.version ?? 0;
  if (!m) return null;
  const counting = m.lines.filter((l) => l.counts);
  return (
    <div className="pl-aside-card pl-flush" aria-label="Cost lines">
      <button type="button" className="pl-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span><b>Cost lines</b><span className="steel"> · {plural(counting.length, "line", "lines")} · shared lines counted once</span></span>
        <span className="pl-show">{open ? "Hide" : "Show"}</span></button>
      {open && <div className="pl-lines">
        {counting.map((l) => <LineRow key={l.id} id={id} city={city} l={l} pv={pv} act={act} busy={busy} methods={v.methods} />)}
        <div className="pl-totals">
          <span className="steel">Inventory</span><span>{m.inventory_known.minor ? fmt(m.inventory_known) : "None known"}</span>
          <span className="steel">Expenses</span><span>{m.expense_known.minor ? fmt(m.expense_known) : "None known"}</span>
          <span className="steel">Refundable deposits</span><span>{m.deposits_known.minor ? fmt(m.deposits_known) : "None known"}</span>
        </div>
        {m.remaining && <p className="small steel">{fmt(m.remaining)} not allocated. The ceiling isn't a target.</p>}
        <p className="small steel">Ceiling {m.ceiling ? fmt(m.ceiling) : "not set"}{v.ceiling.by === "DEFAULT" ? " (default)" : ""}.{m.fx ? ` Other currencies ${m.fx.text}.` : ""} Unknown costs are counted, never shown as zero.</p>
        {m.needs_fx && <p className="small"><Chip label="NOT CONVERTED" /> Some amounts couldn't be converted to US dollars.{" "}
          <button type="button" className="link" disabled={busy} onClick={() => act("rates", () => api65.retryRates(id, city, pv))}>Retry rates</button></p>}
        <ReturnInputs id={id} city={city} v={v} act={act} busy={busy} />
      </div>}
    </div>
  );
}

function LineRow({ id, city, l, pv, act, busy, methods }: { id: string; city: string; l: CostLine; pv: number; act: Act; busy: boolean; methods: PlanMethod[] }) {
  const [open, setOpen] = useState(false);
  const [amt, setAmt] = useState(major(l.amount));
  const [ccy, setCcy] = useState(l.amount?.ccy ?? "USD");
  const inPlan = methods.filter((m) => m.role).map((m) => m.method);
  const e = estimateOf(l);
  const save = (e: React.FormEvent) => { e.preventDefault(); act("line", () => api65.editLine(id, city, l.id, pv, { amount: amt ? { amount: amt, ccy } : null })); setOpen(false); };
  return (
    <div className="pl-line-row">
      <div className="pl-line-main">
        <span>{l.label}</span>
        <span className="pl-line-tags"><Chip label={BASIS[l.basis]} />
          {l.shared && <span className="mono steel">Shared · {l.used_by.map((x) => LABEL[x]).join(", ")}</span>}
          {l.flow === "refundable_deposit" && <span className="mono steel">Refundable</span>}{l.flow === "inventory" && <span className="mono steel">Inventory</span>}</span>
        {l.by !== "scenario" && <button type="button" className="link small" onClick={() => setOpen(!open)} aria-expanded={open}
          aria-label={`${l.amount ? "Change" : "Enter amount"}: ${l.label}`}>{l.amount ? "Change" : "Enter amount"}</button>}
      </div>
      <span className="pl-amt">{l.text ?? (e ? <span title={e.basis_note}>≈ {e.text}<span className="steel small"><br />estimate</span></span> : "—")}{l.usd_text && <span className="steel small"><br />≈ {l.usd_text}</span>}{l.unconverted ? " · not converted" : ""}</span>
      {open && l.basis_note && <p className="small steel">{l.basis_note}</p>}
      {e && <div className="small">
        <span className="steel">Not known · Landfall estimate {e.range_text}</span>
        {l.by !== "scenario" && <> · <button type="button" className="link small" disabled={busy} aria-label={`Use this estimate: ${l.label}`}
          onClick={() => act("line", () => acceptEstimate(id, city, l.id, pv))}>Use this estimate</button></>}
        <details><summary className="small steel">How it's estimated</summary>
          <p className="small steel">{e.basis_note}. {e.label}.</p>
          <ul className="plain">{e.sources.map((x, i) => <li key={i} className="small steel">{x.kind === "SOURCED" ? "Source" : "Assumption"}: {x.url
            ? <a className="link" href={x.url} target="_blank" rel="noreferrer">{x.title}</a> : x.title}{x.retrieved_at ? ` (${x.license}, retrieved ${x.retrieved_at})` : ""}</li>)}</ul>
        </details></div>}
      {l.by === "scenario" && <p className="small steel">From your digital scenario: change the daily budget there.</p>}
      {open && <form className="search-row" onSubmit={save}>
        <label className="small">Amount ({ccy}) <input className="input" inputMode="decimal" value={amt} onChange={(e) => setAmt(e.target.value)} aria-label={`${l.label} amount in ${ccy}`} /></label>
        <label className="small">Currency <input className="input" value={ccy} maxLength={3} size={4} onChange={(e) => setCcy(e.target.value.toUpperCase())} /></label>
        <button type="submit" className="btn-secondary" disabled={busy}>Save</button>
        <select className="input" aria-label={`${l.label}: kind of cost`} value={l.flow} disabled={busy}
          onChange={(e) => act("line", () => api65.editLine(id, city, l.id, pv, { flow: e.target.value as CostLine["flow"] }))}>
          <option value="expense">Expense</option><option value="inventory">Inventory</option><option value="refundable_deposit">Refundable deposit</option></select>
        {inPlan.length > 1 && <fieldset className="small"><legend>Used by</legend>{inPlan.map((m) => <label key={m}><input type="checkbox" checked={l.used_by.includes(m)} disabled={busy}
          onChange={(e) => { const ub = e.target.checked ? [...l.used_by, m] : l.used_by.filter((x) => x !== m); if (ub.length) act("line", () => api65.editLine(id, city, l.id, pv, { used_by: ub })); }} /> {LABEL[m]}</label>)}</fieldset>}
      </form>}
    </div>
  );
}

/** The return estimate's inputs [T3]: units are individual items; gross receipts, never profit. */
function ReturnInputs({ id, city, v, act, busy }: { id: string; city: string; v: PlanView; act: Act; busy: boolean }) {
  const e = v.money?.return_estimate ?? {};
  const [units, setUnits] = useState(e?.units ? String(e.units) : "");
  const [price, setPrice] = useState(major(e?.retail_price));
  const [whole, setWhole] = useState(major(e?.wholesale_price));
  const [lo, setLo] = useState(e?.sell_through ? String(e.sell_through.low) : "");
  const [hi, setHi] = useState(e?.sell_through ? String(e.sell_through.high) : "");
  const ccy = e?.retail_price?.ccy ?? e?.wholesale_price?.ccy ?? "USD";
  const submit = (ev: React.FormEvent) => {
    ev.preventDefault();
    const body: Record<string, unknown> = {};
    if (units) body.units = Number(units);
    if (price) body.retail_price = { amount: price, ccy };
    if (whole) body.wholesale_price = { amount: whole, ccy };
    if (lo && hi) body.sell_through = { low: Number(lo), high: Number(hi) };
    act("return", () => api65.editReturn(id, city, v.plan!.version, body));
  };
  return (
    <details>
      <summary className="small">Return if it sells through (optional)</summary>
      <form className="stack" onSubmit={submit}>
        <label className="small">Units (individual items) <input className="input" inputMode="numeric" value={units} onChange={(x) => setUnits(x.target.value)} /></label>
        <label className="small">Retail price per unit ({ccy}){e?.by?.retail_price === "SOURCE" && ` · from ${e.retail_price_source?.title ?? "your site"}`}
          <input className="input" inputMode="decimal" value={price} onChange={(x) => setPrice(x.target.value)} /></label>
        <label className="small">Wholesale price per unit ({ccy}) <input className="input" inputMode="decimal" value={whole} onChange={(x) => setWhole(x.target.value)} /></label>
        <div className="search-row"><label className="small">Sell-through low (0–1) <input className="input" inputMode="decimal" value={lo} onChange={(x) => setLo(x.target.value)} /></label>
          <label className="small">high <input className="input" inputMode="decimal" value={hi} onChange={(x) => setHi(x.target.value)} /></label></div>
        <button type="submit" className="btn-secondary" disabled={busy}>Save</button>
      </form>
    </details>
  );
}

/** Landfall's proposal (S5-14), kept as a collapsed secondary in the aside: v4 has no proposal panel, only the alternative.
 * Propose again and adopting an option stay reachable here; nothing changes until the founder adopts. */
export function ProposalCard({ id, city, v, act, busy }: { id: string; city: string; v: PlanView; act: Act; busy: boolean }) {
  const p = v.proposal;
  const [open, setOpen] = useState(!!p?.id && !p.adopted);
  const running = p?.run && ["queued", "running"].includes(p.run.status);
  const a = (p?.options ?? []).find((o) => o.id === "A");
  return (
    <div className="pl-aside-card pl-flush" aria-label="Landfall's proposal">
      <button type="button" className="pl-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span><b>Landfall's proposal</b><span className="steel">{running ? " · proposing…" : p?.adopted ? " · adopted" : p?.id ? " · not adopted" : ""}</span></span>
        <span className="pl-show">{open ? "Hide" : "Show"}</span></button>
      {open && <div className="pl-lines stack">
        {!p?.id && <p className="small">Landfall's strategist can propose roles and labeled costs from the research already done, with at most one alternative. Nothing changes until you adopt it.</p>}
        {p?.run?.status === "failed" && <p className="small">{p.run.message ?? "The proposal failed."} Your plan didn't change.</p>}
        {p?.id && v.plan && p.made_from_version !== v.plan.version && <p className="small steel">Made from plan v{p.made_from_version}. Your amounts and roles win until you adopt.</p>}
        {a && <OptionView id={id} city={city} v={v} o={a} act={act} busy={busy} />}
        {(p?.faults?.length ?? 0) > 0 && <details><summary className="small steel">{p!.faults!.length} thing{p!.faults!.length === 1 ? "" : "s"} Landfall left out or relabeled</summary>
          <ul className="plain">{p!.faults!.map((f, i) => <li key={i} className="small steel">{f.reason}</li>)}</ul></details>}
        <button type="button" className="btn-secondary" disabled={busy || !!running || !v.plan}
          onClick={() => act("propose", () => api65.propose(id, city))}>{running ? "Proposing…" : p?.id ? "Propose again" : "Propose a plan"}</button>
      </div>}
    </div>
  );
}

/** v4 08 "Alternative plan": option B when the strategist returned a real one (G7: hidden otherwise); Compare opens it. */
export function AlternativeCard({ v, onCompare }: { v: PlanView; onCompare: () => void }) {
  const b = (v.proposal?.options ?? []).find((o) => o.id === "B");
  if (!b) return null;
  const names = b.methods.filter((m) => m.role === "MAIN" || m.role === "SUPPORTS").map((m) => LABEL[m.method]).join(" and ");
  return (
    <div className="pl-aside-card" aria-label="Alternative plan">
      <span className="label steel">Alternative plan</span>
      <div className="pl-alt"><span className="small"><b>{names || "Option B"}</b><span className="steel"> · {whole(b.preview.money.cash_out_known)} known{b.trade_off ? ` · ${b.trade_off}` : ""}</span></span>
        <button type="button" className="link" onClick={onCompare}>Compare</button></div>
    </div>
  );
}

/** The compare drawer: recommended (your plan) vs the alternative, then adopting it as a separate, explicit step. */
export function CompareAlternative({ id, city, v, act, busy }: { id: string; city: string; v: PlanView; act: Act; busy: boolean }) {
  const b = (v.proposal?.options ?? []).find((o) => o.id === "B");
  if (!b || !v.money) return <p className="small steel">No alternative now.</p>;
  const mine = v.methods.filter((m) => m.role && ["MAIN", "SUPPORTS"].includes(m.role.role)).map((m) => m.label).join(", ");
  const alt = b.methods.filter((m) => m.role === "MAIN" || m.role === "SUPPORTS").map((m) => LABEL[m.method]).join(", ");
  return (
    <div className="stack">
      <p className="small">Compared with your plan now.{b.why ? ` ${b.why}` : ""}</p>
      <ul className="plain small">
        <li>Methods: {mine || "none"} vs {alt}</li>
        <li>Known cash: {whole(v.money.cash_out_known)} vs {whole(b.preview.money.cash_out_known)}</li>
        <li>Not known: {v.money.unknown.length} vs {b.preview.money.unknown.length}</li>
        {b.trade_off && <li>Trade-off: {b.trade_off}</li>}
      </ul>
      <OptionView id={id} city={city} v={v} o={b} act={act} busy={busy} />
    </div>
  );
}

function OptionView({ id, city, v, o, act, busy }: { id: string; city: string; v: PlanView; o: ProposalOption; act: Act; busy: boolean }) {
  const p = v.proposal!;
  const adopted = p.adopted === o.id;
  return (
    <section className="stack" aria-label={`Option ${o.id}`}>
      <p className="small"><strong>Option {o.id}</strong>{adopted ? " · adopted" : ""}</p>
      <ul className="plain">{o.methods.filter((m) => m.role !== "NOT_NOW").map((m) => <li key={m.method} className="small">
        <strong>{LABEL[m.method]}</strong>: {ROLE[m.role]}{m.reason ? ` · ${m.reason}` : ""}</li>)}</ul>
      {o.why && <p className="small">{o.why}</p>}
      <p className="small">{plannedOf(o.preview.money)?.text ?? `${whole(o.preview.money.cash_out_known)} known${o.preview.money.unknown.length ? ` · ${o.preview.money.unknown.length} not known` : ""}`} · {FIT[o.preview.fit]}</p>
      {splitOf(o).length > 0 && <div className="small"><span className="steel">The rest of your budget goes to:</span>
        <ul className="plain">{splitOf(o).map((x) => <li key={x.lever}>{x.text ?? `${x.percent}% · ${x.label}`}{x.why ? <span className="steel"> · {x.why}</span> : null}</li>)}</ul></div>}
      {!adopted && <button type="button" className="btn-secondary" disabled={busy}
        onClick={() => act("adopt", () => api65.adopt(id, city, p.id!, p.version!, o.id, o.preview.base_version))}>Adopt option {o.id}</button>}
    </section>
  );
}
const ROLE: Record<Role, string> = { MAIN: "main test", SUPPORTS: "supports the main test", OPTIONAL: "optional", NOT_NOW: "not now" };

/** LF-PlanRationale (v4 08): the main column, two columns, v4's field labels; each field only when the planner returned it
 * (G8). An assumption outside "Assumed" says so. */
export function PlanRationale({ v, brand }: { v: PlanView; brand: string }) {
  const adopted = v.money?.rationale;
  const r: Rationale | undefined = adopted ?? v.proposal?.rationale;
  if (!r) return null;
  const fields: [keyof Rationale, string][] = [["brand", `Why this fits ${brand}`], ["city", `Why ${v.city.name}`], ["what_we_learn", "What we'll learn"],
    ["alternative", "Why not the alternative"], ["known", "Known"], ["assumed", "Assumed"], ["what_would_change", "What would change this"]];
  return (
    <section className="pl-rationale" aria-label="Why this plan">
      {!adopted && <p className="small steel pl-span">Landfall's proposal, not adopted yet:</p>}
      {fields.filter(([k]) => r[k]?.length).map(([k, label]) => <div key={k}><b>{label}</b>
        <p>{r[k]!.map((st, i) => <span key={i} title={st.downgraded ? `Shown as an assumption: ${st.downgraded}` : undefined}>{i ? " " : ""}{st.text}
          {st.kind === "ASSUMPTION" && k !== "assumed" && <span className="pl-assumed"> · assumed</span>}</span>)}</p></div>)}
    </section>
  );
}

/** LF-DigitalScenario (D): one total daily budget split across platforms; rates per platform, each labeled; the steps. */
export function ScenarioCard({ id, city, v, act, busy }: { id: string; city: string; v: PlanView; act: Act; busy: boolean }) {
  const s = v.scenario!;
  const [total, setTotal] = useState(major(s.total_daily));
  const [days, setDays] = useState(String(s.horizon_days));
  const [plats, setPlats] = useState<ScenarioPlatform[]>(s.platforms.length ? s.platforms
    : s.default_platforms.map((p) => ({ platform: p, allocation: null, allocation_by: "SPLIT", rates: { cpm: null, ctr: null, cvr: null, frequency: null }, input_states: {}, source: null, basis: "UNKNOWN" })));
  const set = (i: number, x: Partial<ScenarioPlatform>) => setPlats(plats.map((p, j) => (j === i ? { ...p, ...x } : p)));
  const toggle = (pl: string, on: boolean) => setPlats(on ? [...plats, { platform: pl, allocation: null, allocation_by: "SPLIT", rates: { cpm: null, ctr: null, cvr: null, frequency: null }, input_states: {}, source: null, basis: "UNKNOWN" }]
    : plats.filter((p) => p.platform !== pl));
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    act("scenario", () => api65.saveScenario(id, city, v.plan!.version, s.version, {
      total_daily: total ? { amount: total, ccy: s.currency } : null, horizon_days: Number(days),
      platforms: plats.map((p) => ({ platform: p.platform, rates: p.rates, input_states: p.input_states, source: p.source,
        allocation_by: p.allocation_by, allocation: p.allocation_by === "YOU_SAID" ? { amount: major(p.allocation), ccy: s.currency } : null })),
    }));
  };
  return (
    <div className="card stack" aria-label="Digital scenario">
      <h2 className="heading-sm">Digital scenario</h2>
      <p className="small steel">{s.label}. Qloo is never an input; a benchmark is not a forecast.</p>
      <form className="stack" onSubmit={save}>
        <div className="search-row">
          <label className="small">Total daily budget ({s.currency}) <input className="input" inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)} /></label>
          <label className="small">Days <input className="input" inputMode="numeric" value={days} size={4} onChange={(e) => setDays(e.target.value)} /></label>
        </div>
        <fieldset className="small"><legend>Platforms (the total is split across them)</legend>
          {s.platforms_offered.map((pl) => <label key={pl}><input type="checkbox" checked={plats.some((p) => p.platform === pl)} disabled={busy}
            onChange={(e) => toggle(pl, e.target.checked)} /> {PLATFORM[pl]}</label>)}</fieldset>
        {plats.map((p, i) => <PlatformInputs key={p.platform} p={p} ccy={s.currency} set={(x) => set(i, x)} />)}
        <button type="submit" className="btn-secondary" disabled={busy}>Save scenario</button>
      </form>
      {s.results && <ScenarioResults s={s} />}
    </div>
  );
}

const RATE_LABEL: Record<string, string> = { cpm: "CPM (cost per 1,000 impressions)", ctr: "CTR (fraction, 0.01 = 1%)", cvr: "Conversion rate (fraction)" };

function PlatformInputs({ p, ccy, set }: { p: ScenarioPlatform; ccy: string; set: (x: Partial<ScenarioPlatform>) => void }) {
  const rate = (k: "cpm" | "ctr" | "cvr", j: "low" | "mid" | "high", val: string) => {
    const cur = { low: "", mid: "", high: "", ...(p.rates[k] ?? {}) };
    const next = { ...cur, [j]: val };
    const any = next.low || next.mid || next.high;
    set({ rates: { ...p.rates, [k]: any ? next : null }, input_states: { ...p.input_states, [k]: any ? (p.input_states[k] === "SOURCED" ? "SOURCED" : "ASSUMED") : "UNKNOWN" } });
  };
  const sourced = Object.values(p.input_states).includes("SOURCED");
  return (
    <fieldset className="small stack"><legend>{PLATFORM[p.platform]}</legend>
      <label><input type="checkbox" checked={p.allocation_by === "YOU_SAID"} onChange={(e) => set({ allocation_by: e.target.checked ? "YOU_SAID" : "SPLIT" })} /> Set this platform's daily budget myself</label>
      {p.allocation_by === "YOU_SAID" && <label>Daily budget ({ccy}) <input className="input" inputMode="decimal" value={major(p.allocation)}
        onChange={(e) => set({ allocation: e.target.value ? { minor: Math.round(Number(e.target.value) * 10 ** ccyExp(ccy)), ccy } : null })} /></label>}
      {(["cpm", "ctr", "cvr"] as const).map((k) => <div key={k} className="search-row">
        <span>{RATE_LABEL[k]}{k === "cpm" ? ` (${ccy})` : ""}</span>
        {(["low", "mid", "high"] as const).map((j) => <label key={j}>{j} <input className="input" inputMode="decimal" size={6} value={(p.rates[k] ?? {})[j] ?? ""}
          aria-label={`${PLATFORM[p.platform]} ${k} ${j}`} onChange={(e) => rate(k, j, e.target.value)} /></label>)}
        <Chip label={p.input_states[k] ?? "UNKNOWN"} /></div>)}
      <label><input type="checkbox" checked={sourced} onChange={(e) => set({
        input_states: Object.fromEntries(Object.entries({ cpm: p.rates.cpm, ctr: p.rates.ctr, cvr: p.rates.cvr, frequency: p.rates.frequency })
          .map(([k, x]) => [k, x == null ? "UNKNOWN" : e.target.checked ? "SOURCED" : "ASSUMED"])),
        source: e.target.checked ? (p.source ?? { name: "", as_of: "", context: "" }) : null })} /> These rates come from a published source</label>
      {sourced && <div className="stack">
        {(["name", "url", "as_of", "context"] as const).map((f) => <label key={f}>{{ name: "Source", url: "Address (optional)", as_of: "Dated", context: "Context (platform, market, objective)" }[f]}
          <input className="input" value={(p.source as Record<string, string> | null)?.[f] ?? ""} onChange={(e) => set({ source: { ...(p.source ?? { name: "", as_of: "", context: "" }), [f]: e.target.value } })} /></label>)}</div>}
    </fieldset>
  );
}

function ScenarioResults({ s }: { s: DigitalScenario }) {
  const r = s.results!;
  const range = (x: { low: number; mid: number; high: number } | null) => (x ? `${x.low.toLocaleString()}–${x.high.toLocaleString()} (mid ${x.mid.toLocaleString()})` : "not known");
  return (
    <div className="stack">
      <p className="small"><strong>{s.media_spend ? `Media spend ${fmt(s.media_spend)}` : "No daily budget yet"}</strong>{s.total_daily && ` · ${fmt(s.total_daily)} a day × ${s.horizon_days} days`}</p>
      <table className="small"><thead><tr><th>Platform</th><th>A day</th><th>Impressions</th><th>Clicks</th><th>Conversions</th></tr></thead>
        <tbody>{r.platforms.map((p) => <tr key={p.platform}><td>{p.label} <Chip label={p.basis.replace("_", " ")} /></td><td>{p.allocation_text ?? "—"}</td>
          <td>{range(p.outputs.impressions)}</td><td>{range(p.outputs.clicks)}</td><td>{range(p.outputs.conversions)}</td></tr>)}
          <tr><td>All platforms</td><td>{fmt(s.total_daily)}</td><td>{range(r.aggregate.impressions)}</td><td>{range(r.aggregate.clicks)}</td><td>{range(r.aggregate.conversions)}</td></tr></tbody></table>
      {r.note && <p className="small steel">{r.note}: totals wait for every platform's rates. Unique reach needs a frequency.</p>}
      {r.platforms.filter((p) => p.below_minimum).map((p) => <p key={p.platform} className="small"><Chip label="BLOCKS EXPORT" /> {p.below_minimum!.text}</p>)}
      <details><summary className="small">How it's calculated</summary>{r.platforms.map((p) => <div key={p.platform}><span className="label steel">{p.label.toUpperCase()}</span>
        {p.calculation.length ? p.calculation.map((c, i) => <p key={i} className="small mono">{c}</p>) : <p className="small steel">No rates: nothing to calculate.</p>}</div>)}</details>
    </div>
  );
}

/** Money for LF-ChangePreview (B03/B04): known cost and unknown count now and after, the lines that change, and the
 * shared lines that stay because another included method still uses them. A method with no lines yet adds its cost items
 * as NOT KNOWN (the plan writes them as lines once it is in). */
export function changeMoney(v: PlanView, m: PlanMethod, op: "add" | "remove", items: string[]) {
  const lines = v.money?.lines ?? [];
  const inc = new Set(v.methods.filter((x) => x.role && ["MAIN", "SUPPORTS"].includes(x.role.role)).map((x) => x.method));
  const after = new Set(op === "remove" ? [...inc].filter((x) => x !== m.method) : [...inc, m.method]);
  const of = (set: Set<string>) => lines.filter((l) => l.used_by.some((u) => set.has(u)));
  const b = of(inc), a = of(after);
  const unknown = (ls: CostLine[]) => ls.filter((l) => !l.amount || l.unconverted).length;
  const known = (ls: CostLine[]) => ls.reduce((s, l) => s + (l.usd?.minor ?? 0), 0);
  const changed = op === "remove" ? b.filter((l) => !a.includes(l)) : a.filter((l) => !b.includes(l));
  const fresh = op === "add" && !lines.some((l) => l.used_by.includes(m.method)) ? items : [];
  const txt = (k: number, u: number) => `${whole({ minor: k, ccy: "USD" })} known · ${u} unknown`;
  return {
    before: txt(known(b), unknown(b)), after: txt(known(a), unknown(a) + fresh.length), changed, fresh,
    stays: op === "remove" ? lines.filter((l) => l.used_by.includes(m.method) && l.used_by.some((u) => u !== m.method && after.has(u)))
      .map((l) => ({ label: l.label, by: l.used_by.filter((u) => u !== m.method && after.has(u)).map((u) => LABEL[u]) })) : [],
  };
}
export { BASIS, LABEL };
