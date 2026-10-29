import "./act.css";
import { type ReactNode, useEffect, useState } from "react";
import { type ActItem, type Draft, type DestinationState, type DestinationV4, type Package, type Segment, type Template,
  api6, api65, majorText, sharedOf, useLive, useProject } from "./api";
import { ActivityDrawer, stagesFor, usageLine } from "./Brand";
import { ContextEdit, ContextStrip, goalLine } from "./Goal";
import { CampaignMockup } from "./mockups/CampaignMockup";
import { Chip, DrawerStack, Link, Notice, ProjectHeader, Skeleton, StageNav, navigate, useDrawers } from "./ui";
import { noCaption } from "./api";

type Act = (fn: () => Promise<unknown>, done?: string) => void;
type Drawers = ReturnType<typeof useDrawers>;
const L = (s: string | null | undefined) => (s || "").replace(/_/g, " ");
// an approval for your own use is offered only where direct delivery isn't the next step, never beside an open outcome
const OWN_USE = ["UNAVAILABLE_IN_THIS_DEPLOYMENT", "PLATFORM_APPROVAL_REQUIRED", "PLATFORM_VERIFICATION_PENDING", "CONNECT_YOUR_ACCOUNT"];
const SEG: Record<string, string> = { SUPPORTED: "SUPPORTED", PLAIN: "PROPOSAL", TO_CHECK: "TO CHECK", YOU_SAID: "YOU SAID", UNCHECKED: "NOT CHECKED" };
const amount = majorText;
const cap1 = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const when = (at: string) => new Date(at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const day = (d: string) => new Date(`${d.slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
const size = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

// ---- LF-CapabilityPill and the one reason sentence (v4 fixtures `reasons`), shared with Act & adapt ----
export type Cap = "connected" | "connect" | "verif" | "approval" | "unavailable";
const CAPS: Record<Cap, [string, string]> = { connected: ["●", "Connected"], connect: ["○", "Connect account"],
  verif: ["◔", "Platform verification pending"], approval: ["◌", "Platform approval required"], unavailable: ["⊘", "Direct delivery unavailable"] };

export function capOf(d: DestinationState): Cap {
  const s = d.state;
  if (sharedOf(d)?.available === false) return "unavailable";  // G-J: exports are the next step, never Reconnect
  if (s.connection === "ACTIVE" || s.connection === "SIMULATED") return "connected";
  if (s.deployment !== "CONFIGURED" && !s.simulated) return "unavailable";
  if (s.connection === "NOT_AVAILABLE") return s.platform_access === "PENDING" ? "verif" : "approval";
  return "connect";
}

export function Pill({ d }: { d: DestinationState }) {
  const c = capOf(d);
  return <span className={`lf-pill${c === "connected" ? " lf-pill-connected" : ""}`}><i aria-hidden>{CAPS[c][0]}</i>{CAPS[c][1]}</span>;
}

export const partnerOf = (v: Package) => v.partner?.name || "your partner";
const OWNER: Record<string, string> = { EMAIL: "Google", META_ADS: "Meta", GOOGLE_ADS: "Google", TIKTOK_ADS: "TikTok" };

export function reasonOf(d: DestinationState, partner: string): string {
  const c = capOf(d);
  const sh = sharedOf(d);
  if (sh && !sh.available) return sh.why || d.text;
  if (d.destination === "EMAIL") return {
    connected: d.routing ? `Sends a test to the private test inbox after you approve it. ${cap1(partner)} won’t be contacted.`
      : `Sends from ${d.state.account || "your Gmail"} after you approve each message.`,
    connect: "Connect your Gmail to send messages you approve from your own address.",
    verif: "Google is reviewing Landfall’s Gmail access. Until then, take messages to your own mail.",
    approval: "Google hasn’t approved Landfall to send from Gmail yet, so it can’t go out automatically today. Use the prepared message yourself.",
    unavailable: "Gmail sending isn’t available in this deployment. Copy, download or open messages in your mail app.",
  }[c];
  const o = OWNER[d.destination];
  return {
    connected: `Creates the campaign ${d.destination === "TIKTOK_ADS" ? "in a non-delivering state" : "paused"} in ${d.state.account || "your account"}. You turn it on in ${d.where}.`,
    connect: `Connect your ${o} ad account to create this campaign paused.`,
    verif: `${o} is reviewing Landfall’s access. Download the package and set it up yourself.`,
    approval: `${o} hasn’t approved Landfall to create ${d.destination === "GOOGLE_ADS" ? "Google Ads" : "ads"} yet, so it can’t be set up automatically today. Use the prepared package yourself.`,
    unavailable: `${d.label} creation isn’t available in this deployment. Download the package and set it up in ${d.where}.`,
  }[c];
}

/** What happened at this destination, in v4's words; null while nothing has. */
export function outcomeOf(d: DestinationState, partner: string): string | null {
  const last = d.actions[d.actions.length - 1];
  const mine = d.manual_records.filter((r) => !(r as { withdrawn_at?: string }).withdrawn_at).pop();
  const email = d.destination === "EMAIL";
  if (d.summary === "TEST_SENT") return `Test email sent · ${partner} not contacted`;
  if (d.manual && mine) return `${email ? "Marked sent by you" : "Set up by you"} · ${day(mine.date)}`;
  if (last?.mode === "SIMULATED" && ["SENT", "CREATED_PAUSED"].includes(d.summary))  // the recorded demo: nothing went anywhere
    return `Simulated · nothing was ${email ? "sent" : "created"} (recorded demo)`;
  return ({
    EXECUTING: email ? "Sending…" : "Creating…", SENT: `Gmail accepted it · ${last ? when(last.at) : ""}`,
    CREATED_PAUSED: `Created, paused · ${last ? when(last.at) : ""}`, PARTLY_CREATED: "Partly created",
    UNCERTAIN: email ? "Not sure Gmail accepted it" : "Not sure it was created", FAILED_VERIFICATION: "Created, not confirmed paused",
    FAILED: "It didn’t go through",
  } as Record<string, string>)[d.summary] ?? null;
}

const COUNTS: Record<string, string> = { META_ADS: "1 campaign, 1 ad set and 1 ad", TIKTOK_ADS: "1 campaign, 1 ad group and 1 ad",
  GOOGLE_ADS: "1 campaign, 1 ad group and 1 search ad" };
const OBJ: Record<string, string> = { campaign: "campaign", adset: "ad set", adgroup: "ad group", ad: "ad", image: "image", creative: "creative", identity: "identity" };

/** v4 12 → 13: /p/{id}/deliver/{package}, inside the app shell (Act & adapt is stage 5). */
export default function DeliverPage({ id, pkg }: { id: string; pkg: string }) {
  const { project: p, reload: reloadP } = useProject(id);
  const { data: v, error, reload } = useLive(id, (pid) => api6.package(pid, pkg));
  const drawers = useDrawers();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [siblings, setSiblings] = useState<ActItem[]>([]);
  const [connectError] = useState(() => new URLSearchParams(window.location.search).get("connect_error"));
  const [dst, setDst] = useState(() => new URLSearchParams(window.location.search).get("dst"));
  const signedHere = !!(p && v?.version && p.beachhead?.city_id === v.city_id && p.plan?.signed_off);
  useEffect(() => {  // the other messages of this method, for v4's message tabs
    if (!signedHere || !v?.city_id) return;
    api65.act(id, v.city_id).then((a) => setSiblings(a.items.filter((i) => i.kind === "DELIVER" && i.method === v.method && i.destination === "EMAIL")))
      .catch(() => setSiblings([]));
  }, [id, signedHere, v?.city_id, v?.method, v?.version]);
  const act: Act = async (fn, done) => {
    setBusy(true); setErr(null); setNote(null);
    try { await fn(); await reload(); if (done) setNote(done); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  if (error && !v) return <div className="app"><main className="page"><div className="error-panel card">{error} <a className="link" href={`/p/${id}`}>Back</a></div></main></div>;
  if (!p || !v) return <div className="app"><main className="page"><Skeleton width="40%" /></main></div>;
  const shell = (body: ReactNode) => (
    <div className="app">
      <ProjectHeader brand={p.brief?.brand_name || p.site} site={p.site} intent={goalLine(p)} briefVersion={p.brief?.version}
        running={!!v.preparing} usage={usageLine(p)} demo={p.demo} onBrief={() => navigate(`/p/${id}`)}
        onActivity={() => drawers.push({ key: "act", kicker: "ACTIVITY", render: () => <ActivityDrawer p={p} /> })} />
      <StageNav stages={stagesFor(p)} current={3} />
      <ContextStrip p={p} onEdit={() => drawers.push({ key: "ctx", kicker: "YOUR GOAL", render: () => <ContextEdit p={p} reload={reloadP} /> })} />
      <main id="main"><div className="aa-wrap">{body}</div></main>
      <DrawerStack d={drawers} />
    </div>
  );
  if (!v.version) {
    const run = v.preparing || null;
    return shell(
      <section className="card stack" style={{ marginTop: 24 }} aria-busy={run ? run.status !== "failed" : undefined}>
        <div className="card-head"><h1 className="subheading">Preparing the delivery package</h1><Chip label={run && run.status === "failed" ? "FAILED" : "RUNNING"} /></div>
        {run && run.status === "failed" ? <p>It wasn’t prepared: {run.message || "the step failed"}. Nothing was changed.</p>
          : <><Skeleton width="70%" /><Skeleton width="55%" /><p className="small steel">Writing the copy, checking every sentence against its evidence, and saving your own product images.</p></>}
      </section>);
  }
  const back = signedHere ? `/p/${id}/launch/${v.city_id}` : `/p/${id}/opportunities/${v.city_id}/${v.method}`;
  const save = (fields: object, done = "Saved as a new version. Earlier approvals of this package are now outdated.") =>
    act(() => api6.edit(id, v.id, v.version, fields), done);
  const d = (v.destination_states.find((x) => x.destination === dst) || v.destination_states[0]) as DestinationV4;
  const partner = partnerOf(v);
  const title = v.flow === "outreach" ? `Inquiry to ${partner}` : `Local ads · ${d.label}`;
  const ctx = { id, v, d, act, busy, save, partner, drawers };
  return shell(<>
    <div className="aa-review-head">
      <button type="button" className="link" style={{ justifySelf: "start" }} onClick={() => navigate(back)}>← Back</button>
      <h1>{title}</h1>
      <Pill d={d} />
    </div>
    <div className="stack" style={{ gap: 10, marginBottom: 12 }}>
      {err && <p className="field-error" role="alert">{err}</p>}
      {connectError && <div className="notice notice-failed" role="alert"><Chip label="NOT CONNECTED" /><span>{connectError}</span></div>}
      {note && <Notice kind="you-changed">{note}</Notice>}
      {v.fence && <div className="notice notice-failed" role="status"><Chip label="EARLIER PLAN" /><span>{v.fence}{v.plan_ref ? ` It was prepared from plan v${v.plan_ref.version}.` : ""} It can’t be approved, validated or sent; its history, exports and any unresolved send stay.</span></div>}
      {v.status === "set_aside" && <div className="notice notice-changed" role="status"><Chip label="SET ASIDE" /><span>You set this recipient aside. Its history stays; choose them again in Prepare to continue.</span></div>}
      {v.stale.map((x) => <div key={x} className="notice notice-changed" role="status"><Chip label="CHANGED" /><span>{x}. Prepare again to use it.</span></div>)}
      <EarlierWording id={id} v={v} act={act} busy={busy} />
    </div>
    {v.flow === "outreach" ? <EmailScreen {...ctx} siblings={siblings} />
      : <AdScreen {...ctx} tabs={v.destination_states.length > 1 ? v.destination_states : []} onTab={(x) => setDst(x)} />}
    <div className="aa-more">
      <Sources v={v} />
      {v.destination_states.map((x) => <Details key={x.destination} id={id} v={v} d={x} act={act} busy={busy} />)}
    </div>
  </>);
}

type Ctx = { id: string; v: Package; d: DestinationV4; act: Act; busy: boolean; save: (f: object, done?: string) => void; partner: string; drawers: Drawers };

const tab = (s: ActItem) => <><b>Inquiry to {s.partner || "your partner"}</b><span>{TAB_WORD[s.status] || cap1(L(s.status).toLowerCase())}</span></>;
const TAB_WORD: Record<string, string> = { PREPARED: "Drafted for your review", NEEDS_INFORMATION: "Needs information", APPROVED: "Approved",
  EXPORTED: "Downloaded", UNCERTAIN: "Not sure Gmail accepted it", SENT: "Gmail accepted it", TEST_SENT: "Test email sent",
  MARKED_SENT_BY_YOU: "Marked sent by you", FAILED: "Didn’t go through", FAILED_VERIFICATION: "Not confirmed" };

/** v4 13 / B10 / B11 / B14 / B18: the exact message first, then what you can do with it. */
function EmailScreen({ siblings, ...c }: Ctx & { siblings: ActItem[] }) {
  const { id, v, d, save, busy, partner } = c;
  const [editing, setEditing] = useState(false);
  const [toDraft, setToDraft] = useState("");
  const cap = capOf(d);
  const attach = v.assets.filter((a) => a.role === "ATTACHMENT");
  const by = v.recipients_by?.to;
  const flagged = (v.copy.segments || []).concat(v.copy.subject ? [v.copy.subject] : []).filter((s) => s.state === "TO_CHECK");
  const needs = d.state.missing.filter((m) => m.field !== "to" && !flagged.some((f) => f.field === m.field));  // a flagged sentence is listed once, whole
  return (
    <div className="aa-review">
      <div className="aa-col">
        {siblings.length > 1 && <nav className="aa-tabs" aria-label="Messages">{siblings.map((s) => (
          s.package!.id === v.id ? <span key={s.package!.id} className="aa-tab" aria-current="page">{tab(s)}</span>
            : <Link key={s.package!.id} className="aa-tab" to={`/p/${id}/deliver/${s.package!.id}`}>{tab(s)}</Link>))}</nav>}
        <section className="aa-msg" aria-label="Message">
          {cap !== "connected" && <div className="aa-greynote">{reasonOf(d, partner)}</div>}
          {editing ? <div className="stack">
            <EmailReview key={`e${v.version}`} v={v} save={save} busy={busy} />
            <Templates key={`t${v.version}`} id={id} v={v} act={c.act} busy={busy} />
            <Products key={`p${v.version}`} id={id} v={v} save={save} busy={busy} />
            <Creative key={`c${v.version}`} id={id} v={v} save={save} act={c.act} busy={busy} />
          </div> : <>
            <dl className="aa-rows">
              <dt>From</dt><dd>{cap === "connected" && d.state.account ? `${d.state.account} · Gmail` : v.from_name ? `${v.from_name} · your own mail account` : "Your own mail account"}</dd>
              <dt>To</dt><dd>{v.recipients.to.length ? <>{v.recipients.to.join(", ")}{by && <span className="aa-mono-note"> · {by === "SOURCE" ? "on their own site" : by === "YOU_SAID" ? "you said" : L(by).toLowerCase()}</span>}</>
                : <form className="aa-inline" onSubmit={(e) => { e.preventDefault(); if (toDraft.trim()) save({ to: [toDraft.trim()] }, "Added. Saved as a new version."); }}>
                  <span className="aa-miss">△ No email address found</span>
                  <input className="input" type="email" value={toDraft} onChange={(e) => setToDraft(e.target.value)} placeholder="name@example.com" aria-label="Recipient" />
                  <button type="submit" className="btn-secondary" disabled={busy || !toDraft.trim()}>Add · YOU SAID</button></form>}</dd>
              <dt>Cc</dt><dd>{v.recipients.cc.join(", ") || "None"}</dd>
              <dt>Bcc</dt><dd>{v.recipients.bcc.join(", ") || "None"}</dd>
              <dt>Subject</dt><dd><b>{v.email?.subject}</b></dd>
              <dt>Attachment</dt><dd>{attach.map((a) => `${a.filename} · ${size(a.size)}`).join(", ") || "None"}</dd>
            </dl>
            <div className="aa-body">{v.email?.body}</div>
            <div className="aa-checks" aria-label="Before you send">
              <span className="aa-kicker">Before you send</span>
              {by === "SOURCE" ? <span>✓ The address is on their own site</span> : by === "YOU_SAID" ? <span>✓ You added the address</span>
                : !v.recipients.to.length ? <span className="aa-warn">△ No email address yet: add one you have</span> : null}
              {flagged.length === 0 ? <span>✓ No claims beyond the evidence</span> : flagged.map((s) => (
                <span key={s.field} className="aa-warn">⚠ {s.why || "Not backed by its evidence"}: “{s.text}” <Keep s={s} keep={(f) => save({ confirm: [f] })} label={s.field === "subject" ? "the subject" : "this sentence"} /></span>))}
              {needs.map((m) => <span key={m.field + m.text} className="aa-warn">△ {m.text}</span>)}
              {v.info.map((m) => <span key={m.field} className="aa-muted">{m.text}</span>)}
            </div>
          </>}
          <div><button type="button" className="btn-secondary" onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit"}</button></div>
        </section>
      </div>
      <div className="aa-col">
        <article className="aa-col" aria-label={d.label}>
          <TestNotice d={d} />
          <Result {...c} />
        </article>
        <section className="aa-use" aria-label="Use it yourself">
          <span className="aa-kicker">Use it yourself</span>
          <CopyButton label="Copy message" text={() => `Subject: ${v.email?.subject}\n\n${v.email?.body}`} />
          <a className="btn-secondary" href={api6.exportUrl(id, v.id, "eml", v.version)} download>Download .eml draft</a>
          {v.email && <a className="btn-secondary" href={v.email.mailto}>Open in your mail app</a>}
          {attach.length === 1 ? <a className="btn-secondary" href={api6.exportUrl(id, v.id, "asset", v.version, attach[0].artifact.id)} download>Download attachment</a>
            : attach.length > 1 && <a className="btn-secondary" href={api6.exportUrl(id, v.id, "outreach_zip", v.version)} download>Download attachments</a>}
          <span className="aa-sub">None of these send anything. Your mail app gets To, Subject and Body; attach files yourself.</span>
          <OwnUse {...c} />
        </section>
        {cap !== "connected" && !d.manual && <Manual {...c} />}
      </div>
    </div>
  );
}

/** 6.7 G3: test delivery, in its own words, above the actions. */
function TestNotice({ d }: { d: DestinationState }) {
  const rt = d.routing;
  if (!rt) return <SharedNote d={d} />;
  const who = cap1(rt.intended.name);
  return <>
    <SharedNote d={d} />
    {!d.test && <div className="notice notice-changed" role="note"><Chip label="TEST DELIVERY" /><span>This outreach is prepared
      for <strong>{rt.intended.name}</strong>. In this demonstration, sending submits it to Gmail for a private test inbox only.
      {" "}{who} will not be contacted.</span></div>}
    <details><summary className="small">What Gmail will receive</summary>
      <pre className="preview-text small">{`From: ${rt.sender_label}\nTo: ${rt.recipient_label}\nSubject: ${rt.receives.subject}\n\n${rt.receives.first_line}\n\n(the prepared message follows unchanged)`}</pre></details>
  </>;
}

/** G-J: explicit disclosure that a judge's send or create goes through Landfall's own test connection. */
function SharedNote({ d }: { d: DestinationState }) {
  const sh = sharedOf(d);
  return sh ? <p className="small" role="note">{sh.disclosure}</p> : null;
}

/** LF-ExecutionResult and the one approve-and-run action, in place of browser dialogs. */
function Result({ id, v, d, act, busy, partner }: Ctx) {
  const [reviewing, setReviewing] = useState(false);
  const [seen, setSeen] = useState("");
  const last = d.actions[d.actions.length - 1];
  const email = d.destination === "EMAIL";
  const rt = d.routing;
  const cap = capOf(d);
  const out = outcomeOf(d, partner);
  const mine = d.manual_records.filter((r) => !(r as { withdrawn_at?: string }).withdrawn_at).pop();
  const box = (open: boolean, head: string, body: ReactNode, actions?: ReactNode) => (
    <div className={`aa-result${open ? " aa-result-open" : ""}`} role="status"><b>{head}</b><span className="aa-line">{body}</span>
      {d.state.simulated && last?.mode === "SIMULATED" && <span className="aa-sub">Simulated: no provider was called.</span>}{actions}</div>);
  let result: ReactNode = null;
  if (d.manual && mine && d.summary !== "UNCERTAIN") {
    result = box(false, out!, email ? "From your own mail. Landfall didn’t send it." : `In ${d.where}. Landfall didn’t create it.`,
      <button type="button" className="btn-secondary" disabled={busy}
        onClick={() => act(() => api65.withdraw(id, v.id, d.destination, mine.id), "Undone. The earlier mark stays in the history.")}>Undo</button>);
  } else if (d.summary === "EXECUTING") {
    result = box(false, out!, `Waiting for ${email ? "Gmail" : d.label}. You can leave; the result is kept.`);
  } else if (d.summary === "SENT" || d.summary === "TEST_SENT") {
    result = box(false, out!, d.summary === "TEST_SENT" ? d.text : "Gmail took the message. That isn’t the same as read. Message and thread ids recorded.",
      last && <Ids a={last} />);
  } else if (d.summary === "CREATED_PAUSED" || d.summary === "FAILED_VERIFICATION") {
    result = box(d.summary !== "CREATED_PAUSED", d.summary === "CREATED_PAUSED" ? out! : "Created, but not confirmed paused", d.text, last && <Ids a={last} />);
  } else if (d.summary === "PARTLY_CREATED" && !reviewing) {
    result = box(true, "Partly created", d.text.replace(/^Partly created · /, ""),
      <button type="button" className="btn-secondary aa-btn-wide" onClick={() => setReviewing(true)}>Review the missing ads</button>);
  } else if (d.summary === "UNCERTAIN" && d.next.includes("resolve_happened") && last) {
    const test = d.test;
    result = box(true, test ? "Not sure Gmail accepted this test email" : email ? "Not sure Gmail accepted it" : "Not sure it was created",
      <>{test ? d.text : email ? "No answer from Gmail. Check your Sent folder first. Landfall won’t retry on its own."
        : `No answer from ${d.label}. Check ${d.where} before trying again. Landfall won’t retry on its own.`}
        {" "}<span className="aa-mono-note">Landfall reference {last.reference}</span></>,
      <>{!test && <input className="input" value={seen} onChange={(e) => setSeen(e.target.value)} aria-label="The id you saw (optional)" placeholder="The id you saw (optional)" />}
        <div className="btn-row">
          <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api6.resolve(id, last.id, true, test ? undefined : seen || undefined),
            test ? "Recorded: you confirmed the test email is in the private test inbox. Gmail’s answer stays unclear." : "Recorded by you. The provider result stays unknown.")}>
            {test ? "It’s in the test inbox" : email ? "It’s in Sent" : "I found it"}</button>
          <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api6.resolve(id, last.id, false), "Closed. Approve again to try once more.")}>
            {test ? "It wasn’t sent" : "It isn’t there"}</button>
        </div></>);
  } else if (d.summary === "UNCERTAIN") {
    result = box(true, out!, d.text);
  } else if (d.summary === "FAILED") {
    result = box(true, email ? "Gmail refused it" : `${d.label} refused it`, d.text);
  }
  // the one action: approve this exact version and run it (B10, B12), or what stands in its way
  const partly = d.partly && d.summary === "PARTLY_CREATED" ? d.partly : null;
  const runnable = d.next.includes("approve") || d.next.includes("execute");
  const showAction = cap === "connected" && !(partly && !reviewing) && !["SENT", "TEST_SENT", "CREATED_PAUSED", "EXECUTING", "FAILED_VERIFICATION", "UNCERTAIN"].includes(d.summary) && !d.manual;
  const label = email ? (rt ? "Send test email" : "Approve and send with Gmail")
    : partly ? `Approve and create the missing ${partly.missing.map((x) => OBJ[x] || x).join(" and ")}`
    : `Approve and create ${d.destination === "TIKTOK_ADS" ? "disabled" : "paused"} in ${d.label}`;
  const n = v.recipients.to.length + v.recipients.cc.length + v.recipients.bcc.length;
  const sub = email ? (rt ? `Approves this exact version and submits it to Gmail for the private test inbox only. ${cap1(partner)} won’t be contacted. Editing anything clears this approval.`
      : `Sends this exact message from ${d.state.account} to ${n} recipient${n === 1 ? "" : "s"}. Editing anything clears this approval.`)
    : partly ? `Creates only what’s missing (${partly.missing.map((x) => OBJ[x] || x).join(", ")}) in ${d.state.account}, ${d.destination === "TIKTOK_ADS" ? "disabled" : "paused"}; what was created before is reused, never made twice. Landfall can’t turn them on or spend.`
    : `Creates ${COUNTS[d.destination]} in ${d.state.account}, all ${d.destination === "TIKTOK_ADS" ? "disabled (not delivering)" : "paused"}. Landfall can’t turn them on or spend. Editing anything clears this approval.`;
  const run = () => act(async () => {
    let aid = d.next.includes("execute") ? d.state.approval_id : null;
    if (!aid) {
      const r = await api6.approve(id, v.id, d.destination, v.version);
      if (r.use !== "execute") throw new Error("Approved for your own use only: nothing was sent or created.");
      aid = r.approval_id;
    }
    await api6.execute(id, v.id, d.destination, aid);
    setReviewing(false);
  });
  const blockers = [...d.state.missing, ...d.plan.blockers.filter((b) => !d.state.missing.some((m) => m.text === b.text))];
  return <>
    {result}
    {showAction && (d.next.includes("choose_account") ? <AccountForm id={id} v={v} d={d} act={act} busy={busy} />
      : runnable ? <div className="aa-approve"><button type="button" className="btn-primary" disabled={busy} onClick={run}>{label}</button><span className="aa-sub">{sub}</span></div>
      : d.summary === "NEEDS_INFORMATION" && <div className="aa-approve"><button type="button" className="btn-disabled" disabled>{label}</button>
        <span className="aa-sub">{blockers.map((b) => b.text).join("; ")} first.</span></div>)}
    {d.next.includes("connect") && cap !== "connected" && !d.manual && !sharedOf(d) && <button type="button" className="btn-secondary aa-btn-wide" disabled={busy}
      onClick={() => act(async () => { const r = await api6.connect(id, d.destination, v.id); if (r.url) window.location.assign(r.url); })}>
      {d.state.connection === "EXPIRED" ? `Reconnect ${email ? "Gmail" : d.label}` : email ? "Connect Gmail to send from here" : `Connect ${d.label} to create it paused`}</button>}
  </>;
}

function OwnUse({ id, v, d, act, busy }: Ctx) {
  if (!(OWN_USE.includes(d.summary) && !d.next.includes("approve") && d.state.package === "VALID" && d.state.approval !== "APPROVED")) return null;
  return <button type="button" className="link" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={() => act(() => api6.approve(id, v.id, d.destination, v.version),
    `Approved v${v.version} for your own use.`)}>Approve v{v.version} for your own use</button>;
}

function Manual({ id, v, d, act, busy }: Ctx) {
  const email = d.destination === "EMAIL";
  if (["SENT", "TEST_SENT", "CREATED_PAUSED", "EXECUTING", "UNCERTAIN"].includes(d.summary)) return null;
  return (
    <section className="aa-manual" aria-label={email ? "Sent it yourself?" : "Set it up yourself?"}>
      <b>{email ? "Sent it yourself?" : "Set it up yourself?"}</b>
      <span className="aa-sub">{email ? "Record it so the plan knows it went out." : "Record it so the plan knows the ads exist."}</span>
      <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api6.manual(id, v.id, d.destination, new Date().toISOString().slice(0, 10)),
        "Recorded by you. Not confirmed by the provider.")}>{email ? "Mark as sent by me" : `I set this up in ${d.where}`}</button>
    </section>
  );
}

function CopyButton({ label, text }: { label: string; text: () => string | Promise<string> }) {
  const [done, setDone] = useState<string | null>(null);
  return <>
    <button type="button" className="btn-secondary" onClick={async () => {
      try { await navigator.clipboard.writeText(await text()); setDone("Copied."); } catch { setDone("Couldn’t copy here: use a download instead."); }
    }}>{label}</button>
    {done && <span className="aa-sub" role="status">{done}</span>}
  </>;
}

/** v4 B09 · B12 · B13: an ad package to review, set up yourself, or create paused. */
function AdScreen({ tabs, onTab, ...c }: Ctx & { tabs: DestinationState[]; onTab: (dst: string) => void }) {
  const { id, v, d, save, busy, partner, drawers } = c;
  const [editing, setEditing] = useState(false);
  const [landing, setLanding] = useState("");
  const cap = capOf(d);
  const cp = v.copy;
  const img = v.assets.find((a) => a.role === "CREATIVE" && a.readable);
  const heads = (cp.headlines || []).map((s) => s.text);
  const body = d.destination === "TIKTOK_ADS" ? cp.ad_text?.text : cp.primary_text?.text || cp.descriptions?.[0]?.text;
  const cta = v.creative.cta ? cap1(L(v.creative.cta).toLowerCase()) : null;
  const city = v.targeting.geo.name || v.city_id;
  const b = v.budget_view;
  const connecting = d.summary === "CONNECT_YOUR_ACCOUNT";
  const blocked = [...d.state.missing, ...d.plan.blockers.filter((x) => !d.state.missing.some((m) => m.text === x.text)
    && (connecting || !["account", "sender"].includes(x.field)))];
  const page = d.plan.values.find((x) => x.field === "Page")?.value as string | null | undefined;
  const status = d.destination === "TIKTOK_ADS" ? "Disabled (not delivering) · can’t be changed here" : "Paused · can’t be changed here";
  const csv = api6.exportUrl(id, v.id, "settings_csv", v.version);
  return <>
    {tabs.length > 0 && <div className="aa-seg" role="group" aria-label="Platforms" style={{ marginBottom: 12 }}>{tabs.map((t) => (
      <button key={t.destination} type="button" aria-pressed={t.destination === d.destination} onClick={() => onTab(t.destination)}>{t.label}</button>))}</div>}
    {cap !== "connected" && <div className="aa-greynote" style={{ maxWidth: 900, marginBottom: 16 }}>{reasonOf(d, partner)}</div>}
    <SharedNote d={d} />
    <div className="aa-review">
      <section className="aa-msg" aria-label="Ad package">
        {editing ? <div className="stack">
          <AdCopyReview key={`a${v.version}`} v={v} save={save} busy={busy} />
          <Creative key={`c${v.version}`} id={id} v={v} save={save} act={c.act} busy={busy} />
          <Audience key={`u${v.version}`} v={v} save={save} busy={busy} />
          <Budget key={`b${v.version}`} v={v} save={save} busy={busy} />
        </div> : <div>
          <div className="aa-ad-sec"><b>1 · Creative and copy</b>
            <div className="aa-previews">{(["ad1", "ad2", "ad3"] as const).map((k) => (
              <div key={k} className="aa-ad-frame" role="img" aria-label={`Illustrative ${d.label} story ad`}>
                <CampaignMockup kind={k} d={{ brand: v.creative.display_name || "", city, concept: v.concept, partner: null,
                  products: img ? [{ title: img.filename, src: api6.exportUrl(id, v.id, "asset", v.version, img.artifact.id, true) }] : [],
                  copy: { headlines: heads, primary_text: body || null, cta } }} /></div>))}</div>
            <dl className="aa-rows aa-rows-wide">
              <dt>Headlines</dt><dd>{heads.map((h) => `“${h}”`).join(" · ") || "None yet"}</dd>
              <dt>{d.destination === "TIKTOK_ADS" ? "Ad text" : d.destination === "GOOGLE_ADS" ? "Descriptions" : "Primary text"}</dt>
              <dd>{d.destination === "GOOGLE_ADS" ? (cp.descriptions || []).map((s) => s.text).join(" · ") || "None yet" : body || "None yet"} <span className="aa-muted">({cp.locale})</span></dd>
              {d.destination === "GOOGLE_ADS" && <><dt>Keywords</dt><dd>{(cp.keywords || []).map((k) => `${k.text} [${k.match_type.toLowerCase()}]`).join(" · ") || "None yet"}</dd></>}
              <dt>Button</dt><dd>{cta || "Not chosen"}</dd>
              <dt>Image</dt><dd>{img ? `${img.filename}${img.rights_confirmed ? " · rights confirmed by you" : " · rights to confirm"}` : "None saved yet"}</dd>
            </dl>
            <span className="aa-sub">Previews are illustrative, drawn from this copy and your saved image; the platform renders the real ad.</span>
          </div>
          <div className="aa-ad-sec"><b>2 · Audience rationale and proposed targeting</b>
            <span className="aa-tag"><Chip label="QLOO" title={v.audience_rationale.label} /><span>{v.audience_rationale.hypothesis}
              {v.audience_rationale.qloo_interests.length > 0 && ` Qloo affinity: ${v.audience_rationale.qloo_interests.join(", ")}.`} That describes taste, not audience size.</span></span>
            <dl className="aa-rows aa-rows-wide">
              <dt>Location</dt><dd>{v.targeting.geo.name}{v.targeting.geo.radius_km ? ` · +${Math.round((v.targeting.geo.radius_km / 1.609344) * 10) / 10} mi` : ""}</dd>
              <dt>Interests</dt><dd>{v.targeting.platform_interests.map((t) => t.name).join(" · ") || "None proposed"}</dd>
            </dl>
          </div>
          <div className="aa-ad-sec"><b>3 · Destination</b>
            {v.creative.landing_url ? <span className="aa-line">Landing page: {v.creative.landing_url} · {v.creative.landing_by === "YOU_SAID" ? "YOU SAID" : "your website"}</span>
              : <form className="aa-inline" onSubmit={(e) => { e.preventDefault(); if (landing.trim()) save({ landing_url: landing.trim() }, "Added. Saved as a new version."); }}>
                <span className="aa-miss">△ Landing page link needed</span>
                <input className="input" value={landing} onChange={(e) => setLanding(e.target.value)} placeholder="yourbrand.com/city" aria-label="Landing page" />
                <button type="submit" className="btn-secondary" disabled={busy || !landing.trim()}>Add link</button></form>}
          </div>
          <div className="aa-ad-sec"><b>4 · Budget and schedule</b>
            <dl className="aa-rows aa-rows-wide">
              <dt>Ad spend</dt><dd>{b?.total_text ? `${b.total_text} a day across ${v.destinations.length > 1 ? "the platforms" : d.label} · ${b.source === "PLAN" ? "from your plan" : "you set this"}` : "Not set yet"}</dd>
              <dt>Start</dt><dd>{v.timing.start || "You choose, when you turn it on"}</dd>
              {v.timing.end && <><dt>End</dt><dd>{v.timing.end}</dd></>}
            </dl>
          </div>
          <div className="aa-ad-sec"><b>5 · Account and assets</b>
            {cap === "connected" && d.state.accounts.length > 0 && <AccountForm id={id} v={v} d={d} act={c.act} busy={busy} />}
            <dl className="aa-rows aa-rows-wide">
              <dt>Account</dt><dd>{d.state.account || "Yours"}</dd>
              {d.destination === "META_ADS" && <><dt>Facebook Page</dt><dd>{page || "Needed"}</dd></>}
              <dt>Objective</dt><dd>{cap1(L(String(d.plan.values.find((x) => x.field === "Objective")?.value || v.settings.objective || "")).toLowerCase().replace(/^outcome /, "")) || "Not set"}</dd>
              <dt>Status</dt><dd>{status}</dd>
            </dl>
          </div>
          <div className="aa-ad-sec"><b>6 · Missing information and assumptions</b>
            {blocked.map((m) => <span key={m.field + m.text} className="aa-tag"><Chip label="NEEDS INFORMATION" /><span>{m.text}</span></span>)}
            {v.assumptions.map((a) => <span key={a} className="aa-tag"><Chip label="ASSUMED" /><span>{a}</span></span>)}
            {!blocked.length && !v.assumptions.length && <span className="aa-muted aa-line">Nothing missing.</span>}
          </div>
        </div>}
        <div><button type="button" className="btn-secondary" onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit"}</button></div>
      </section>
      <div className="aa-col">
        <article className="aa-col" aria-label={d.label}><Result {...c} /></article>
        <section className="aa-use" aria-label="Use it yourself">
          <span className="aa-kicker">Use it yourself</span>
          <a className="btn-secondary" href={csv} download>Download settings sheet (.csv)</a>
          <a className="btn-secondary" href={api6.exportUrl(id, v.id, "campaign_zip", v.version)} download>Download creative list</a>
          <CopyButton label="Copy settings" text={() => fetch(`${csv}&preview=1`).then((r) => r.text())} />
          <button type="button" className="btn-secondary" onClick={() => drawers.push({ key: `steps-${d.destination}`, kicker: "SET IT UP YOURSELF",
            render: () => <ol className="small">{d.setup_steps.map((x) => <li key={x}>{x}</li>)}</ol> })}>Manual set-up instructions</button>
          <span className="aa-sub">The settings sheet is for setting up by hand. It isn’t a {d.label} import file.</span>
          <OwnUse {...c} />
        </section>
        {cap !== "connected" && !d.manual && <Manual {...c} />}
      </div>
    </div>
  </>;
}

/** Choose (or change) the account a connection uses; Landfall then reads its currency, and its Page or location. */
function AccountForm({ id, v, d, act, busy }: { id: string; v: Package; d: DestinationState; act: Act; busy: boolean }) {
  const [account, setAccount] = useState(d.state.accounts[0]?.id || "");
  const [page, setPage] = useState(d.state.pages.length === 1 ? d.state.pages[0].id : "");
  const [login, setLogin] = useState("");
  return (
    <form className="stack" onSubmit={(e) => { e.preventDefault();
      act(() => api6.account(id, d.destination, { account_id: account, page_id: page || null, login_customer_id: login || null, city_id: v.city_id }),
        "Account set. Landfall read its currency" + (d.destination === "META_ADS" ? " and Page." : ".")); }}>
      <label className="small" htmlFor={`acc-${d.destination}`}>{d.state.account && !d.next.includes("choose_account") ? "Change the" : "Choose the"}{" "}
        {d.destination === "TIKTOK_ADS" ? "advertiser" : "account"}</label>
      <select id={`acc-${d.destination}`} className="input" value={account} onChange={(e) => setAccount(e.target.value)}>
        {d.state.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.currency ? ` · ${a.currency}` : ""}</option>)}</select>
      {d.destination === "META_ADS" && <select className="input" aria-label="Facebook Page the ad runs from" value={page} onChange={(e) => setPage(e.target.value)}>
        <option value="">Choose the Page…</option>{d.state.pages.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>}
      {d.destination === "GOOGLE_ADS" && <input className="input" aria-label="Manager (login) customer id, if any" placeholder="Manager customer id (optional)" value={login} onChange={(e) => setLogin(e.target.value)} />}
      <button type="submit" className="btn-secondary" disabled={busy || !account || (d.destination === "META_ADS" && !page)}>Use this account</button>
    </form>
  );
}

function Ids({ a }: { a: DestinationState["actions"][number] }) {
  const links = (a.result?.links || {}) as Record<string, string>;
  return <p className="small">Ids: <span className="mono">{Object.entries(a.ids).filter(([k]) => !k.startsWith("rid_")).map(([k, x]) => `${k} ${x}`).join(" · ")}</span>
    {Object.entries(links).filter(([k]) => k !== "link_format").map(([k, u]) => <> · <a key={k} className="link" href={u} target="_blank" rel="noreferrer">Open ↗</a> <span className="steel">(link format unverified)</span></>)}</p>;
}

function SegChip({ s }: { s?: Segment | null }) {
  if (!s) return null;
  return <Chip label={SEG[s.state] || s.state} title={s.state === "TO_CHECK" ? `${s.why || "Not supported"}: edit it, remove it, or keep it as yours`
    : s.claim_ids.length ? `Evidence: ${s.cites.join(", ")}` : undefined} />;
}

/** A flagged sentence can become the founder's own statement (STATED, YOU SAID); outside the label, so its name is its own. */
function Keep({ s, keep, label }: { s?: Segment | null; keep: (field: string) => void; label: string }) {
  if (s?.state !== "TO_CHECK") return null;
  return <button type="button" className="link" aria-label={`Keep ${label} as mine`} onClick={() => keep(s.field)}>Keep it as mine</button>;
}

function Field({ label, children, hint, after }: { label: string; children: ReactNode; hint?: ReactNode; after?: ReactNode }) {
  return <div className="stack" style={{ gap: 4 }}>
    <label className="stack" style={{ gap: 4 }}><span className="small">{label} {hint}</span>{children}</label>
    {after && <div className="btn-row">{after}</div>}
  </div>;
}

function EmailReview({ v, save, busy }: { v: Package; save: (f: object) => void; busy: boolean }) {
  const [to, setTo] = useState(v.recipients.to.join(", "));
  const [cc, setCc] = useState(v.recipients.cc.join(", "));
  const [bcc, setBcc] = useState(v.recipients.bcc.join(", "));
  const [from, setFrom] = useState(v.from_name || "");
  const [subject, setSubject] = useState(v.copy.subject?.text || "");
  const [segs, setSegs] = useState((v.copy.segments || []).map((s) => s.text));
  const list = (x: string) => x.split(/[,;\s]+/).filter(Boolean);
  const src = v.recipients_by?.to;
  const keep = (field: string) => save({ confirm: [field] });
  return (
    <section className="card stack" aria-labelledby="msg">
      <div className="card-head"><h3 id="msg" className="subheading">The message</h3><span className="small steel">{v.copy.locale}</span></div>
      {v.partner && <p className="small">To {v.partner.name} <Chip label="NOT CONFIRMED" title="Participation not confirmed" /></p>}
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save({ to: list(to), cc: list(cc), bcc: list(bcc), from_name: from, subject, segments: segs }); }}>
        <Field label="To" hint={src === "SOURCE" ? <Chip label="SOURCE" title="From the shop's own site" /> : src === "YOU_SAID" ? <Chip label="YOU SAID" /> : null}>
          <input className="input" value={to} onChange={(e) => setTo(e.target.value)} placeholder="name@shop.com" /></Field>
        <Field label="Cc"><input className="input" value={cc} onChange={(e) => setCc(e.target.value)} /></Field>
        <Field label="Bcc (shown here and bound to your approval)"><input className="input" value={bcc} onChange={(e) => setBcc(e.target.value)} /></Field>
        <Field label="Your name, as the sender"><input className="input" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="Subject" hint={<SegChip s={v.copy.subject} />} after={<Keep s={v.copy.subject} keep={keep} label="the subject" />}>
          <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} /></Field>
        {segs.map((t, i) => {
          const s = v.copy.segments?.find((x) => x.text === t);
          return <Field key={i} label={`Paragraph ${i + 1}`} hint={<SegChip s={s} />} after={<>
            <Keep s={s} keep={keep} label={`paragraph ${i + 1}`} />
            <button type="button" className="link" onClick={() => setSegs(segs.filter((_, j) => j !== i))}>Remove paragraph {i + 1}</button></>}>
            <textarea className="input" rows={3} value={t} onChange={(e) => setSegs(segs.map((x, j) => (j === i ? e.target.value : x)))} /></Field>;
        })}
        <div className="btn-row"><button type="button" className="btn-secondary" onClick={() => setSegs([...segs, ""])}>Add a paragraph</button>
          <button type="submit" className="btn-primary" disabled={busy}>Save changes</button></div>
      </form>
      {v.email && <details><summary className="small">Preview the exact message</summary>
        <pre className="preview-text small">{`To: ${v.recipients.to.join(", ")}${v.recipients.cc.length ? `\nCc: ${v.recipients.cc.join(", ")}` : ""}${v.recipients.bcc.length ? `\nBcc: ${v.recipients.bcc.join(", ")}` : ""}\nSubject: ${v.email.subject}\n\n${v.email.body}`}</pre>
        <p className="small steel">Attachments: {v.assets.filter((a) => a.role === "ATTACHMENT").map((a) => a.filename).join(", ") || "none"}</p></details>}
    </section>
  );
}

function AdCopyReview({ v, save, busy }: { v: Package; save: (f: object) => void; busy: boolean }) {
  const c = v.copy;
  const [heads, setHeads] = useState((c.headlines || []).map((s) => s.text));
  const [descs, setDescs] = useState((c.descriptions || []).map((s) => s.text));
  const [primary, setPrimary] = useState(c.primary_text?.text || "");
  const [adText, setAdText] = useState(c.ad_text?.text || "");
  const [kws, setKws] = useState((c.keywords || []).map((k) => `${k.text} [${k.match_type}]`).join("\n"));
  const [cta, setCta] = useState(v.creative.cta || "");
  const [url, setUrl] = useState(v.creative.landing_url || "");
  const [name, setName] = useState(v.creative.display_name || "");
  const keep = (field: string) => save({ confirm: [field] });
  const lines = (arr: string[], set: (x: string[]) => void, max: number, label: string, segs?: Segment[]) => arr.map((t, i) =>
    <Field key={i} label={`${label} ${i + 1}`} hint={<><span className={t.length > max ? "field-error" : "steel"}>{t.length}/{max}</span> <SegChip s={segs?.find((s) => s.text === t)} /></>} after={<Keep s={segs?.find((s) => s.text === t)} keep={keep} label={`${label.toLowerCase()} ${i + 1}`} />}>
      <input className="input" value={t} onChange={(e) => set(arr.map((x, j) => (j === i ? e.target.value : x)))} /></Field>);
  const keywords = () => kws.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^(.*?)\s*\[(EXACT|PHRASE|BROAD)\]$/i);
    return m ? { text: m[1], match_type: m[2].toUpperCase() } : { text: l, match_type: "PHRASE" };
  });
  return (
    <section className="card stack" aria-labelledby="adcopy">
      <div className="card-head"><h3 id="adcopy" className="subheading">Ad copy</h3><span className="small steel">{c.locale}</span></div>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save({ headlines: heads, descriptions: descs, primary_text: primary, ad_text: adText,
        keywords: keywords(), ...(cta ? { cta } : {}), landing_url: url, display_name: name }); }}>
        {lines(heads, setHeads, 30, "Headline", c.headlines)}
        <button type="button" className="link" onClick={() => setHeads([...heads, ""])}>Add a headline</button>
        {lines(descs, setDescs, 90, "Description", c.descriptions)}
        <button type="button" className="link" onClick={() => setDescs([...descs, ""])}>Add a description</button>
        <Field label="Primary text (Meta)" hint={<SegChip s={c.primary_text} />} after={<Keep s={c.primary_text} keep={keep} label="the primary text" />}><textarea className="input" rows={2} value={primary} onChange={(e) => setPrimary(e.target.value)} /></Field>
        <Field label="Ad text (TikTok, ≤ 100)" hint={<SegChip s={c.ad_text} />} after={<Keep s={c.ad_text} keep={keep} label="the ad text" />}><input className="input" value={adText} onChange={(e) => setAdText(e.target.value)} /></Field>
        <Field label="Keywords (Google; one per line, [PHRASE] unless you write [EXACT] or [BROAD])"><textarea className="input" rows={3} value={kws} onChange={(e) => setKws(e.target.value)} /></Field>
        <Field label="Call to action"><select className="input" value={cta} onChange={(e) => setCta(e.target.value)}>
          <option value="">Choose…</option>{["LEARN_MORE", "SHOP_NOW", "SIGN_UP"].map((x) => <option key={x} value={x}>{L(x).toLowerCase()}</option>)}</select></Field>
        <Field label="Landing page" hint={v.creative.landing_by === "DERIVED" ? <Chip label="CALC" title="Your website, from the project" /> : <Chip label="YOU SAID" />}>
          <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        <Field label="Display name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <button type="submit" className="btn-primary" disabled={busy}>Save changes</button>
      </form>
    </section>
  );
}

function Creative({ id, v, save, act, busy }: { id: string; v: Package; save: (f: object) => void; act: Act; busy: boolean }) {
  const role = v.flow === "outreach" ? "ATTACHMENT" : "CREATIVE";
  const upload = (f: File) => {
    const r = new FileReader();
    r.onload = () => act(() => api6.upload(id, v.id, { base_version: v.version, filename: f.name, media_type: f.type,
      data_b64: String(r.result).split(",")[1], role }), "Added. It's saved by Landfall; the package refers to these exact bytes.");
    r.readAsDataURL(f);
  };
  const allConfirmed = v.assets.length > 0 && v.assets.every((a) => a.rights_confirmed);
  return (
    <section className="card stack" aria-labelledby="files">
      <h3 id="files" className="subheading">{v.flow === "outreach" ? "Attachments" : "Creative"}</h3>
      {v.assets.length === 0 && <p className="small">No image is saved yet. Upload one you're allowed to use.</p>}
      <div className="card-grid">{v.assets.map((a) => (
        <figure key={a.artifact.id} className="stack" style={{ margin: 0 }}>
          {a.readable ? <img src={api6.exportUrl(id, v.id, "asset", v.version, a.artifact.id, true)} alt={a.alt_text} style={{ maxWidth: "100%", maxHeight: 180, objectFit: "contain" }} />
            : <Chip label="UNAVAILABLE" title="The saved bytes are missing: upload it again" />}
          <figcaption className="small"><strong>{a.filename}</strong> · {Math.round(a.size / 1024)} KB · <span className="mono">{a.artifact.sha256.slice(0, 12)}</span><br />
            {a.source.kind === "FETCHED" ? <>From your site{a.source.url && <> · <a className="link" href={a.source.url} target="_blank" rel="noreferrer">source ↗</a></>}</> : "Uploaded by you"} ·{" "}
            <Chip label={a.rights === "brand_own_unconfirmed" ? "RIGHTS TO CHECK" : a.rights === "founder_supplied" ? "YOU SAID" : L(a.rights).toUpperCase()} />
            {a.rights_confirmed && <Chip label="CHECKED BY YOU" />}
            {a.usage_constraints.map((u) => <span key={u} className="steel"><br />{u}</span>)}<br />
            <a className="link" href={api6.exportUrl(id, v.id, "asset", v.version, a.artifact.id)} download>Download</a>{" · "}
            <button type="button" className="link" disabled={busy} onClick={() => save({ remove_assets: [a.artifact.id] })}>Remove</button></figcaption>
        </figure>))}</div>
      {v.asset_attempts.filter((x) => x.outcome !== "OK").map((x) => <p key={x.product} className="small"><Chip label={x.outcome === "REFUSED" ? "NOT READ" : "UNKNOWN"} /> {x.product}: {x.reason} (unknown, not absent)</p>)}
      {v.assets.length > 0 && <label className="row-inline small"><input type="checkbox" checked={allConfirmed} disabled={busy}
        onChange={(e) => save({ rights_confirmed: e.target.checked })} /> I hold the rights to use these images {v.flow === "digital" ? "in ads" : "in this message"}</label>}
      <label className="small">Upload {role === "CREATIVE" ? "an image (JPEG or PNG)" : "a file (JPEG, PNG or PDF)"}{" "}
        <input type="file" accept={role === "CREATIVE" ? "image/jpeg,image/png" : "image/jpeg,image/png,application/pdf"} disabled={busy}
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
    </section>
  );
}

function Audience({ v, save, busy }: { v: Package; save: (f: object) => void; busy: boolean }) {
  const [radius, setRadius] = useState(v.targeting.geo.radius_km ? String(v.targeting.geo.radius_km) : "");
  const a = v.audience_rationale;
  return (
    <section className="card stack" aria-labelledby="aud">
      <h3 id="aud" className="subheading">Audience rationale and targeting</h3>
      <p className="small"><Chip label="HYPOTHESIS" /> {a.hypothesis}{noCaption(a.caveat) ? ` (${noCaption(a.caveat)})` : ""}</p>
      {a.qloo_interests.length > 0 && <p className="small"><Chip label="QLOO" title={a.label} /> {a.qloo_interests.join(", ")}
        <span className="steel"> · informs the recommendation; never sent to a platform as targeting</span></p>}
      {v.flow === "digital" && <>
        <div><span className="label steel">PROPOSED PLATFORM TARGETING</span>
          <p className="small">Location: {v.targeting.geo.name}{v.targeting.geo.radius_km ? ` + ${Math.round((v.targeting.geo.radius_km / 1.609344) * 10) / 10} mi` : ""}{" "}
            <Chip label={v.targeting.geo.radius_by === "YOU_SAID" ? "YOU SAID" : v.targeting.geo.radius_by ? "HYPOTHESIS" : "NOT KNOWN"} /></p>
          {v.targeting.platform_interests.map((t) => <p key={t.name} className="small">{t.name} <Chip label={t.basis === "PLATFORM_OPTION" ? "PLATFORM OPTION" : "TO CHECK IN ADS MANAGER"} />
            <span className="steel"> · not sent; look up the platform's own id</span></p>)}</div>
        <form className="search-row" onSubmit={(e) => { e.preventDefault(); save({ radius_km: radius ? Number(radius) : null }); }}>
          <label className="small" htmlFor="radius">Radius (km)</label>
          <input id="radius" className="input" type="number" min={1} max={80} value={radius} onChange={(e) => setRadius(e.target.value)} />
          <button type="submit" className="btn-secondary" disabled={busy}>Save</button></form></>}
    </section>
  );
}

const PLATFORM: Record<string, string> = { META_ADS: "Meta", GOOGLE_ADS: "Google", TIKTOK_ADS: "TikTok" };

/** S5-23, S5-24: one total daily budget split across the chosen platforms; from the signed plan, YOU SAID, or not known. */
function BudgetLine({ v }: { v: Package }) {
  const b = v.budget_view;
  if (!b || !b.total_text) return <p className="small"><Chip label="NOT KNOWN" /> No daily budget yet: enter one, or set it in your plan's digital scenario and re-prepare.</p>;
  const split = Object.entries(b.split).map(([d, x]) => `${x} on ${PLATFORM[d] ?? d}`).join(", ");
  return (
    <>
      <p className="small">{b.source === "PLAN" ? <><Chip label="FROM YOUR PLAN" /> From your signed plan (v{b.plan?.version}): </> : <><Chip label="YOU SAID" /> </>}
        {b.total_text} a day in total{Object.keys(b.split).length > 1 ? `, split ${split}` : ""}.</p>
      {b.differs_from_plan && <p className="small"><Chip label="CHANGED" /> Differs from your signed plan ({b.plan_total_text} a day).</p>}
    </>
  );
}

function Budget({ v, save, busy }: { v: Package; save: (f: object) => void; busy: boolean }) {
  const [amt, setAmt] = useState(amount(v.budget.amount));
  const [ccy, setCcy] = useState(v.budget.amount?.ccy || v.settings.currency_hint || "USD");
  const [cpc, setCpc] = useState(amount(v.budget.max_cpc));
  const [start, setStart] = useState(v.timing.start || "");
  const [end, setEnd] = useState(v.timing.end || "");
  return (
    <section className="card stack" aria-labelledby="bud">
      <h3 id="bud" className="subheading">Budget, timing and settings</h3>
      <BudgetLine v={v} />
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save({ budget: amt ? { amount: amt, ccy } : null, max_cpc: cpc ? { amount: cpc, ccy } : null, start: start || null, end: end || null }); }}>
        <div className="search-row"><Field label="Daily budget (total, split across platforms)"><input className="input" inputMode="decimal" value={amt} onChange={(e) => setAmt(e.target.value)} /></Field>
          <Field label="Currency"><input className="input" value={ccy} maxLength={3} onChange={(e) => setCcy(e.target.value.toUpperCase())} /></Field></div>
        <Field label="Max cost per click (Google, optional)"><input className="input" inputMode="decimal" value={cpc} onChange={(e) => setCpc(e.target.value)} /></Field>
        <div className="search-row"><Field label="Start"><input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label="End (optional)"><input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field></div>
        <button type="submit" className="btn-primary" disabled={busy}>Save</button>
      </form>
      <p className="small steel">Objective: {L(v.settings.objective).toLowerCase() || "not set"}. Everything is created paused; Landfall never activates a campaign or spends.</p>
    </section>
  );
}

function Sources({ v }: { v: Package }) {
  return (
    <details className="card">
      <summary className="subheading">Sources and assumptions</summary>
      <div className="stack">
        <div><span className="label steel">EVIDENCE THE COPY MAY USE</span>{v.evidence.map((e) => <p key={e.id} className="small"><span className="mono">{e.id}</span> <Chip label={e.role === "CLAIM" ? "SOURCED" : e.role === "QLOO" ? "QLOO" : e.role === "FOUNDER" ? "YOU SAID" : "BRAND FACT MATCH"} /> {e.text}</p>)}</div>
        <div><span className="label steel">STILL TO VALIDATE (FROM THE DRAFT)</span>{v.assumptions.map((a) => <p key={a} className="small"><Chip label="TO CHECK" /> {a}</p>)}</div>
        {v.faults.length > 0 && <div><span className="label steel">LEFT OUT BY THE CHECKS</span>{v.faults.map((f) => <p key={f.field + f.reason} className="small">{f.reason}</p>)}</div>}
      </div>
    </details>
  );
}

function Details({ id, v, d, act, busy }: { id: string; v: Package; d: DestinationState; act: Act; busy: boolean }) {
  const s = d.state;
  const rows: [string, string][] = [["Adapter", L(s.adapter)], ["Deployment", L(s.deployment) + (s.missing_config.length ? ` (missing ${s.missing_config.join(", ")})` : "")],
    ["Platform access", `${L(s.platform_access)} · ${s.access_note}`], ["Connection", L(s.connection) + (s.account ? ` · ${s.account}` : "")],
    ["Package", L(s.package)], ["Provider validation", L(s.provider_validation) + (s.provider_validation_message ? ` · ${s.provider_validation_message}` : "")],
    ["Your approval", L(s.approval) + (s.approval_use === "manual" ? " (for your own use)" : "")], ["Execution", L(s.execution)]];
  return (
    <details className="card" aria-label={`${d.label} details`}>
      <summary className="subheading">{d.label}: what Landfall would send, and its state</summary>
      <div className="stack">
        <dl className="defs small">{rows.map(([k, x]) => <div key={k} className="row-inline"><dt className="steel" style={{ minWidth: 140 }}>{k}</dt><dd style={{ margin: 0 }}>{x}</dd></div>)}</dl>
        {s.connection === "ACTIVE" && s.accounts.length > 0 && !d.next.includes("choose_account") &&
          <AccountForm id={id} v={v} d={d} act={act} busy={busy} />}
        <div className="btn-row">
          {!sharedOf(d) && s.provider_validation !== "NOT_SUPPORTED" && s.connection !== "NOT_AVAILABLE" && d.plan.blockers.length === 0 &&
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api6.validate(id, v.id, d.destination))}>Check with {d.label} (validate only)</button>}
          {!sharedOf(d) && ["ACTIVE", "EXPIRED", "SIMULATED"].includes(s.connection) && <button type="button" className="btn-secondary" disabled={busy}
            onClick={() => act(() => api6.disconnect(id, d.destination), "Disconnected. Landfall deleted its copy of the token.")}>Disconnect</button>}
        </div>
        <div><span className="label steel">{d.plan.label}</span>
          <table className="small" style={{ width: "100%", borderCollapse: "collapse" }}><tbody>
            {d.plan.values.map((x) => <tr key={x.field} style={{ borderTop: "1px solid var(--lf-fog)" }}><td className="steel">{x.field}</td>
              <td>{x.value === null || x.value === undefined || x.value === "" ? <Chip label="NOT KNOWN" /> : Array.isArray(x.value) ? x.value.join(", ") || "none" : String(x.value)}</td>
              <td><Chip label={x.label} title={x.source} /></td></tr>)}</tbody></table></div>
        {d.actions.length > 0 && <div><span className="label steel">ATTEMPTS</span>{d.actions.map((a) => <div key={a.id} className="small">
          <p><Chip label={L(a.status)} /> {a.mode === "SIMULATED" && <Chip label="SIMULATED" />} {a.reference} · {new Date(a.at).toLocaleString()}{a.error ? ` · ${a.error}` : ""}</p>
          <p className="mono">{a.steps.map((st) => `${st.step}: ${L(st.status).toLowerCase()}${st.provider_id ? ` (${st.provider_id})` : ""}`).join(" → ")}</p>
          {a.partial && <p>Created before it stopped (kept, and their state shown): {a.partial.join(", ")}</p>}
          {a.verification?.objects && <p>Read back: {Object.entries(a.verification.objects).map(([k, o]) => `${k} ${Object.values(o).filter(Boolean).join("/")}`).join(" · ")}</p>}</div>)}</div>}
        <div><span className="label steel">SET IT UP YOURSELF</span><ol className="small">{d.setup_steps.map((x) => <li key={x}>{x}</li>)}</ol></div>
      </div>
    </details>
  );
}

// ---- slice 6.5: earlier wording (R5), products in this proposal (A4a), message templates (G13) ----
const asText = (x: unknown): string => Array.isArray(x) ? x.map(asText).join(" ") : x && typeof x === "object" && "text" in x ? String((x as { text: string }).text) : String(x ?? "");

function EarlierWording({ id, v, act, busy }: { id: string; v: Package; act: Act; busy: boolean }) {
  const fields = Object.entries(v.earlier_wording || {});
  if (!fields.length) return null;
  return (
    <section className="card stack" aria-label="Your earlier wording">
      <h2 className="subheading">Your earlier wording</h2>
      <p className="small">This version was written again from a newer draft. You had edited these; choose which to keep. Until you do, it can't be approved.</p>
      {fields.map(([f, old]) => (
        <div key={f} className="stack">
          <div className="label steel">{L(f).toUpperCase()}</div>
          <p className="small"><Chip label="YOU SAID" /> {asText(old)}</p>
          <div className="btn-row">
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api65.wording(id, v.id, v.version, f, "mine"), "Kept your wording; it was checked again.")}>Keep mine</button>
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => api65.wording(id, v.id, v.version, f, "new"), "Using the new text.")}>Use the new text</button>
          </div>
        </div>
      ))}
    </section>
  );
}

function Products({ id, v, save, busy }: { id: string; v: Package; save: (fields: object, done?: string) => void; busy: boolean }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [sel, setSel] = useState(new Set(v.products.map((x) => x.id)));
  useEffect(() => { fetch(`/api/projects/${id}/drafts/${v.draft.id}`).then((r) => r.json()).then(setDraft).catch(() => null); }, [id, v.draft.id]);
  const all = draft?.products || v.products;
  if (all.length < 2) return null;
  const changed = all.some((x) => sel.has(x.id) !== v.products.some((y) => y.id === x.id));
  const written = [...(v.copy_for || all.map((x) => x.id))].sort().join() === v.products.map((x) => x.id).sort().join();
  return (
    <section className="card stack" aria-label="Products in this proposal">
      <h3 className="subheading">Products in this proposal</h3>
      <ul className="plain">{all.map((x) => (
        <li key={x.id}><label className="row-inline"><input type="checkbox" checked={sel.has(x.id)} disabled={busy}
          onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(x.id); else n.delete(x.id); setSel(n); }} /> {x.title}</label></li>
      ))}</ul>
      <p className="small steel">Taking a product out removes its images and flags any sentence that cites it. The approval of this version becomes outdated.</p>
      <div className="btn-row"><button type="button" className="btn-secondary" disabled={busy || !changed || sel.size === 0}
        onClick={() => save({ products: [...sel] }, "Saved the product choice as a new version. Earlier approvals are now outdated.")}>Save the product choice</button>
        {!changed && !written && <button type="button" className="btn-secondary" disabled={busy} onClick={() => api65.prepare(id, v.draft.id, v.partner?.lead_id || undefined)
          .then(() => window.location.reload())}>Rewrite for these products</button>}</div>
    </section>
  );
}

function Templates({ id, v, act, busy }: { id: string; v: Package; act: Act; busy: boolean }) {
  const [list, setList] = useState<Template[] | null>(null);
  const [pick, setPick] = useState("");
  useEffect(() => { api65.templates(id).then((t) => setList(t.filter((x) => x.method === v.method.toUpperCase() || x.method === v.method))).catch(() => setList([])); }, [id, v.method]);
  if (!list?.length) return null;
  const t = list.find((x) => x.id === pick);
  return (
    <section className="card stack" aria-label="Message template">
      <h3 className="subheading">Message template</h3>
      <p className="small">{v.template_ref ? "This message uses a template." : "This message is the generated draft."} A template's blanks are filled only from your records; anything missing is listed, never guessed.</p>
      <div className="row-inline">
        <select className="input" aria-label="Template" value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">Choose a template</option>{list.map((x) => <option key={x.id} value={x.id}>{x.name}{x.origin === "YOU_SAID" ? " (yours)" : ""}</option>)}</select>
        <button type="button" className="btn-secondary" disabled={busy || !t} onClick={() => act(() => api65.applyTemplate(id, v.id, v.version, { id: t!.id, version: t!.version }),
          "Applied the template as a new version. Anything it still needs is listed under Package review; earlier approvals are now outdated.")}>Use this template</button>
      </div>
      {t && <p className="small steel"><strong>{t.subject}</strong> · {t.body.slice(0, 160)}{t.body.length > 160 ? "…" : ""}</p>}
    </section>
  );
}
