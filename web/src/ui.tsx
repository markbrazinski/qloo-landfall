import { type ReactNode, useEffect, useRef, useState } from "react";

// ---- LF-StatusChip: knowledge state as text first, colour second (COMPONENT-CATALOG §9) ----
const CATEGORY: Record<string, string[]> = {
  sourced: ["SOURCE", "SUPPORTED", "STATED", "LISTED RATE", "PRESENCE VERIFIED", "DONE", "YOUR SITE",
    "NAME MATCHES YOUR SITE", "WEBSITE MATCHES", "LINKED FROM YOUR SITE", "ON YOUR HOMEPAGE", "SAME COUNTRY", "INSIDE YOUR RANGE", "NO IMPORT STEP (EU)",
    "SAME TRADE BLOC", "SHARED LANGUAGE", "HOME", "HOME COUNTRY"],
  founder: ["YOU SAID", "ADDED BY YOU", "NEEDS YOU", "CHECKED BY YOU", "DEFAULT", "DESCRIPTION", "CONFIRMED", "YOU CHANGED",
    "YOU LEFT THIS OUT", "YOU'RE ALREADY HERE", "MOVED FOR YOUR GOAL"],
  qloo: ["QLOO", "QLOO · CATEGORY", "COMPARABLE · NOT YOU", "CANDIDATE", "RETURNED", "QLOO STORES", "QLOO KEY MARKET"],
  interpretation: ["TEAM READ", "DETERMINISTIC", "MIXED", "INFERRED", "ESTIMATE", "ASSUMED", "DRAFT", "CALC", "MODERATE · NOT YET CHECKED",
    "LIMITED · NOT YET CHECKED"],
  unknown: ["UNKNOWN", "NOT KNOWN", "TO CHECK", "NOT CHECKED", "NOT READ", "BASIS NOT ESTABLISHED", "NOT CONFIRMED",
    "UNRESOLVED", "COULDN'T RESOLVE", "THIN SEARCH RESULT", "FIT NOT KNOWN", "IMPORT NEEDED", "COULD CHANGE IT",
    "EXPLORE BY ANOTHER ROUTE", "INSUFFICIENT EVIDENCE", "NOT RANKED BY QLOO", "IMPORT, NEW LANGUAGE"],
  contested: ["SOURCES DIFFER", "AMBIGUOUS", "UNCONFIRMED", "REVIEW · FACT CHANGED", "DIFFERENT WEBSITE"],
  failed: ["FAILED", "CHECK FAILED", "UNAVAILABLE", "SITE UNREADABLE", "DROPPED BY CHECKS"],
  running: ["RUNNING", "CHECKING", "READING", "SEARCHING"],
  idle: ["QUEUED", "SKIPPED", "SUPERSEDED", "STOPPED", "NO MATCH", "EMPTY", "STRONG ALTERNATIVE", "ALSO WORTH RESEARCHING",
    "NOT PRIORITISED", "NOT PROPOSED", "OUTSIDE YOUR GOAL"],
  proposal: ["DEMONSTRATION", "RECORDED", "SYNTHETIC", "PROPOSED", "RESEARCH PRIORITY", "INVESTIGATE FIRST", "TOP RECOMMENDATION",
    "RECOMMENDED"],
};
const categoryOf = (label: string) =>
  Object.entries(CATEGORY).find(([, ls]) => ls.includes(label.toUpperCase()))?.[0] ?? "idle";

export function Chip({ label, onClick, title }: { label: string; onClick?: () => void; title?: string }) {
  const cls = `chip chip-${categoryOf(label)}`;
  if (onClick)
    return (
      <button type="button" className={`${cls} chip-link`} onClick={onClick} title={title}>
        {label} ↗
      </button>
    );
  return <span className={cls} title={title}>{label}</span>;
}

/** LF-EvidenceLink: sourced claims read "View source ↗"; other states read their label + ↗. */
export function EvidenceLink({ state, onOpen }: { state: string; onOpen: () => void }) {
  if (state === "SUPPORTED")
    return (
      <button type="button" className="chip chip-sourced chip-link" onClick={onOpen}>
        View source ↗
      </button>
    );
  return <Chip label={state.replace("_", " ")} onClick={onOpen} />;
}

export function Lockup() {
  return (
    <a href="/" className="lockup" aria-label="Landfall home">
      <img src="/lockup.svg" alt="landfall" height={28} />
    </a>
  );
}

// ---- a tiny router: pushState plus a popstate the app listens to ----
export function navigate(to: string) {
  window.history.pushState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

export function Link({ to, className, children }: { to: string; className?: string; children: ReactNode }) {
  return <a href={to} className={className} onClick={(e) => { e.preventDefault(); navigate(to); }}>{children}</a>;
}

// ---- LF-AppShell / LF-ProjectHeader / LF-StageNav ----
export type Stage = { label: string; outcome: string; href?: string };
const LATER = ["Opportunities", "Decide", "Pursue"];

export function ProjectHeader(props: {
  brand: string; site: string; intent: string; briefVersion?: number; running: boolean; usage: string; demo: boolean;
  onBrief?: () => void; onActivity: () => void;
}) {
  return (
    <header className="project-header">
      <div className="ph-left">
        <Lockup />
        <span className="ph-slash" aria-hidden>/</span>
        <div>
          <div className="ph-brand">
            {props.brand} {props.demo && <Chip label="DEMONSTRATION" title="Recorded provider responses replayed; no live calls" />}
          </div>
          <div className="mono steel">{props.site} · {props.intent}</div>
        </div>
      </div>
      <div className="ph-actions">
        {props.briefVersion && (
          <button type="button" className="btn-secondary" onClick={props.onBrief}>
            Brief <span className="mono steel">v{props.briefVersion}</span>
          </button>
        )}
        <button type="button" className="btn-secondary" onClick={props.onActivity}>
          <span className={`dot ${props.running ? "dot-running" : ""}`} aria-hidden /> Activity{" "}
          <span className="mono steel">{props.usage}</span>
        </button>
      </div>
    </header>
  );
}

/** Brand and Markets link to each other once Markets is open; later stages arrive in later builds. */
export function StageNav({ stages, current }: { stages: Stage[]; current: number }) {
  const [open, setOpen] = useState(false);
  const items: Stage[] = [...stages, ...LATER.map((label) => ({ label, outcome: "Not started" }))];
  return (
    <nav className="stage-nav" aria-label="Stages">
      <button type="button" className="stage-compact" aria-expanded={open} onClick={() => setOpen(!open)}>
        {current + 1} / 5 · {items[current].label} <span className="steel">{items[current].outcome}</span> ▾
      </button>
      <ol className={open ? "open" : ""}>
        {items.map((st, i) => {
          const body = (
            <>
              <span className="stage-num">{i < current ? "✓" : i + 1}</span>
              <span className="stage-text">
                <span className="stage-label">{st.label}</span>
                <span className="stage-outcome">{st.outcome}</span>
              </span>
            </>
          );
          return (
            <li key={st.label} className={i === current ? "current" : st.href ? "done" : "disabled"}
                aria-current={i === current ? "step" : undefined} title={st.href || i === current ? undefined : "Arrives in a later build"}>
              {st.href && i !== current ? <Link to={st.href} className="stage-link">{body}</Link> : body}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// ---- LF-DecisionBar ----
export function DecisionBar(props: {
  kicker: string; title: string; subtitle?: ReactNode; primary?: { label: string; onClick?: () => void; disabledReason?: string };
  secondary?: ReactNode;
}) {
  const p = props.primary;
  return (
    <section className="decision-bar">
      <div>
        <div className="kicker">{props.kicker}</div>
        <h1>{props.title}</h1>
        {props.subtitle && <p className="subtitle">{props.subtitle}</p>}
      </div>
      <div className="db-actions">
        {props.secondary}
        {p && (
          <div className="primary-wrap">
            <button type="button" className={p.disabledReason ? "btn-disabled" : "btn-primary"} onClick={p.onClick}
                    disabled={!!p.disabledReason} aria-describedby={p.disabledReason ? "primary-reason" : undefined}>
              {p.label}
            </button>
            {p.disabledReason && <div id="primary-reason" className="reason">{p.disabledReason}</div>}
          </div>
        )}
      </div>
    </section>
  );
}

// ---- LF-DrawerStack: push adds a back link; Escape closes the top; focus returns to the opener ----
export type DrawerEntry = { key: string; kicker: string; render: () => ReactNode };

export function useDrawers() {
  const [stack, setStack] = useState<{ entry: DrawerEntry; opener: HTMLElement | null }[]>([]);
  const push = (entry: DrawerEntry) =>
    setStack((s) => [...s, { entry, opener: document.activeElement as HTMLElement | null }]);
  const pop = () =>
    setStack((s) => {
      const top = s[s.length - 1];
      setTimeout(() => top?.opener?.focus(), 0);
      return s.slice(0, -1);
    });
  const close = () =>
    setStack((s) => {
      setTimeout(() => s[0]?.opener?.focus(), 0);
      return [];
    });
  return { stack, push, pop, close };
}

export function DrawerStack({ d }: { d: ReturnType<typeof useDrawers> }) {
  const panel = useRef<HTMLDivElement>(null);
  const top = d.stack[d.stack.length - 1];
  useEffect(() => {
    if (!top) return;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && d.pop();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [top?.entry.key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!top) return null;
  const wide = top.entry.kicker === "BRIEF";
  return (
    <div className="drawer-layer">
      <div className="drawer-scrim" onClick={d.close} aria-hidden />
      <div className={`drawer ${wide ? "drawer-wide" : ""}`} role="dialog" aria-modal="true" aria-label={top.entry.kicker}
           tabIndex={-1} ref={panel}>
        <div className="drawer-head">
          {d.stack.length > 1 ? (
            <button type="button" className="link" onClick={d.pop}>← Back</button>
          ) : <span />}
          <span className="drawer-kicker">{top.entry.kicker}</span>
          <button type="button" className="btn-secondary" onClick={d.close}>Close</button>
        </div>
        <div className="drawer-body">{top.entry.render()}</div>
      </div>
    </div>
  );
}

export function Notice({ kind, children }: { kind: "you-changed" | "changed" | "info"; children: ReactNode }) {
  return (
    <div className={`notice notice-${kind}`} role="status">
      <span className="chip chip-founder">{kind === "you-changed" ? "YOU CHANGED" : kind === "changed" ? "CHANGED" : "NOTE"}</span>
      <span>{children}</span>
    </div>
  );
}

export function Skeleton({ width = "100%" }: { width?: string }) {
  return <span className="skeleton" style={{ width }} aria-hidden />;
}
