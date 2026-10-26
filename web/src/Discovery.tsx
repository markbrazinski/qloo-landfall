import { type FormEvent, useEffect, useState } from "react";
import { api, type Candidate, type Claim, type Project, type QlooTag, type Signal } from "./api";
import { Chip, Skeleton } from "./ui";

const typeOf = (c: Candidate) => (c.types[0] || "").replace("urn:entity:", "") || "entity";

function corroborationChips(c: Candidate) {
  const k = c.corroboration;
  return [
    k.name_match !== "different" && "NAME MATCHES YOUR SITE",
    k.domain_match === true && "WEBSITE MATCHES",
    k.domain_match === false && "DIFFERENT WEBSITE",
    k.site_mentions_name === true && "ON YOUR HOMEPAGE",
  ].filter(Boolean) as string[];
}

/** LF-IdentityChooser: radio cards (native radios: arrow keys move, Space selects), "Neither" last. */
export function IdentityChooser({ p, onDone }: { p: Project; onDone?: () => void }) {
  const ident = p.identity;
  const current = ident.decision === "none" ? "none" : ident.decision?.entity_id ?? null;
  const [pick, setPick] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const one = ident.candidates.length === 1;
  const reading = p.run?.status === "running" || p.run?.status === "queued";
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!pick || ident.version == null) return;
    setBusy(true);
    try {
      await api.chooseIdentity(p.id, ident.version, pick);
      onDone?.();
    } catch (x) {
      setErr((x as Error).message);
      setBusy(false);
    }
  };
  return (
    <form className="chooser card-frame" onSubmit={submit} aria-labelledby="chooser-h">
      <span className="needs-you">Needs you</span>
      <h2 id="chooser-h" className="heading">{one ? "Is this you?" : "Which of these is you?"}</h2>
      <p className="steel">
        {one ? `Qloo has one brand that might be ${ident.site_name}.` : `Qloo has ${ident.candidates.length} brands that might be ${ident.site_name}.`}{" "}
        Picking yours lets the Market scout use its cultural relationships.{reading ? " We keep reading your site meanwhile." : ""}
      </p>
      <div className="cand-grid" role="radiogroup" aria-labelledby="chooser-h">
        {ident.candidates.map((c) => (
          <label key={c.qloo_id} className={`cand ${pick === c.qloo_id ? "cand-on" : ""}`}>
            <input type="radio" name="identity" value={c.qloo_id} checked={pick === c.qloo_id} onChange={() => setPick(c.qloo_id)} />
            <span className="cand-body">
              <strong>{c.name} · {typeOf(c)}</strong>{current === c.qloo_id && <span className="mono steel"> · current</span>}
              {c.short_description && <span className="small">{c.short_description}</span>}
              {c.tags.length > 0 && (
                <span className="tag-chips">{c.tags.slice(0, 3).map((t) => <span key={t.qloo_id} className="tag">{t.name}</span>)}<Chip label="QLOO" /></span>
              )}
              <span className="tag-chips">{corroborationChips(c).map((x) => <Chip key={x} label={x} />)}</span>
            </span>
          </label>
        ))}
        <label className={`cand cand-none ${pick === "none" ? "cand-on" : ""}`}>
          <input type="radio" name="identity" value="none" checked={pick === "none"} onChange={() => setPick("none")} />
          <span className="cand-body"><strong>Neither. Continue without a Qloo match</strong>
            <span className="small steel">We'll propose a profile of Qloo tags from your brief for you to check.</span></span>
        </label>
      </div>
      {err && <p className="field-error">{err}</p>}
      <div className="btn-row">
        <button type="submit" className="btn-primary" disabled={!pick || busy}>
          {pick === "none" ? "Continue without a match" : "This is us"}
        </button>
      </div>
    </form>
  );
}

export function QlooDrawer({ c, p }: { c: Candidate; p: Project }) {
  const groups: Record<string, QlooTag[]> = {};
  c.tags.forEach((t) => (groups[t.type_label || t.type] ||= []).push(t));
  const prof = p.profile?.route === "brand_entity" ? p.profile : null;
  return (
    <div className="evidence">
      <div className="label steel">QLOO ENTITY</div>
      <h2 className="heading">{c.name} · {typeOf(c)}</h2>
      <Chip label="QLOO" />
      {c.short_description && <p>{c.short_description}</p>}
      <dl className="checks">
        <dt>Qloo id</dt><dd className="mono">{c.qloo_id}</dd>
        <dt>Website in Qloo</dt><dd>{c.official_site || "not given"}</dd>
        <dt>How it was settled</dt><dd>{p.identity.decided_by === "landfall" ? `Matched automatically: ${p.identity.basis.join(" · ")}` : "Chosen by you"}</dd>
        <dt>Searched as</dt><dd>{p.identity.queries.map((q) => `“${q}”`).join(", ")}</dd>
      </dl>
      <div className="label steel">WHAT QLOO ASSOCIATES WITH {c.name.toUpperCase()}</div>
      {(prof?.display_tags.length ? prof.display_tags : Object.entries(groups).map(([type, tags]) => ({ type, tags }))).map((g) => (
        <div key={g.type} className="tag-group"><span className="mono steel">{g.type}</span>
          <span className="tag-chips">{g.tags.map((t) => <span key={t.qloo_id} className="tag">{t.name}</span>)}</span></div>
      ))}
      <div className="means">
        <p className="small">These are cultural relationships from Qloo: what people who like {c.name} also tend to like. They are not demand, audience size or sales.</p>
      </div>
    </div>
  );
}

const ROUTE: Record<string, string> = {
  brand_entity: "Your Qloo brand: the Market scout searches from what Qloo associates with it",
  proposed_profile: "A profile of Qloo tags proposed from your brief, which you confirm",
  description_profile: "A profile of Qloo tags proposed from your description, which you confirm",
};

/** The brief's Discovery group: identity, route, and what Qloo associates with the brand (an assembled view). */
export function DiscoveryGroup({ p, onNotYou, onQloo }: { p: Project; onNotYou: () => void; onQloo: (c: Candidate) => void }) {
  const ident = p.identity;
  const prof = p.profile;
  const [busy, setBusy] = useState(false);
  const failed = ident.status === "failed" || ident.status === "not_started";
  const route = prof && !prof.outdated ? prof.route : null;  // an older route never stands in for the current one
  return (
    <section className="card group" aria-labelledby="disc-h">
      <div className="group-head"><h2 id="disc-h" className="subheading">Discovery</h2><span className="small steel">How the Market scout will search</span></div>
      <div className="row row-3">
        <span className="row-label">Qloo identity</span>
        {failed ? (
          <span>Qloo identity not established
            <div className="mono steel prov">{ident.failure_reason || "not looked up"} ·{" "}
              <button type="button" className="link" disabled={busy} onClick={() => { setBusy(true); api.retryIdentity(p.id).finally(() => setBusy(false)); }}>Retry Qloo lookup</button></div></span>
        ) : ident.chosen ? (
          <span>{ident.chosen.name} · {typeOf(ident.chosen)}
            <div className="mono steel prov">{ident.decided_by === "landfall" ? "Matched automatically · " : "Chosen by you · "}
              <button type="button" className="link" onClick={onNotYou}>Not you?</button></div></span>
        ) : (
          <span>No Qloo match
            <div className="mono steel prov">{ident.decided_by === "founder" ? "You said none of Qloo's brands is you" : ident.basis.join(" · ")}
              {ident.candidates.length > 0 && <> · <button type="button" className="link" onClick={onNotYou}>Change</button></>}</div></span>
        )}
        {failed ? <Chip label="UNKNOWN" /> : ident.chosen ? <Chip label="QLOO" onClick={() => onQloo(ident.chosen!)} title="What Qloo knows about this brand" />
          : <Chip label={ident.decided_by === "founder" ? "YOU SAID" : "NO MATCH"} />}
      </div>
      <div className="row row-3">
        <span className="row-label">Discovery route</span>
        <span>{failed ? "Waits for a Qloo identity" : route ? ROUTE[route] : prof?.state === "failed" ? "No profile yet: the proposal failed"
          : "Proposing a profile from your brief"}
          {prof && !failed && <div className="mono steel prov">{!route ? (prof.state === "failed" ? "retry or add signals below" : "the Profile agent is working")
            : prof.confirmed ? (prof.confirmed_by === "landfall" ? "used automatically" : "confirmed by you") : "not confirmed yet"}</div>}</span>
        <Chip label={failed || prof?.state === "failed" ? "UNKNOWN" : route === "brand_entity" ? "QLOO" : prof?.confirmed ? "YOU SAID" : "INFERRED"} />
      </div>
      {prof?.route === "brand_entity" && !prof.outdated && (
        <div className="row row-3">
          <span className="row-label">What Qloo associates with you</span>
          <span>
            {prof.display_tags.map((g) => (
              <div key={g.type} className="tag-group"><span className="mono steel">{g.type}</span>
                <span className="tag-chips">{g.tags.map((t) => <span key={t.qloo_id} className="tag">{t.name}</span>)}</span></div>
            ))}
            {!prof.display_tags.length && <span className="steel">Qloo returned no tags of the kinds we use.</span>}
            <div className="mono steel prov">a cultural relationship, not demand</div>
          </span>
          <Chip label="QLOO" />
        </div>
      )}
    </section>
  );
}

const ORIGIN: Record<Signal["origin"], string> = { QLOO: "QLOO", QLOO_CATEGORY: "QLOO · CATEGORY", COMPARABLE: "COMPARABLE · NOT YOU", YOU_SAID: "YOU SAID" };
const COMPARABLE_NOTE = "Off by default: Qloo finds comparable brands by matching words, not shared audiences. Tick it if this brand's customers are like yours.";

export function signalAsClaim(s: Signal): Claim {
  return { id: s.qloo_id, label: `Signal · ${s.name}`, value: s.type_label ? `${s.name} (${s.type_label})` : s.name, state: "INFERRED",
    vc: "I", basis: null, reason: null, quote: null, source: null, corrected: false, quote_found: null, support: null, checker: null,
    inferred_from: s.inferred_from };
}

/** The profile editor (WI-A4): remove, tick comparables, add via Qloo tag search, confirm. */
export function ProfileEditor({ p, onInference, reload }: { p: Project; onInference: (c: Claim) => void; reload: () => Promise<unknown> }) {
  const prof = p.profile!;
  const [rows, setRows] = useState<Signal[]>(prof.signals);
  const [dirty, setDirty] = useState(false);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<{ results: QlooTag[]; hidden: number; query: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [searching, setSearching] = useState(false);
  useEffect(() => { setRows(prof.signals); setDirty(false); }, [prof.version, prof.signals.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const change = (next: Signal[]) => { setRows(next); setDirty(true); };
  const save = async (confirm: boolean) => {
    setBusy(true); setErr(null);
    try {
      await api.editProfile(p.id, prof.version, rows.map(({ qloo_id, kind, included }) => ({ qloo_id, kind, included })), confirm);
      await reload();
      setDirty(false);
    } catch (x) { setErr((x as Error).message); }
    setBusy(false);
  };
  const search = async (e: FormEvent) => {
    e.preventDefault();
    if (searching) return;
    setErr(null);
    setSearching(true);
    try { setFound(await api.searchTags(p.id, q)); } catch (x) { setErr((x as Error).message); }
    setSearching(false);
  };
  const failedRun = prof.state === "failed";
  const included = rows.filter((r) => r.included).length;

  return (
    <section className="card group profile" aria-labelledby="prof-h">
      <div className="group-head">
        <h2 id="prof-h" className="subheading">Your discovery profile</h2>
        <span className="row-actions">{prof.version > 0 && <span className="mono steel">v{prof.version}</span>}
          <Chip label={prof.confirmed && !dirty ? "CONFIRMED" : "NOT CONFIRMED"} /></span>
      </div>
      <p className="small steel intro">With no Qloo match, we describe your customers' taste in Qloo's own tags, each inferred from your brief. Remove what doesn't fit, add your own, then confirm. The market scan waits for your confirmation.</p>
      {prof.proposing && (
        <div className="rows" aria-live="polite"><p className="small">The Profile agent is searching Qloo's tags for your brand…</p>
          {[0, 1, 2].map((i) => <div className="row" key={i}><Skeleton width="30%" /><Skeleton width="55%" /></div>)}</div>
      )}
      {failedRun && (
        <div className="notice notice-changed" role="status"><Chip label="FAILED" />
          <span>We couldn't propose a profile ({prof.run!.message || prof.run!.status}). Retry, or add signals yourself below.{" "}
            <button type="button" className="link" onClick={() => api.retryProfile(p.id).then(reload)}>Retry</button></span></div>
      )}
      {prof.state === "waiting" && <p className="small steel">The Profile agent starts as soon as your brief is ready.</p>}
      {prof.state === "ready" && rows.length === 0 && (
        <p className="small">No usable Qloo tags were found for your brief. Add signals yourself: search for a word that describes your brand or customers.</p>
      )}
      {rows.map((s) => {
        const comparable = s.origin === "COMPARABLE";
        const changed = s.inferred_from?.changed.length;
        return (
          <div className="row-wrap" key={s.qloo_id}>
            <div className={`row row-3 ${!s.included ? "row-off" : ""}`}>
              <span className="row-label">{s.name}{s.type_label && <div className="mono steel">{s.type_label}</div>}</span>
              <span>
                {comparable && (
                  <label className="tick"><input type="checkbox" checked={s.included}
                    onChange={() => change(rows.map((r) => (r.qloo_id === s.qloo_id ? { ...r, included: !r.included } : r)))} />
                    Use this comparable brand</label>
                )}
                {s.reasoning && <div className="small">{s.reasoning}</div>}
                {s.inferred_from ? (
                  <button type="button" className="link mono" onClick={() => onInference(signalAsClaim(s))}>
                    Inferred from: {[...new Set(s.inferred_from.facts.map((f) => f.label))].join(" · ")}</button>
                ) : <div className="mono steel prov">{s.origin === "YOU_SAID" ? "added by you from a Qloo search" : ""}</div>}
                {comparable && <div className="small steel note">{COMPARABLE_NOTE}</div>}
              </span>
              <span className="row-actions">
                {changed ? <Chip label="REVIEW · FACT CHANGED" /> : null}
                {s.inferred_from && !s.inferred_from.established ? <Chip label="BASIS NOT ESTABLISHED" /> : null}
                <Chip label={ORIGIN[s.origin]} />
                <button type="button" className="link" aria-label={`Remove ${s.name}`} onClick={() => change(rows.filter((r) => r.qloo_id !== s.qloo_id))}>Remove</button>
              </span>
            </div>
          </div>
        );
      })}
      <form className="add-signal" onSubmit={search} role="search">
        <label className="field-title small" htmlFor="add-signal">Add a signal</label>
        <div className="search-row">
          <input id="add-signal" className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="A word for your brand or customers, e.g. outdoor" />
          <button type="submit" className="btn-secondary" disabled={q.trim().length < 2 || searching}>{searching ? "Searching…" : "Search Qloo"}</button>
        </div>
        {found && (
          <ul className="found" aria-label={`Qloo tags for ${found.query}`}>
            {found.results.map((t) => {
              const have = rows.some((r) => r.qloo_id === t.qloo_id);
              return (
                <li key={t.qloo_id}><span>{t.name} <span className="mono steel">{t.type_label}</span></span>
                  <button type="button" className="link" disabled={have} onClick={() => change([...rows, { kind: "tag", qloo_id: t.qloo_id, name: t.name, tag_type: t.type,
                    type_label: t.type_label || null, origin: "YOU_SAID", included: true, reasoning: null }])}>{have ? "Added" : "Add"}</button></li>
              );
            })}
            {!found.results.length && <li className="small steel">No usable Qloo tags for “{found.query}”. Try a shorter or singular word.</li>}
            {found.hidden > 0 && <li className="small steel">{found.hidden} place categories or other kinds of tag were left out.</li>}
          </ul>
        )}
      </form>
      {err && <p className="field-error">{err}</p>}
      {prof.dropped.length > 0 && (
        <details className="small steel dropped"><summary>{prof.dropped.length} proposal{prof.dropped.length > 1 ? "s" : ""} dropped by validation</summary>
          <ul>{prof.dropped.map((d) => <li key={d.qloo_id + d.reason}>{d.name || d.qloo_id}: {d.reason}</li>)}</ul></details>
      )}
      <div className="btn-row profile-actions">
        {dirty && <button type="button" className="btn-secondary" disabled={busy} onClick={() => save(false)}>Save changes</button>}
        <button type="button" className="btn-primary" disabled={busy || included === 0 || (prof.confirmed && !dirty)} onClick={() => save(true)}>
          {prof.confirmed && !dirty ? "Profile confirmed" : "Confirm profile"}</button>
        <span className="small steel">{included} signal{included === 1 ? "" : "s"} in use</span>
      </div>
    </section>
  );
}
