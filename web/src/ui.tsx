import { type ReactNode, useEffect, useRef, useState } from "react";

// ---- LF-StatusChip: knowledge state as text first, color second (COMPONENT-CATALOG §9) ----
const CATEGORY: Record<string, string[]> = {
  sourced: ["FOUND NOW", "SENT", "CREATED · PAUSED", "ACCOUNT", "EVIDENCED", "DEVELOPED", "BRAND FACT MATCH", "VERIFIED", "LOCAL", "OWN SITE", "PUBLIC FORM", "PLACED", "KEPT", "HIRE OFFERED", "HOSTED EVENT", "MARKET LISTING", "UPCOMING", "RECURRING", "IDENTIFIED", "REPORTED", "SELF REPORTED", "PLATFORM OPTION", "SOURCED", "MATCH", "EVIDENCED DRAFT", "PUBLISHED FORM", "EMAIL", "SOURCE", "SUPPORTED", "STATED", "LISTED RATE", "PRESENCE VERIFIED", "DONE", "YOUR SITE",
    "NAME MATCHES YOUR SITE", "WEBSITE MATCHES", "LINKED FROM YOUR SITE", "ON YOUR HOMEPAGE", "SAME COUNTRY", "INSIDE YOUR RANGE", "NO IMPORT STEP (EU)",
    "SAME TRADE BLOC", "SHARED LANGUAGE", "HOME", "HOME COUNTRY"],
  founder: ["YOUR BEACHHEAD", "SELECTED BY YOU", "APPROVED", "READY TO APPROVE", "MARKED SENT BY YOU", "MARKED CREATED BY YOU", "FOUNDER", "YOU CHOSE", "DRAFTED", "FOUNDER EXCLUDED", "YOU SAID", "ADDED BY YOU", "NEEDS YOU", "CHECKED BY YOU", "DEFAULT", "DESCRIPTION", "CONFIRMED", "YOU CHANGED",
    "YOU LEFT THIS OUT", "YOU'RE ALREADY HERE", "MOVED FOR YOUR GOAL"],
  qloo: ["QLOO RELATED", "QLOO LISTING", "QLOO DERIVED", "QLOO SEARCH", "PLATFORM DECLARED", "QLOO", "QLOO · CATEGORY", "COMPARABLE · NOT YOU", "CANDIDATE", "RETURNED", "QLOO STORES", "QLOO KEY MARKET"],
  interpretation: ["DERIVED", "HYPOTHESIS-LED", "HYPOTHESIS-LED DRAFT", "INTERPRETATION", "REPEATED BEFORE", "PARTIAL", "HYPOTHESIS", "PROPOSAL", "SCALE MISMATCH", "TEAM READ", "DETERMINISTIC", "MIXED", "INFERRED", "ESTIMATE", "ASSUMED", "DRAFT", "CALC", "MODERATE · NOT YET CHECKED",
    "LIMITED · NOT YET CHECKED"],
  unknown: ["NEEDS INFORMATION", "CONNECT", "VERIFICATION PENDING", "RIGHTS TO CHECK", "NOT YET", "NEEDS RESEARCH", "PENDING", "UNPLACED", "UNDATED", "POSSIBLE", "WILLINGNESS UNKNOWN", "TO CHECK IN ADS MANAGER", "NONE", "UNKNOWN", "NOT KNOWN", "TO CHECK", "NOT CHECKED", "NOT READ", "BASIS NOT ESTABLISHED", "NOT CONFIRMED",
    "UNRESOLVED", "COULDN'T RESOLVE", "THIN SEARCH RESULT", "FIT NOT KNOWN", "IMPORT NEEDED", "COULD CHANGE IT",
    "EXPLORE BY ANOTHER ROUTE", "INSUFFICIENT EVIDENCE", "NOT RANKED BY QLOO", "IMPORT, NEW LANGUAGE"],
  contested: ["EARLIER PLAN", "SET ASIDE", "USED", "UNCERTAIN", "OUTDATED", "NOT CONFIRMED PAUSED", "SOURCES DIFFER", "AMBIGUOUS", "UNCONFIRMED", "REVIEW · FACT CHANGED", "DIFFERENT WEBSITE"],
  failed: ["NOT CONNECTED", "FAILED", "CHECK FAILED", "UNAVAILABLE", "SITE UNREADABLE", "DROPPED BY CHECKS"],
  running: ["RUNNING", "CHECKING", "READING", "SEARCHING"],
  idle: ["UNAVAILABLE HERE", "PLATFORM APPROVAL REQUIRED", "FIXED", "REJECTED", "PAST", "ONE OFF", "CHAIN", "ORGANISER", "VENUE", "BOTH", "CATEGORY MISMATCH", "NOT RUN", "QUEUED", "SKIPPED", "SUPERSEDED", "STOPPED", "NO MATCH", "EMPTY", "STRONG ALTERNATIVE", "ALSO WORTH RESEARCHING",
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
/** One of the four v4 stages. `locked` says why it can't be opened yet (SH1). */
export type Stage = { label: string; outcome: string; href?: string; locked?: string };

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
        </div>
      </div>
      <div className="ph-actions">
        <button type="button" className="btn-secondary" onClick={props.onActivity}>
          <span className={`dot ${props.running ? "dot-running" : ""}`} aria-hidden /> Activity
        </button>
      </div>
    </header>
  );
}

/** Brand · Markets · Plan · Act. A locked stage explains itself when clicked; `current` -1 marks a page outside the four. */
export function StageNav({ stages, current }: { stages: Stage[]; current: number }) {
  const [why, setWhy] = useState<string | null>(null);
  return (
    <nav className="stage-nav" aria-label="Stages">
      <ol>
        {stages.map((st, i) => {
          const body = (
            <>
              <span className="stage-num">{i < current && st.href ? "✓" : i + 1}</span>
              <span className="stage-text">
                <span className="stage-label">{st.label}</span>
                <span className="stage-outcome">{st.outcome}</span>
              </span>
            </>
          );
          return (
            <li key={st.label} className={i === current ? "current" : st.href ? "done" : "disabled"} aria-current={i === current ? "step" : undefined}>
              {i === current ? body : st.href ? <Link to={st.href} className="stage-link">{body}</Link>
                : <button type="button" className="stage-link stage-locked" onClick={() => setWhy(st.locked || "Not available yet")}>{body}</button>}
            </li>
          );
        })}
      </ol>
      {why && <p className="stage-why" role="status">{why} <button type="button" className="link" onClick={() => setWhy(null)}>Dismiss</button></p>}
    </nav>
  );
}

// ---- LF-DecisionBar ----
export function DecisionBar(props: {
  kicker: string; title: string; subtitle?: ReactNode; primary?: { label: string; onClick?: () => void; disabledReason?: string; busy?: boolean };
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
                    disabled={!!p.disabledReason || p.busy} aria-busy={p.busy || undefined} aria-describedby={p.disabledReason ? "primary-reason" : undefined}>
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

/** Tab and Shift+Tab cycle inside `box` (drawers and dialogs). */
export function trapTab(e: KeyboardEvent, box: HTMLElement | null) {
  if (e.key !== "Tab" || !box) return;
  const f = [...box.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex='-1'])")]
    .filter((el) => el.getClientRects().length > 0);  // hidden controls (the 390 px <select>) can't take focus
  if (!f.length) return;
  const [first, last] = [f[0], f[f.length - 1]];
  // focus that fell out (its control unmounted, e.g. a chip row that closed on choice) comes back in, never to the page
  if (!box.contains(document.activeElement)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); return; }
  if (e.shiftKey && (document.activeElement === first || document.activeElement === box)) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

export function DrawerStack({ d }: { d: ReturnType<typeof useDrawers> }) {
  const panel = useRef<HTMLDivElement>(null);
  const top = d.stack[d.stack.length - 1];
  useEffect(() => {
    if (!top) return;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") d.pop();
      trapTab(e, panel.current);  // SH4: focus stays inside the open drawer
    };
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
