import { type FormEvent, useMemo, useState } from "react";
import { api } from "./api";
import { Chip, Lockup } from "./ui";

const TEAM = [
  ["Brand reader", "01 Brand", "Reads your public site and separates what it says from what we infer.", "A brief you can correct, with sources"],
  ["Market scout", "02 Markets", "Uses Qloo cultural relationships to find places related to your brand in up to 80 cities.", "A few cities worth a closer look, with reasons"],
  ["Researcher", "03 Opportunities", "Checks each lead against its own site and independent sources, and keeps failed checks visible.", "Verified local partners and what is still unknown"],
  ["Strategist", "03–04", "Develops launch options and compares them on the same terms.", "A recommendation, an alternative and the trade-offs"],
  ["Analyst", "04–05", "Estimates costs for each launch option, separating cash, deposits and expense, and shows assumptions.", "Labelled costs and what you would need to pay out"],
  ["Creative & outreach", "05 Pursue", "Makes the launch tangible and drafts the first message to a partner.", "A campaign kit and an inquiry for you to approve"],
];
const BUILT = [
  ["QLOO", "Cultural relationships", "Which places relate to your brand's taste. Not demand."],
  ["SOURCE", "Independent research", "Whether a place exists and what it says about itself."],
  ["TEAM READ", "Reasoning", "Approaches and how they compare."],
  ["CALC", "Calculations", "Costs, cash needed and what is assumed."],
  ["DRAFT", "Creative work", "Previews and copy you can show a partner."],
];

export default function Entry() {
  const [url, setUrl] = useState("");
  const [adv, setAdv] = useState(false);
  const [fields, setFields] = useState({ objective: "Explore where to expand for me", ceiling: "", include: "", exclude: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const idem = useMemo(() => crypto.randomUUID(), []);

  const start = async (body: object) => {
    setBusy(true);
    setError(null);
    try {
      const { project_id } = await api.create(body, idem);
      window.location.assign(`/p/${project_id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    start({
      url, objective: fields.objective, ceiling_usd: fields.ceiling ? Number(fields.ceiling) : null,
      include: fields.include || null, exclude: fields.exclude || null,
    });
  };
  const set = (k: keyof typeof fields) => (e: { target: { value: string } }) => setFields({ ...fields, [k]: e.target.value });

  return (
    <div className="app">
      <header className="entry-header"><Lockup /></header>
      <main className="entry">
        <section className="entry-left">
          <h1 className="display">Your next market. A team to get you started.</h1>
          <p className="lede">Give us your website. Your team works out where you could expand, what you could try there, and helps you take the first step. You make every call that matters.</p>
          <form className="intake card-frame" onSubmit={submit} noValidate>
            <label htmlFor="url" className="field-title">Your brand's website</label>
            <input id="url" className="input input-lg" placeholder="yourbrand.com" value={url} autoComplete="url"
                   onChange={(e) => setUrl(e.target.value)} aria-invalid={!!error} aria-describedby={error ? "url-error" : undefined} />
            {error && <p id="url-error" className="field-error">{error}</p>}
            <button type="submit" className="btn-primary btn-block" disabled={busy || !url.trim()}>
              {busy ? "Starting…" : "Start exploring"}
            </button>
            <p className="small steel">We read public pages only. Nothing is sent or published on your behalf.</p>
            <div className="demo-row">
              <span className="label steel">OR TRY A DEMO</span>
              <button type="button" className="pill" onClick={() => start({ demo: true })} disabled={busy}>
                Tidewell · sparkling cold-brewed tea
              </button>
            </div>
            <div className="default-box">
              <Chip label="DEFAULT" /> <strong>Explore where to expand for me</strong>
              <p className="small steel">No budget, objective or location needed. We ask only when one would change a decision.</p>
            </div>
            <button type="button" className="link" aria-expanded={adv} onClick={() => setAdv(!adv)}>
              {adv ? "Hide advanced options" : "Advanced options (all optional)"}
            </button>
            {adv && (
              <div className="adv-grid">
                <label>Objective
                  <select className="input" value={fields.objective} onChange={set("objective")}>
                    <option>Explore where to expand for me</option>
                    <option>Test demand in a new country</option>
                    <option>Find retail partners abroad</option>
                  </select>
                </label>
                <label>Most you'd spend
                  <input className="input" inputMode="numeric" placeholder="Not set" value={fields.ceiling}
                         onChange={(e) => setFields({ ...fields, ceiling: e.target.value.replace(/\D/g, "") })} />
                </label>
                <label>Include places<input className="input" placeholder="Anywhere" value={fields.include} onChange={set("include")} /></label>
                <label>Leave out<input className="input" placeholder="None" value={fields.exclude} onChange={set("exclude")} /></label>
                <p className="small steel span2">Saved with your brief as your intent. They shape later stages.</p>
              </div>
            )}
          </form>
        </section>
        <section className="entry-right">
          <div className="label steel">MEET YOUR TEAM</div>
          <h2 className="title">Six specialists working from one brief</h2>
          <p className="steel">Each one owns a piece of the work and hands you something you can use. They work in the background; you see results and the occasional question.</p>
          <div className="team-grid">
            {TEAM.map(([name, step, does, gets]) => (
              <div className="card team-card" key={name}>
                <div className="team-top"><h3>{name}</h3><span className="mono steel">{step}</span></div>
                <p>{does}</p>
                <div className="team-gets"><div className="label steel">YOU GET</div><p>{gets}</p></div>
              </div>
            ))}
          </div>
          <div className="card built">
            <div className="built-head"><h3>How a recommendation is built</h3><span className="small steel">Each step is labelled wherever its results appear.</span></div>
            <div className="built-grid">
              {BUILT.map(([chip, t, d]) => (
                <div key={t}><Chip label={chip} /><h4>{t}</h4><p className="small steel">{d}</p></div>
              ))}
            </div>
          </div>
          <ul className="promises">
            <li>Nothing is sent or published without your approval.</li>
            <li>Unknowns stay beside the decision, never counted as zero.</li>
            <li>Every claim opens its source.</li>
          </ul>
          <p className="mono steel small">The demo uses Tidewell, a fictional brand, with recorded data. Results depend on your brand and what each source returns.</p>
        </section>
      </main>
    </div>
  );
}
