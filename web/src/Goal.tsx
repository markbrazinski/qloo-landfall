import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { api, type Audience, type Goal, type Presence, type Project, type Purpose, type Scope, type StandIns } from "./api";
import { Chip } from "./ui";

export const SCOPES: [Scope, string][] = [["international", "internationally"], ["domestic", "domestically"], ["both", "both at home and abroad"]];
export const PURPOSES: [Purpose, string][] = [["presence", "expand my market presence"], ["stockists", "find stores and stockists to carry us"],
  ["test_demand", "test demand before committing"]];

/** An underlined word that opens a menu: Enter or Space opens, arrows move, Enter picks, Escape closes. At 390 px a native
 *  <select> replaces it (CSS swaps them). */
function Menu<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(0);
  const id = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const current = options.findIndex(([v]) => v === value);
  useEffect(() => { if (open) { setAt(Math.max(0, current)); list.current?.focus(); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (i: number) => { onChange(options[i][0]); setOpen(false); btn.current?.focus(); };
  const key = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setAt((at + 1) % options.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setAt((at - 1 + options.length) % options.length); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(at); }
    else if (e.key === "Escape" || e.key === "Tab") { setOpen(false); btn.current?.focus(); }
  };
  return (
    <span className="goal-word">
      <button ref={btn} type="button" className="goal-menu" aria-haspopup="listbox" aria-expanded={open} aria-controls={id}
        aria-label={`${label}: ${options[current]?.[1]}`} onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); } }}>
        {options[current]?.[1]} ▾
      </button>
      {open && (
        <ul ref={list} id={id} role="listbox" aria-label={label} tabIndex={-1} className="goal-list" onKeyDown={key}
          aria-activedescendant={`${id}-${at}`} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
          {options.map(([v, text], i) => (
            <li key={v} id={`${id}-${i}`} role="option" aria-selected={v === value} className={i === at ? "goal-active" : ""}
              onMouseDown={(e) => e.preventDefault()} onClick={() => pick(i)}>{text}</li>
          ))}
        </ul>
      )}
      <select className="goal-select" aria-label={label} value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </span>
  );
}

/** The goal in a few words, for the project header. */
export function goalLine(p: Project) {
  const it = p.intent;
  if (!it) return "";
  return `Expand ${SCOPES.find(([v]) => v === it.scope)?.[1]} to ${PURPOSES.find(([v]) => v === it.purpose)?.[1]}`;
}

export function GoalSentence({ scope, purpose, onChange }: { scope: Scope; purpose: Purpose; onChange: (c: { scope?: Scope; purpose?: Purpose }) => void }) {
  return (
    <p className="goal-sentence">
      I would like to expand <Menu label="Where" value={scope} options={SCOPES} onChange={(v) => onChange({ scope: v })} /> to{" "}
      <Menu label="Why" value={purpose} options={PURPOSES} onChange={(v) => onChange({ purpose: v })} />.
    </p>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  QLOO_STORE: "QLOO STORES", YOUR_SITE: "YOUR SITE", HOME: "HOME", YOU_SAID: "YOU SAID", QLOO_KEY_MARKET: "QLOO KEY MARKET",
  HOME_COUNTRY: "HOME COUNTRY",
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

/** The goal on the brief: the same sentence, editable; a change is a notice first, then an explicit Update. */
export function GoalRow({ p, reload, onNotice }: { p: Project; reload: () => void; onNotice: (t: string) => void }) {
  const it = p.intent!;
  const { err, act } = useAct(reload);
  return (
    <div className="row row-3">
      <span className="row-label">Your goal</span>
      <span><GoalSentence scope={it.scope} purpose={it.purpose}
        onChange={(c) => act(async () => onNotice((await api.intent(p.id, it.version, c)).notice))} />
        {err && <span className="field-error">{err}</span>}</span>
      <Chip label="YOU SAID" />
    </div>
  );
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
            <span className="small steel">{c.excluded ? (pres.leave_out_presence ? "left out of the ranking" : "kept in: you chose to keep cities where you are") : "kept in: you said you're not there yet"}</span>{" "}
            <button type="button" className="link" disabled={busy} onClick={() => edit(c.excluded ? { remove_cities: [c.city_id] } : { add_cities: [c.city_id] })}>
              {c.excluded ? "We're not there yet" : "We are there"}</button>
          </li>
        ))}
      </ul>
      {pres.countries.length > 0 && (
        <>
          <div className="label steel">COUNTRIES · THEY INFORM, THEY DON'T LEAVE CITIES OUT</div>
          <ul className="plain presence-list">
            {pres.countries.map((c) => (
              <li key={c.iso}><strong>{c.name}</strong>{" "}
                <span className="tag-chips">{[...new Set(c.sources.map((s) => s.kind))].map((k) => <Chip key={k} label={SOURCE_LABEL[k]} />)}{c.leave_out && <Chip label="YOU LEFT THIS OUT" />}</span>{" "}
                <button type="button" className="link" disabled={busy} onClick={() => edit(c.leave_out ? { restore_countries: [c.iso] } : { leave_out_countries: [c.iso] })}>
                  {c.leave_out ? "Include this country" : "Leave out this country"}</button></li>
            ))}
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

export function AudiencePanel({ p, aud, reload }: { p: Project; aud: Audience; reload: () => void }) {
  const { err, busy, act } = useAct(reload);
  if (aud.state === "pending") return <p className="small steel">Asking Qloo who likes your brand…</p>;
  if (aud.state === "no_signal") return <p className="small steel">Not known yet: Qloo needs your brand or a brand with a similar following first.</p>;
  const failed = aud.status && Object.values(aud.status).includes("failed");
  return (
    <div className="audience">
      <p className="small"><Chip label="QLOO" /> {aud.label}.</p>
      {aud.from_previous_standins && <p className="small steel">From your previous brands with a similar following. Update the recommendations to refresh it.</p>}
      <div className="label steel">AUDIENCES</div>
      {aud.status?.audiences === "failed" ? <p className="small">Not known: Qloo's audience lookup failed.</p>
        : aud.audiences.length === 0 ? <p className="small steel">None {aud.removed.length ? "left" : "returned"}.</p> : (
          <span className="tag-chips">{aud.audiences.map((a) => (
            <span key={a.uuid} className="tag">{a.name}{" "}
              <button type="button" className="link-plain" aria-label={`Remove the audience ${a.name}`} disabled={busy}
                onClick={() => act(() => api.removeAudience(p.id, aud.version!, [a.uuid]))}>×</button></span>))}</span>
        )}
      {aud.removed.length > 0 && <p className="small steel">You removed: {aud.removed.map((a) => a.name).join(", ")}.</p>}
      <div className="label steel">AGE AND GENDER SKEW · NOT SHARES</div>
      {aud.status?.demographics === "failed" ? <p className="small">Not known: Qloo's demographics lookup failed.</p> : (
        <ul className="plain">
          {aud.demographics!.lines.map((l) => <li key={l.signal_id} className="small">{aud.kind === "standins" ? `People who like ${l.signal_name}: ` : `People who like ${l.signal_name} `}{l.text}</li>)}
          {aud.demographics!.summary === "mixed" && <li className="small"><Chip label="MIXED" /> These skews point in different directions, so there is no single profile.</li>}
        </ul>
      )}
      {failed && <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api.retryAudience(p.id))}>Ask Qloo again</button>}
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

/** The brief's slice-3.5 group: where you already are, who likes you, stand-ins, and the home-country question. */
export function GoalGroup({ p, reload }: { p: Project; reload: () => void }) {
  const g = p.goal;
  if (!g) return null;
  return (
    <>
      {g.needs_home && <HomeQuestion p={p} goal={g} reload={reload} />}
      <section className="card group" aria-labelledby="pres-h">
        <div className="group-head"><h2 id="pres-h" className="subheading">Where you already are</h2><span className="small steel">These cities are left out of the ranking</span></div>
        <PresencePanel p={p} pres={g.presence} reload={reload} catalog={g.catalog} />
        {g.home_unknown_international && <p className="small steel">Your home country isn't known, so no city is left out as home.</p>}
      </section>
      {g.standins && (
        <section className="card group" aria-labelledby="si-h">
          <div className="group-head"><h2 id="si-h" className="subheading">Brands with a similar following</h2><span className="small steel">Qloo doesn't know your brand yet</span></div>
          <StandInsPanel p={p} si={g.standins} reload={reload} />
        </section>
      )}
      <section className="card group" aria-labelledby="aud-h">
        <div className="group-head"><h2 id="aud-h" className="subheading">Who likes you, according to Qloo</h2><span className="small steel">A cultural relationship, not your customer data</span></div>
        <AudiencePanel p={p} aud={g.audience} reload={reload} />
      </section>
    </>
  );
}
