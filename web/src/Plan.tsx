import { useEffect, useRef, useState } from "react";
import "./plan.css";
import { api45, api65, costItemsOf, draftOf, foundNameOf, qlooLine, type PlanMethod, type PlanView, type Role, useLive, useProject } from "./api";
import { ActivityDrawer, stagesFor, usageLine } from "./Brand";
import { ContextEdit, ContextStrip, goalLine } from "./Goal";
import { Chip, DecisionBar, DrawerStack, Link, Notice, ProjectHeader, Skeleton, StageNav, navigate, trapTab, useDrawers } from "./ui";
import { useMinPlay } from "./pace";
import { AlternativeCard, BASIS, CompareAlternative, CostLines, CostReturnStrip, PlanRationale, ProposalCard, ScenarioCard, changeMoney, whole } from "./PlanMoney";

export const ROLE_LABEL: Record<Role, string> = { MAIN: "Main test", SUPPORTS: "Supports the main test", OPTIONAL: "Optional", NOT_NOW: "Not now" };
const INCLUDED: Role[] = ["MAIN", "SUPPORTS"];
const ORDER: Role[] = ["MAIN", "SUPPORTS", "OPTIONAL", "NOT_NOW"];
const PHRASE: Record<string, string> = { retail: "a shelf trial", popup: "a pop-up", creator: "a local creator", community: "a local group", digital: "local ads" };
const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** v4 08: a method's role as shown. A method outside the plan is Optional, or Not now when Landfall's adopted option set it
 * aside, with that option's reason. */
export function shownRole(v: PlanView, m: PlanMethod): { role: Role; reason: string | null } {
  if (m.role) return { role: m.role.role, reason: m.role.role === "NOT_NOW" ? m.role.note : null };
  const opt = (v.proposal?.options ?? []).find((o) => o.id === v.proposal?.adopted);
  const x = opt?.methods.find((y) => y.method === m.method && y.role === "NOT_NOW");
  return x ? { role: "NOT_NOW", reason: x.reason || null } : { role: "OPTIONAL", reason: null };
}

/** The decision bar's sentence, built from the plan's roles (as v4 builds it), never from a model's prose. */
export function planTitle(v: PlanView, included: PlanMethod[]) {
  const main = included.find((m) => m.role?.role === "MAIN");
  if (!main) return `Your plan for ${v.city.name}`;
  const sup = included.filter((m) => m.role?.role === "SUPPORTS").map((m) => PHRASE[m.method] ?? m.label.toLowerCase());
  return `Start with ${PHRASE[main.method] ?? main.label.toLowerCase()} in ${v.city.name}${sup.length ? `, supported by ${sup.join(" and ")}` : ""}`;
}

/** v4 08 · the plan for the beachhead city: one sentence, the cost-and-return strip, the method cards with Add / Remove
 * through a change preview, the rationale, and the aside (cost lines, dependencies, open questions, alternative).
 * "Prepare this plan" signs off the versions on screen (owner decision D2: no separate sign-off step). */
export default function PlanPage({ id, city }: { id: string; city: string }) {
  const { project: p, reload: reloadProject } = useProject(id);
  const { data: v, error, reload } = useLive(id, (pid) => api65.plan(pid, city));
  const drawers = useDrawers();
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [developing, setDeveloping] = useState<string[]>([]);
  const [preview, setPreview] = useState<{ m: PlanMethod; op: "add" | "remove" } | null>(null);
  const paced = usePacedBuild(v?.auto ?? null);
  if (error && !v) return <div className="app"><main className="page"><div className="error-panel card">{error} <Link className="link" to={`/p/${id}/markets`}>Back to Markets</Link></div></main></div>;
  if (!p || !v) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const both = () => { reload(); reloadProject(); };
  const plan = v.plan;
  const brand = p.brief?.brand_name || p.site;
  const byRole = (a: PlanMethod, b: PlanMethod) => ORDER.indexOf(shownRole(v, a).role) - ORDER.indexOf(shownRole(v, b).role);
  const included = v.methods.filter((m) => m.role && INCLUDED.includes(m.role.role)).sort(byRole);
  const others = v.methods.filter((m) => !(m.role && INCLUDED.includes(m.role.role))).sort(byRole);
  const current = (m: PlanMethod) => !!m.draft && !!m.investigation.dossier && m.draft.built_on === m.investigation.dossier.version;
  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setErr(null);
    try { await fn(); both(); return true; } catch (e) { setErr((e as Error).message); return false; } finally { setBusy(null); }
  };
  const mains = included.filter((m) => m.role?.role === "MAIN").length;
  const running = included.filter((m) => ["running", "queued"].includes(m.investigation.status));
  const auto = paced;  // owner, 27 Oct 2026: choosing the city builds the plan; the page shows it happening
  const building = !!auto && ["queued", "running"].includes(auto.status);
  const autoStopped = !!auto && !building && auto.phase !== "ready" && auto.status !== "completed";
  const toDevelop = included.filter((m) => m.investigation.dossier && !current(m));
  const signed = !!plan?.signed_off && !plan.changed_since_sign_off;
  const blocked = building ? "Landfall is building your plan"
    : !plan || !included.length ? (autoStopped || !plan ? "Build your plan first" : "Add a method to the plan first")
    : mains !== 1 ? "Add a main test first" : running.length ? `Wait for ${running.map((m) => m.label).join(", ")} to finish`
    : included.find((m) => !m.investigation.dossier) ? `Investigate ${included.find((m) => !m.investigation.dossier)!.label} first`
    : developing.length && toDevelop.length ? `Landfall is drafting ${toDevelop.map((m) => m.label).join(", ")}` : null;
  // D2: Prepare signs off exactly the versions on screen (drafting any that are missing first), then opens Prepare
  const prepare = () => act("prepare", async () => {
    if (signed) return navigate(`/p/${id}/prepare/${city}`);
    if (toDevelop.length) {
      for (const m of toDevelop) await api45.develop(id, m.investigation.dossier!.id, m.investigation.dossier!.version);
      setDeveloping(toDevelop.map((m) => m.method));
      return;
    }
    const expect = { ...Object.fromEntries(included.map((m) => [m.method, { dossier_version: m.investigation.dossier!.version, draft_version: m.draft!.version }])),
      plan_version: plan!.version };
    await api65.signOff(id, city, plan!.version, expect);
    await api65.makeMockups(id, city).catch(() => null);  // a mockup that can't start never blocks the plan
    navigate(`/p/${id}/prepare/${city}`);
  });
  const primary = { label: busy === "prepare" ? "Preparing…" : "Prepare this plan", ...(blocked ? { disabledReason: blocked } : { onClick: prepare }) };
  const editContext = () => drawers.push({ key: "ctx", kicker: "YOUR GOAL", render: () => <ContextEdit p={p} reload={both} /> });
  const openActivity = () => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> });
  const compare = () => drawers.push({ key: "alt", kicker: "ALTERNATIVE PLAN", render: () => <CompareAlternative id={id} city={city} v={v} act={act} busy={!!busy} /> });
  const main = included.find((m) => m.role?.role === "MAIN");
  const cityUp = v.city.name.toUpperCase();
  const bar = building ? { kicker: `PLAN · ${cityUp} · BUILDING`, title: `Building your plan for ${v.city.name}`, subtitle: "This can take up to 4 minutes. Methods appear as they're investigated. Nobody is contacted." }
    : { kicker: `PLAN · ${cityUp}${plan ? ` · PLAN V${plan.version}` : ""}`, title: planTitle(v, included),
        subtitle: (main && draftOf(main)?.concept.line) || (included.length ? "One launch test, with the methods that support it." : "Landfall researches the city, investigates every way in and proposes your first launch test.") };
  const fiveLine = <div className="pl-five"><span className="small steel">{v.city.name} · five methods:</span>
    {[...v.methods].sort(byRole).map((m) => { const r = shownRole(v, m).role; return <Link key={m.method} to={`/p/${id}/plan/${city}/${m.method}`} className="pl-five-chip">
      {m.label} · {INCLUDED.includes(r) && m.role ? "in plan" : ROLE_LABEL[r].toLowerCase()}</Link>; })}</div>;
  return (
    <div className="app">
      <ProjectHeader brand={brand} site={p.site} intent={goalLine(p)} briefVersion={p.brief?.version}
        running={v.research.status === "running" || !!running.length || building} usage={usageLine(p)} demo={p.demo}
        onBrief={() => navigate(`/p/${id}`)} onActivity={openActivity} />
      <StageNav stages={stagesFor(p)} current={2} />
      <ContextStrip p={p} onEdit={editContext} />
      <main id="main" className="pl-page">
        {plan?.changed_since_sign_off && !building && <div className="pl-banner" role="status"><b>Plan v{plan.version} changed since you prepared v{plan.signed_version}.</b>{" "}
          Prepare and Act use v{plan.signed_version} until you prepare this plan again. A method you removed stops at once; earlier versions and delivery records are kept.</div>}
        <DecisionBar kicker={bar.kicker} title={bar.title} subtitle={bar.subtitle} primary={primary} />
        {building && <div className="pl-progress" aria-hidden="true"><span style={{ width: `${(auto!.steps.filter((s) => s.state === "done").length / auto!.steps.length) * 100}%` }} /></div>}
        <div className="page pl-body">
          {!v.beachhead && <Notice kind="info">{v.city.name} isn't your beachhead now. This plan is kept as it was; <Link className="link" to={`/p/${id}/markets`}>choose it again in Markets</Link> to continue it.</Notice>}
          {plan?.stale.map((s) => <Notice key={s} kind="changed">{s}</Notice>)}
          {err && !preview && <p className="field-error" role="alert">{err}</p>}
          {developing.length > 0 && toDevelop.length > 0 && <Notice kind="info">Landfall is drafting {toDevelop.map((m) => m.label).join(", ")} before this plan can be prepared. Nothing is sent.</Notice>}
          {(building || autoStopped) && auto && <BuildProgress auto={auto} v={v} busy={!!busy} onRetry={() => act("auto", () => api65.autoPlan(id, city))} />}
          {!auto && !plan && <section className="pl-aside-card" aria-label="Your plan">
            <p className="small">Landfall researches the city, investigates every way in, and proposes your first launch test for you to review.</p>
            <button type="button" className="btn-secondary" disabled={!!busy || !v.beachhead} onClick={() => act("auto", () => api65.autoPlan(id, city))}>Build my plan</button>
          </section>}
          {!building && <CostReturnStrip v={v} />}
          {!building && <div className="pl-split">
            <div className="pl-main">
              <h2 className="label steel">In this plan</h2>
              {included.length === 0 ? <p className="small steel">No method is in the plan yet. Add one below; the first you add is the main test.</p>
                : <div className="pl-c3">{included.map((m) => <MethodCard key={m.method} id={id} city={city} v={v} m={m} included busy={!!busy}
                  onAct={() => { setErr(null); setPreview({ m, op: "remove" }); }} />)}</div>}
              {others.length > 0 && <><h2 className="label steel">Could add</h2>
                <div className="pl-c2">{others.map((m) => <MethodCard key={m.method} id={id} city={city} v={v} m={m} included={false} busy={!!busy}
                  onAct={() => { setErr(null); setPreview({ m, op: "add" }); }} />)}</div></>}
              <PlanRationale v={v} brand={brand} />
              {fiveLine}
            </div>
            <aside className="pl-aside">
              <CostLines id={id} city={city} v={v} act={act} busy={!!busy} />
              <DependsOn included={included} />
              <AlternativeCard v={v} onCompare={compare} />
              <ProposalCard id={id} city={city} v={v} act={act} busy={!!busy} />
              {v.scenario && included.some((m) => m.method === "digital") && <Collapsed open title="Digital ads scenario" note="daily budget and rates">
                <ScenarioCard key={`${v.scenario.version}`} id={id} city={city} v={v} act={act} busy={!!busy} /></Collapsed>}
            </aside>
          </div>}
        </div>
        {preview && <ChangePreview v={v} m={preview.m} op={preview.op} busy={!!busy} err={err} onCancel={() => { setPreview(null); setErr(null); }}
          onConfirm={async () => { if (await act("role", () => changePlan(id, city, v, preview.m, preview.op))) setPreview(null); }} />}
      </main>
      <DrawerStack d={drawers} />
    </div>
  );
}

/** B03/B04 confirm: Add fills a missing Main test first, else Supports; Remove moves the method to Optional (its research
 * stays). One new plan version, written only after the preview. */
export function changePlan(id: string, city: string, v: PlanView, m: PlanMethod, op: "add" | "remove") {
  const hasMain = v.methods.some((y) => y.role?.role === "MAIN" && y.method !== m.method);
  return api65.savePlan(id, city, v.plan?.version ?? null, v.methods.filter((x) => x.role || x.method === m.method)
    .map((x) => ({ method: x.method, role: x.method !== m.method ? x.role!.role : op === "remove" ? "OPTIONAL" : hasMain ? "SUPPORTS" : "MAIN" })));
}

/** The channel's planned amount, beside its name (owner, 28 Oct 2026): known costs plus its lines' labeled estimates. */
function cardCost(v: PlanView, m: PlanMethod) {
  const b = (v.money as { by_method?: Record<string, { amount: { minor: number; ccy: string }; estimated: boolean; not_known: number }> } | null)?.by_method?.[m.method];
  if (!b || (!b.amount.minor && b.not_known)) return b?.not_known ? `${b.not_known} not known` : null;  // an unknown is never US$0
  return `${b.estimated ? "≈ " : ""}${whole(b.amount)}${b.not_known ? ` · ${b.not_known} not known` : ""}`;
}

/** LF-MethodCard (v4 08): name, role tag, the plan line, "Tells you", the reason when not now, the cost, Details · Remove
 * (or Add to plan). Solid when in the plan, dashed when it could be added. */
function MethodCard({ id, city, v, m, included, busy, onAct }: { id: string; city: string; v: PlanView; m: PlanMethod; included: boolean; busy: boolean; onAct: () => void }) {
  const { role, reason } = shownRole(v, m);
  const dr = draftOf(m);
  const found = m.investigation.found;
  const line = dr?.concept.name || foundNameOf(m) || (m.investigation.status === "not_investigated" ? null : found?.concept);
  const investigating = ["running", "queued"].includes(m.investigation.status);
  const [inv, setInv] = useState<string | null>(null);  // no auto-plan: a method can still be investigated from its card
  const investigate = async () => {
    setInv("…");
    try { await api45.investigate(id, city, m.method); setInv(null); } catch (e) { setInv((e as Error).message); }
  };
  return (
    <article className={`pl-card ${included ? "pl-in" : "pl-out"}`} aria-label={m.label}>
      <div className="pl-name"><div className="pl-name-row"><h3>{m.label}</h3>{included && cardCost(v, m) && <span className="pl-amount mono">{cardCost(v, m)}</span>}</div>
        <span className="pl-tag">{ROLE_LABEL[role]}</span></div>
      {line ? <p className="pl-line">{line}</p> : <p className="pl-line steel">{investigating ? "Being investigated…" : m.investigation.status === "failed" ? "The investigation didn't finish." : "Not investigated yet."}</p>}
      {dr?.success_measure ? <p className="pl-tells"><span className="steel">Tells you: </span>{dr.success_measure}</p>
        : found && found.kept.length > 0 && <p className="pl-tells"><span className="steel">Found: </span>{found.kept.join(", ")}</p>}
      {qlooLine(m.why_it_fits) && <p className="pl-tells small"><span className="steel">Why: Qloo · </span>{qlooLine(m.why_it_fits)}</p>}
      {!included && reason && <p className="pl-why">{reason}</p>}
      {m.investigation.stale && m.investigation.stale.length > 0 && <p className="pl-why">Changed: {m.investigation.stale.join(" · ")}</p>}
      <div className="pl-links">
        <Link to={`/p/${id}/plan/${city}/${m.method}`} className="link">Details</Link>
        <button type="button" className="link" disabled={busy} onClick={onAct}>{included ? "Remove" : "Add to plan"}</button>
        {["not_investigated", "failed"].includes(m.investigation.status) && <button type="button" className="link" disabled={busy || inv === "…"} onClick={investigate}>
          Investigate {m.label.toLowerCase()}</button>}
      </div>
      {inv && inv !== "…" && <p className="field-error">{inv}</p>}
    </article>
  );
}

/** v4 08 aside "Depends on": what each included method waits on, as sentences, not counts. */
function DependsOn({ included }: { included: PlanMethod[] }) {
  const rows = included.filter((m) => m.dependencies.length);
  if (!rows.length) return null;
  return (
    <div className="pl-aside-card" aria-label="Depends on">
      <span className="label steel">Depends on</span>
      {rows.map((m) => <p key={m.method} className="small">{m.label} waits on {m.dependencies.map((d) => `${d.text}${d.state === "TO_CHECK" ? " (to check)" : ""}`).join(", ")}.</p>)}
    </div>
  );
}


/** A secondary panel kept out of the way: v4 has no home for it on Plan, so it opens on request. */
function Collapsed({ title, note, children, open: initial = false }: { title: string; note: string; children: React.ReactNode; open?: boolean }) {
  const [open, setOpen] = useState(initial);
  return (
    <div className="pl-aside-card pl-flush">
      <button type="button" className="pl-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span><b>{title}</b><span className="steel"> · {note}</span></span><span className="pl-show">{open ? "Hide" : "Show"}</span></button>
      {open && <div className="pl-lines">{children}</div>}
    </div>
  );
}

/** LF-ChangePreview (v4 B03 / B04): nothing is written until Confirm. Objective, activities, the cost lines that change
 * with their amounts and knowledge, the shared lines that stay, and the known cost now and after. */
export function ChangePreview({ v, m, op, busy, err, onCancel, onConfirm }: {
  v: PlanView; m: PlanMethod; op: "add" | "remove"; busy: boolean; err: string | null; onCancel: () => void; onConfirm: () => void;
}) {
  const dr = draftOf(m);
  const money = changeMoney(v, m, op, costItemsOf(m));
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>(".pl-close")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); trapTab(e, ref.current); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); opener?.focus(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const title = op === "remove" ? `Remove ${m.label} from the plan?` : `Add ${m.label} to the plan?`;
  const objective = dr?.concept.line || m.investigation.found?.concept;
  const isMain = op === "remove" && m.role?.role === "MAIN";
  const becomesMain = op === "add" && !v.methods.some((x) => x.role?.role === "MAIN");
  const sign = op === "remove" ? "−" : "+";
  return (
    <div className="drawer-layer pl-dialog-layer">
      <div className="drawer-scrim" onClick={onCancel} aria-hidden />
      <div className="pl-dialog" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="pl-dialog-head"><h2>{title}</h2><button type="button" className="pl-close" aria-label="Close" onClick={onCancel}>×</button></div>
        <div className="pl-dialog-body">
          <p>Nothing is lost. The research stays under other methods to consider, and the current version is kept.
            {op === "remove" && " Its prepared packages move to \"Earlier plan\" and can't be approved or sent; their history stays."}</p>
          <div><b>Objective</b><p>{objective ? `You ${op === "remove" ? "lose" : "add"}: ${lower(objective)}` : "Not drafted yet."}</p></div>
          <div><b>Activities</b><p>{dr?.activities.length ? `${op === "remove" ? "Removed" : "Added"}: ${dr.activities.map(lower).join("; ")}` : "None drafted yet."}</p></div>
          {becomesMain && <p className="small">{m.label} becomes the main test: the plan has none now.</p>}
          {(money.changed.length > 0 || money.fresh.length > 0) && <div><b>Cost lines that change</b>
            {money.changed.map((l) => <div key={l.id} className="pl-dl-line"><span>{sign} {l.label}</span><span className="pl-amt">{l.text ?? "—"}</span><Chip label={BASIS[l.basis]} /></div>)}
            {money.fresh.map((x) => <div key={x} className="pl-dl-line"><span>+ {x}</span><span className="pl-amt">—</span><Chip label="NOT KNOWN" /></div>)}</div>}
          {money.stays.length > 0 && <div><b>Shared lines that stay</b>{money.stays.map((s) => <p key={s.label}>{s.label} stays: {s.by.join(" and ")} still {s.by.length > 1 ? "use" : "uses"} it</p>)}</div>}
          <div className="pl-nowafter"><span><span className="steel">Now</span><br /><b>{money.before}</b></span><span><span className="steel">After</span><br /><b>{money.after}</b></span></div>
          {isMain && <div className="pl-warn"><b>Depends on this</b><span>{m.label} is the main test. Add another method as the main test before you prepare the plan.</span></div>}
          {err && <p className="field-error" role="alert">{err}</p>}
          <div className="pl-dialog-actions">
            <button type="button" className="btn-primary" disabled={busy} onClick={onConfirm}>{busy ? "Saving…" : `${op === "remove" ? "Remove" : "Add"} ${m.label}`}</button>
            <button type="button" className="link" onClick={onCancel}>Cancel</button></div>
        </div>
      </div>
    </div>
  );
}

const STEP_MS = 2000;  // owner, 28 Oct 2026: a plan built ahead still plays its build, 2 s per block
const METHOD_NAMES = ["retail", "creator", "pop-up", "community", "digital"];
type Auto = NonNullable<PlanView["auto"]> & { run_id?: string; ended_at?: string | null };

/** The build as shown: for its first moments on this page (a build that is running, or one that finished in the last
 * minute, e.g. a city prepared in advance), each block plays for 2 s, the five investigations ticking off together in
 * theirs; it never runs ahead of the real build. After that, or for a build seen later, it is the build as it is. */
function usePacedBuild(a: Auto | null): Auto | null {
  const live = !!a && ["queued", "running"].includes(a.status);
  const ready = !!a && a.phase === "ready";
  const recent = live || (ready && !!a?.ended_at && Date.now() - Date.parse(a.ended_at) < 60_000);
  const t = useMinPlay(a?.run_id && `build-${a.run_id}`, STEP_MS * (a?.steps.length || 4), recent);
  if (!a || t >= 1 || !(live || ready)) return a;
  const real = ready ? a.steps.length : a.steps.filter((s) => s.state === "done").length;
  const pos = t * a.steps.length;
  const at = Math.min(real, Math.floor(pos));
  const k = Math.min(METHOD_NAMES.length, Math.floor((pos - at) * (METHOD_NAMES.length + 1)));  // concurrent ticks
  return { ...a, status: "running", steps: a.steps.map((s, i) => ({ ...s, state: i < at ? "done" : i === at ? "running" : "waiting",
    detail: i !== at ? null : s.key === "investigate" && at < real
      ? `${k} of ${METHOD_NAMES.length} done · ` + METHOD_NAMES.map((n, j) => n + (j < k ? " ✓" : "…")).join(", ")
      : s.state === "running" ? s.detail : null })) };
}

/** The plan being built (v4 02/04 progress pattern): a labeled step list with status words, the findings as each method
 * finishes, and, when it stops, the same list with the stopped step marked and a way to try again. */
function BuildProgress({ auto, v, busy, onRetry }: { auto: NonNullable<PlanView["auto"]>; v: PlanView; busy: boolean; onRetry: () => void }) {
  const live = ["queued", "running"].includes(auto.status);
  const word = { done: "Done", running: "Running…", waiting: "Waiting", stopped: "Stopped" } as const;
  const done = auto.steps.filter((s) => s.state === "done").length;
  const found = v.methods.filter((m) => m.investigation.dossier && !["running", "queued"].includes(m.investigation.status));
  return (
    <div className="pl-build stack">
      <section className="pl-steps" aria-label="Building your plan" aria-busy={live}>
        <div className="pl-steps-head"><span className="label steel">Plan builder · {done} of {auto.steps.length}</span></div>
        <ol className="plain">
          {auto.steps.map((s) => <li key={s.key} className={`pl-step pl-step-${s.state}`}>
            <span><span>{s.label}</span>{s.state === "running" && s.detail && <span className="small steel"> · {s.detail}</span>}</span>
            <span className="pl-step-word">{word[s.state]}</span></li>)}
        </ol>
        {!live && <div className="pl-stopped" role="alert"><span>{auto.message || "The plan stopped before it was finished."} What was found is kept.</span>
          <button type="button" className="btn-secondary" disabled={busy || !v.beachhead} onClick={onRetry}>Try again</button></div>}
        {live && (auto.feed?.length ?? 0) > 0 && <ul className="plain pl-feed" aria-live="polite" aria-label="What Landfall is doing now">
          {auto.feed!.map((f, i) => <li key={f.at + f.text} className={`small ${i ? "steel" : ""}`}>{f.text}</li>)}</ul>}
        {live && <p className="small steel">Landfall reads public pages and asks Qloo; nobody is contacted. You can leave this page; it keeps going.</p>}
      </section>
      {found.length > 0 && <section className="pl-steps" aria-label="What we've found so far">
        <span className="label steel">What we've found so far</span>
        <ul className="plain">{found.map((m) => <li key={m.method} className="small"><b>{m.label}</b>: {foundNameOf(m) ?? "investigated"}
          {m.investigation.found && m.investigation.found.kept.length > 0 ? ` · found ${m.investigation.found.kept.join(", ")}` : " · nothing that fits yet"}</li>)}</ul>
      </section>}
    </div>
  );
}
