import { useId, useState } from "react";
import { api, type Goal, type Presence, type Project, type Purpose, type Scope, type StandIns } from "./api";
import { Chip } from "./ui";

// v4 LF-IntakeSentence option sets. "domestic" says "in my home market": v4's "In the US" would be wrong for a brand
// based elsewhere, and the home country isn't known at intake.
export const SCOPES: [Scope, string][] = [["international", "internationally"], ["domestic", "in my home market"], ["both", "both"]];
export const PURPOSES: [Purpose, string][] = [["test_demand", "test demand in a new market"], ["stockists", "find retail partners"],
  ["presence", "build awareness"]];

/** The goal in a few words, for the project header. */
export function goalLine(p: Project) {
  const it = p.intent;
  if (!it) return "";
  return `Expand ${SCOPES.find(([v]) => v === it.scope)?.[1]} to ${PURPOSES.find(([v]) => v === it.purpose)?.[1]}`;
}

export const usd = (n: number) => `US$${n.toLocaleString("en-US")}`;
const BUDGETS = [5000, 10000, 25000];
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
type Part = "scope" | "purpose" | "budget";
const PART_LABEL: Record<Part, string> = { scope: "Where", purpose: "What you want to learn", budget: "Starting budget" };

/** v4 LF-IntakeSentence: three tappable parts sized to their text; the open part shows its chips underneath. */
export function GoalSentence({ scope, purpose, onChange, ceiling, onCeiling }: {
  scope: Scope; purpose: Purpose; onChange: (c: { scope?: Scope; purpose?: Purpose }) => void;
  ceiling?: number; onCeiling?: (n: number) => void;
}) {
  const [open, setOpen] = useState<Part | null>(null);
  const [custom, setCustom] = useState(false);
  const [text, setText] = useState("");
  const rowId = useId();
  const part = (k: Part, label: string, value: string) => (
    <button type="button" className={`lfb-part${open === k ? " lfb-part-open" : ""}`} aria-expanded={open === k}
      aria-controls={rowId} aria-label={`${label}: ${value}`} onClick={() => { setOpen(open === k ? null : k); setCustom(false); }}>
      {value} ▾</button>
  );
  const chip = (key: string, label: string, on: boolean, go: () => void) => (
    <button key={key} type="button" className={`lfb-pick${on ? " lfb-pick-on" : ""}`} aria-pressed={on} onClick={go}>{label}</button>
  );
  const commit = () => { if (Number(text) > 0) { onCeiling?.(Number(text)); setOpen(null); setCustom(false); } };
  return (
    <div className="lfb-sentence-wrap">
      <p className="lfb-sentence">
        I'd like to expand {part("scope", "Where", SCOPES.find(([v]) => v === scope)![1])} to{" "}
        {part("purpose", "Why", PURPOSES.find(([v]) => v === purpose)![1])}
        {ceiling != null && onCeiling && <> with a starting budget of {part("budget", "Budget", usd(ceiling))}</>}.
      </p>
      {open && (
        <div className="lfb-picks" id={rowId} role="group" aria-label={PART_LABEL[open]}>
          <span className="label steel">{PART_LABEL[open]}</span>
          <div className="lfb-pick-row">
            {open === "scope" && SCOPES.map(([v, t]) => chip(v, cap(t), v === scope, () => { onChange({ scope: v }); setOpen(null); }))}
            {open === "purpose" && PURPOSES.map(([v, t]) => chip(v, cap(t), v === purpose, () => { onChange({ purpose: v }); setOpen(null); }))}
            {open === "budget" && <>
              {BUDGETS.map((n) => chip(String(n), usd(n), n === ceiling && !custom, () => { onCeiling?.(n); setOpen(null); }))}
              {chip("custom", "Custom", custom || !BUDGETS.includes(ceiling ?? 0), () => setCustom(true))}
            </>}
          </div>
          {open === "budget" && custom && (
            <span className="lfb-custom">US$<input inputMode="numeric" aria-label="Custom budget" autoFocus value={text}
              onChange={(e) => setText(e.target.value.replace(/\D/g, ""))} onBlur={commit}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } else if (e.key === "Escape") setCustom(false); }} /></span>
          )}
          {open === "budget" && <span className="small steel">Your ceiling for the first launch test, not a spending commitment.</span>}
        </div>
      )}
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  QLOO_STORE: "QLOO STORES", YOUR_SITE: "YOUR SITE", HOME: "HOME", YOU_SAID: "YOU SAID", QLOO_KEY_MARKET: "QLOO KEY MARKET",
  HOME_COUNTRY: "HOME COUNTRY", WEB: "WEB",
};

function useAct(reload: () => void) {
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return { err, busy, act };
}

export function HomeQuestion({ p, goal, reload }: { p: Project; goal: Goal; reload: () => void }) {
  const [iso, setIso] = useState("");
  const { err, busy, act } = useAct(reload);
  return (
    <form className="card-frame home-q" onSubmit={(e) => { e.preventDefault(); if (iso) act(() => api.intent(p.id, p.intent!.version, { home_country: iso })); }}>
      <span className="needs-you">Needs you</span>
      <h2 className="heading-sm"><label htmlFor="home-country">Which country is home?</label></h2>
      <p className="small steel">Your goal includes your home market, and your brief doesn't say where that is. We never broaden the goal to guess.</p>
      <div className="row-inline">
        <select id="home-country" className="input" value={iso} onChange={(e) => setIso(e.target.value)}>
          <option value="">Choose a country</option>
          {goal.countries.map((c) => <option key={c.iso} value={c.iso}>{c.name}</option>)}
        </select>
        <button type="submit" className="btn-primary" disabled={!iso || busy}>Save</button>
      </div>
      {err && <p className="field-error">{err}</p>}
    </form>
  );
}

export function PresencePanel({ p, pres, reload, catalog }: { p: Project; pres: Presence; reload: () => void; catalog?: { city_id: string; name: string }[] }) {
  const { err, busy, act } = useAct(reload);
  const [add, setAdd] = useState("");
  const edit = (e: Parameters<typeof api.presence>[2]) => act(() => api.presence(p.id, pres.version, e));
  const here = new Set(pres.cities.map((c) => c.city_id));
  return (
    <div className="presence">
      <p className="small"><Chip label={pres.store_search.status === "complete" ? "QLOO" : pres.store_search.status === "not_applicable" ? "NOT KNOWN" : "NOT CHECKED"} /> {pres.coverage_line}.</p>
      {pres.cities.length === 0 && <p className="small steel">No city where you already are, as far as Landfall knows.</p>}
      <ul className="plain presence-list">
        {pres.cities.map((c) => (
          <li key={c.city_id} className={c.excluded ? "" : "row-off"}>
            <strong>{c.name}</strong>{" "}
            <span className="tag-chips">{[...new Set(c.sources.map((s) => s.kind))].map((k) => <Chip key={k} label={SOURCE_LABEL[k]} />)}</span>{" "}
            {(c.sources as { kind: string; ref: unknown; quote?: string }[]).filter((s) => s.kind === "WEB").map((s) => <span key={String(s.ref)} className="small steel">“{s.quote}” </span>)}
            <span className="small steel">{c.excluded ? (pres.leave_out_presence ? "left out of the ranking" : "kept in: you chose to keep cities where you are") : "kept in: you said you're not there yet"}</span>{" "}
            <button type="button" className="link" disabled={busy} onClick={() => edit(c.excluded ? { remove_cities: [c.city_id] } : { add_cities: [c.city_id] })}>
              {c.excluded ? "We're not there yet" : "We are there"}</button>
          </li>
        ))}
      </ul>
      {pres.countries.length > 0 && (
        <>
          <div className="label steel">COUNTRIES · WHERE YOU SELL LEAVES ITS CITIES OUT; THE REST INFORM</div>
          <ul className="plain presence-list">
            {pres.countries.map((c) => { const out = c.leave_out || (c.present && pres.leave_out_presence); return (
              <li key={c.iso}><strong>{c.name}</strong>{" "}
                <span className="tag-chips">{[...new Set(c.sources.map((s) => s.kind))].map((k) => <Chip key={k} label={SOURCE_LABEL[k]} />)}{c.leave_out && <Chip label="YOU LEFT THIS OUT" />}</span>{" "}
                {c.present && !c.leave_out && <span className="small steel">{c.sources.some((s) => s.kind === "YOUR_SITE") ? "you said on your site you sell here" : "the web says you sell here"}{pres.leave_out_presence ? ", so its cities are left out " : " "}</span>}
                <button type="button" className="link" disabled={busy} onClick={() => edit(out ? { restore_countries: [c.iso] } : { leave_out_countries: [c.iso] })}>
                  {out ? "Include this country" : "Leave out this country"}</button></li>
            ); })}
          </ul>
        </>
      )}
      {pres.unresolved_regions.length > 0 && <p className="small steel">Qloo also lists {pres.unresolved_regions.join(", ")} as a key market: a region, not a country, so it leaves nothing out.</p>}
      {pres.site_not_used.length > 0 && <p className="small steel">Not used until checked: {pres.site_not_used.map((f) => f.value).join("; ")} <Chip label="TO CHECK" /></p>}
      {catalog && (
        <form className="row-inline" onSubmit={(e) => { e.preventDefault(); if (add) { edit({ add_cities: [add] }); setAdd(""); } }}>
          <label className="small" htmlFor="pres-add">We're also in</label>
          <select id="pres-add" className="input" value={add} onChange={(e) => setAdd(e.target.value)}>
            <option value="">a catalog city…</option>
            {catalog.filter((c) => !here.has(c.city_id)).map((c) => <option key={c.city_id} value={c.city_id}>{c.name}</option>)}
          </select>
          <button type="submit" className="btn-secondary" disabled={!add || busy}>Add</button>
        </form>
      )}
      {err && <p className="field-error">{err}</p>}
    </div>
  );
}

export function StandInsPanel({ p, si, reload }: { p: Project; si: NonNullable<StandIns>; reload: () => void }) {
  const { err, busy, act } = useAct(reload);
  const [q, setQ] = useState("");
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [found, setFound] = useState<{ qloo_id: string; name: string; short_description: string }[] | null>(null);
  if (si.state === "pending" || si.state === "not_started") return <p className="small steel">Qloo is picking brands with a similar following…</p>;
  const ids = si.brands.map((b) => b.qloo_id);
  const set = (next: string[]) => act(() => api.standins(p.id, si.version ?? 0, next));
  return (
    <div className="standins">
      <p className="small">{si.state === "failed" ? "Qloo's brand lookup failed. Add a brand with a similar following to continue." : si.line}</p>
      {si.brands.length > 0 && (
        <ul className="plain presence-list">
          {si.brands.map((b) => (
            <li key={b.qloo_id}><strong>{b.name}</strong> <Chip label={b.origin === "YOU_SAID" ? "YOU SAID" : "QLOO"} />{" "}
              <button type="button" className="link" disabled={busy} onClick={() => set(ids.filter((x) => x !== b.qloo_id))}>Remove</button></li>
          ))}
        </ul>
      )}
      <p className="small steel">{si.genre_line}{si.excluded?.length ? ` · left out as charities: ${si.excluded.map((x) => x.name).join(", ")}` : ""}. Never treated as competitors: Qloo ranks cities for people who like them, as a stand-in for you.</p>
      <form className="row-inline" onSubmit={async (e) => { e.preventDefault(); setFound(null); setSearchErr(null); try { setFound((await api.searchBrands(p.id, q)).results); } catch (x) { setSearchErr((x as Error).message); } }}>
        <label className="small" htmlFor="si-q">Add a brand with a similar following</label>
        <input id="si-q" className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Brand name" />
        <button type="submit" className="btn-secondary" disabled={q.trim().length < 2}>Search Qloo</button>
      </form>
      {found && (found.length === 0 ? <p className="small steel">No Qloo brand matches '{q}'.</p> : (
        <ul className="plain">{found.map((b) => (
          <li key={b.qloo_id} className="small"><strong>{b.name}</strong> {b.short_description}{" "}
            <button type="button" className="link" disabled={busy || ids.includes(b.qloo_id)} onClick={() => { set([...ids, b.qloo_id]); setFound(null); setQ(""); }}>
              {ids.includes(b.qloo_id) ? "Added" : "Add"}</button></li>))}</ul>
      ))}
      {(err || searchErr) && <p className="field-error">{err || searchErr}</p>}
    </div>
  );
}

// ---- SH2 context strip: "International · Test demand · Up to US$10,000 · Beachhead: Brisbane · Edit" ----
const SCOPE_SHORT: Record<Scope, string> = { international: "International", domestic: "Domestic", both: "At home and abroad" };
const PURPOSE_SHORT: Record<Purpose, string> = { presence: "Market presence", stockists: "Find stockists", test_demand: "Test demand" };

export function ContextStrip({ p, onEdit }: { p: Project; onEdit: () => void }) {
  const it = p.intent;
  if (!it) return null;
  return (
    <div className="context-strip" aria-label="Your goal">
      <span>{SCOPE_SHORT[it.scope]}</span> · <span>{PURPOSE_SHORT[it.purpose]}</span> ·{" "}
      <span>{it.ceiling_usd ? `Up to ${usd(it.ceiling_usd)}` : "No ceiling"}{it.ceiling_by === "DEFAULT" ? " (default)" : ""}</span>
      {p.beachhead && <> · <span>Beachhead: {p.beachhead.name}</span></>}
      {"  "}<button type="button" className="link" onClick={onEdit}>Edit</button>
    </div>
  );
}

/** The Edit drawer: goal and ceiling. A ceiling change re-runs nothing; a goal change shows the usual Update notice. */
export function ContextEdit({ p, reload }: { p: Project; reload: () => void }) {
  const it = p.intent!;
  const [note, setNote] = useState<string | null>(null);
  const { err, busy, act } = useAct(reload);
  const save = (change: { scope?: Scope; purpose?: Purpose; ceiling_usd?: number }) =>
    act(async () => setNote((await api.intent(p.id, it.version, change)).notice
      + ("ceiling_usd" in change ? ` Ceiling ${usd(change.ceiling_usd!)}; nothing re-runs.` : "")));
  return (
    <div className="stack">
      <h2 className="heading">Your goal</h2>
      <GoalSentence scope={it.scope} purpose={it.purpose} onChange={save} ceiling={it.ceiling_usd ?? 10000}
        onCeiling={(n) => save({ ceiling_usd: n })} />
      <p className="small steel">The ceiling is for your first launch test: never a spending target, and separate from Landfall's own running costs.
        {it.ceiling_by === "DEFAULT" ? " It is the default until you change it." : ""}</p>
      {busy && <p className="small steel">Saving…</p>}
      {note && <p className="small" role="status">{note}</p>}
      {err && <p className="field-error">{err} Your change wasn't saved.</p>}
    </div>
  );
}
