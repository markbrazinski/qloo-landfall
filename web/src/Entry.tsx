import { type FormEvent, useEffect, useMemo, useState } from "react";
import { api, type Purpose, type Scope, sessionTrack, type SessionTrack } from "./api";
import "./brand.css";
import { GoalSentence, PURPOSES, SCOPES, usd } from "./Goal";

// v4 01: stage · name · what they do · what you get
const TEAM = [
  ["01 Brand", "Brand reader", "Reads your public site and separates what it says from what we infer.", "A brief you can correct, with sources"],
  ["02 Markets", "Market scout", "Uses Qloo cultural relationships to find places related to your brand across 80 cities.", "One recommended city, two alternatives, and why"],
  ["03 Plan", "Method specialists", "Check retail, pop-up, creator, community and digital leads against their own sources.", "Real candidates, and what’s still unknown"],
  ["03 Plan", "Planner", "Combines the strongest methods into one entry plan within your budget.", "A recommended plan, an alternative and the trade-offs"],
  ["04 Prepare", "Creative & outreach", "Drafts the messages, shelf materials and ads for each method.", "A launch kit to review and edit"],
  ["05 Act & adapt", "Delivery", "Sends what you approve where it can, and gives you everything to use yourself where it can’t.", "A record of what went out, and the next step after a reply"],
];

// Owner, 28 Oct 2026: the demo track replays two real brands, recorded end to end with the default goal (fixtures/demo/).
const DEMOS = [
  ["deathwishcoffee", "Death Wish Coffee", "deathwishcoffee.com · coffee"],
  ["untuckit", "UNTUCKit", "untuckit.com · shirts and apparel"],
] as const;

export default function Entry() {
  const [url, setUrl] = useState("");
  const [ceiling, setCeiling] = useState<number | null>(null);  // null: untouched, so the server records the default
  const [goal, setGoal] = useState<{ scope: Scope; purpose: Purpose }>({ scope: "international", purpose: "test_demand" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const idem = useMemo(() => crypto.randomUUID(), []);
  // A1 (owner, 27 Oct 2026): a judge or owner link gets live research; everyone else gets the recorded demo.
  const [track, setTrack] = useState<SessionTrack["track"] | null>(null);
  useEffect(() => { sessionTrack().then((s) => setTrack(s.track), () => setTrack("live")); }, []);
  const demo = track === "demo";
  const [demoCase, setDemoCase] = useState<string>(DEMOS[0][0]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!demo && !url.trim()) return setError("Enter your brand’s website to start.");
    setBusy(true);
    setError(null);
    try {
      const target = demo ? { demo: true, demo_case: demoCase } : { url };
      const { project_id } = await api.create({ ...target, ...goal, ...(ceiling ? { ceiling_usd: ceiling } : {}) }, idem);
      window.location.assign(`/p/${project_id}`);
    } catch (x) {
      setError((x as Error).message);
      setBusy(false);
    }
  };

  return (
    <main className="lfb-intake-page">
      <div className="lfb-intake-lockup"><a href="/" aria-label="Landfall home"><img src="/lockup.svg" alt="landfall" height={26} /></a></div>
      <div className="lfb-intake">
        <section className="lfb-intake-left">
          <h1 className="lfb-intake-h1">Your next market. A team to get you started.</h1>
          <p className="lfb-intake-lede">Give us your website. Your team works out where you could expand, what you could try there, and helps you take the first step. You make every call that matters.</p>
          {track && <form className="lfb-intake-form" onSubmit={submit} noValidate>
            {demo
              ? <div className="stack" role="radiogroup" aria-label="Recorded demo">
                <p className="lfb-intake-lede">Choose a brand. Each is a full recorded run, from the brief to Act &amp; adapt. Live research is open to judges through the link they were given.</p>
                {DEMOS.map(([k, name, sub]) => (
                  <label key={k} className={`prep-option ${demoCase === k ? "on" : ""}`}>
                    <input type="radio" name="demo-case" checked={demoCase === k} onChange={() => setDemoCase(k)} />
                    <span className="prep-option-body">
                      <span className="prep-option-name"><b>{name}</b><span className="chip chip-proposal">RECORDED</span></span>
                      <span className="steel">{sub}</span>
                      <span className="mono steel">Recorded demo · replayed, nothing live</span>
                    </span>
                  </label>
                ))}
              </div>
              : <label className="lfb-url">
                <span>Your brand's website</span>
                <input id="url" placeholder="yourbrand.com" value={url} autoComplete="url" onChange={(e) => setUrl(e.target.value)}
                  aria-invalid={!!error} aria-describedby={error ? "url-error" : undefined} />
              </label>}
            {error && <p id="url-error" className="field-error" role="alert">{error}</p>}
            {demo  // the recordings ran with the default goal; a replay can only follow it
              ? <p className="lfb-sentence">I'd like to expand {SCOPES[0][1]} to {PURPOSES[0][1]} with a starting budget of {usd(10000)}.</p>
              : <GoalSentence scope={goal.scope} purpose={goal.purpose} onChange={(c) => setGoal({ ...goal, ...c })}
                ceiling={ceiling ?? 10000} onCeiling={setCeiling} />}
            <button type="submit" className="btn-primary btn-block" disabled={busy}>{busy ? "Starting…" : demo ? "Start the demo" : "Start exploring"}</button>
            <span className="mono steel">{demo ? "Replayed from a recording: no live calls, nothing goes out." : "We read public pages only. Nothing goes out until you approve it."}</span>
          </form>}
        </section>
        <section className="lfb-intake-right">
          <span className="label steel">Meet your team</span>
          <h2 className="lfb-team-h2">Six specialists working from one brief</h2>
          <p className="steel">Each owns a piece of the work and hands you something you can use. They work in the background; you see results and the occasional question.</p>
          <div className="lfb-team">
            {TEAM.map(([stage, name, does, gets]) => (
              <div className="lfb-team-card" key={name}>
                <div><span className="mono steel">{stage}</span><h3>{name}</h3></div>
                <p>{does}</p>
                <div className="lfb-team-gets"><span className="label steel">You get</span><span>{gets}</span></div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
