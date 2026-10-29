import type { CSSProperties } from "react";

/** A port of design/v4/prototype/"Campaign Mockup.dc.html" (D3a, 27 Oct 2026): every kind is composed in the page from the
 *  brand's own copy, its city and its saved product photos. Sizes are in cqw, so one component serves the 112 px kit tile,
 *  the 110 px message preview and the 300 px drawer. The product is the hero; a product with no saved photo is v4's hatched
 *  "No image" tile, so the layout keeps its shape. Every kind carries ILLUSTRATIVE; kinds that name a partner add
 *  PROPOSAL · PARTNER NOT CONFIRMED. Nothing here is a real post, installation, ad or partnership. */
export type MockKind = "shelf" | "sampling" | "social" | "brief" | "ad1" | "ad2" | "ad3" | "waitlist";
export type MockProduct = { title: string; src: string | null };
export type MockData = {
  brand: string; city: string; concept: { name: string; line: string | null };
  products: MockProduct[]; partner: string | null;
  copy: { headlines: string[]; primary_text: string | null; cta: string | null; bodies?: (string | null)[] } | null;  // bodies: one per ad (Launch's ad choices)
  scene?: string | null;  // the generated setting photo (people in soft focus), the background of the ads and the social post
  looks?: string[][] | null;  // the three ads' [background, text, button, button text], from the scene's colors (mockups.looks_of)
};

/** The setting photo behind an ad or post, washed toward `tint` top and bottom so the copy and the products stay legible. */
function Scene({ src, tint }: { src: string; tint: string }) {
  return <>
    <img src={src} alt="" aria-hidden="true" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />
    <div style={{ position: "absolute", inset: 0, background: `linear-gradient(${tint}cc 0%, ${tint}80 34%, ${tint}00 55%, ${tint}66 78%, ${tint}e6 100%)` }} />
  </>;
}

const SERIF = "'Young Serif', Georgia, serif";
const SANS = "'Schibsted Grotesk', system-ui, sans-serif";
const MONO = "'JetBrains Mono', ui-monospace, monospace";
const f = (w: number, size: number, lh: number | string, fam = SANS) => `${w} ${size}cqw/${lh} ${fam}`;
const NAMED: MockKind[] = ["sampling", "social"];  // v4 renderVals: these name a partner, so they're a proposal

/** The ad copy per colorway: the package's own headlines and primary text when a digital package exists; otherwise
 *  v4's template lines filled with the brand, its products and the city. Never the plan's internal test name, and never
 *  another method's concept line (an ad is shopper-facing). */
export function adCopy(d: MockData, i: 0 | 1 | 2) {
  const h = d.copy?.headlines || [];
  const names = d.products.slice(0, 3).map((p) => p.title).join(", ");
  return [
    { h: h[0] || `${d.brand}, coming to ${d.city}`, b: d.copy?.primary_text || (names ? `${names}. Join the list to hear when it lands.` : "Join the list to hear when it lands.") },
    { h: h[1] || `Coming to ${d.city}?`, b: `${d.brand}. Tell us if you'd like it here.` },
    { h: h[2] || "Where would you buy it?", b: `Tell us your ${d.city} shop. We’ll try to be there.` },
  ].map((x, j) => (d.copy?.bodies?.[j] ? { ...x, b: d.copy.bodies[j]! } : x))[i];
}
const sentence = (s: string) => (/[.!?]$/.test(s) ? s : `${s}.`);
// an ad platform's CTA enum (LEARN_MORE) reads as words on the button
const cta = (d: MockData) => (d.copy?.cta ? d.copy.cta.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()) : "Join the list");
const AD: Record<"ad1" | "ad2" | "ad3", [string, string, string, string]> = {  // background, text, CTA fill, CTA text
  // only without a scene: otherwise the looks come from the scene's own colors, so they follow the brand (owner, 28 Oct 2026)
  ad1: ["#f3ede1", "#14323d", "#14323d", "#f3ede1"], ad2: ["#1f4e5f", "#f3ede1", "#e9c46a", "#14323d"], ad3: ["#e07a5f", "#14323d", "#14323d", "#f3ede1"],
};

function NoImage({ w, label = "No image" }: { w: number; label?: string }) {
  return (
    <div style={{ width: `${w}cqw`, aspectRatio: "3 / 4", borderRadius: "1cqw", flex: "none", display: "grid", placeItems: "center",
      background: "repeating-linear-gradient(45deg,#eef0f3 0 6px,#fff 6px 12px)", boxShadow: "inset 0 0 0 1px #dcdee1",
      font: `500 max(8px, ${Math.max(1.6, w / 7)}cqw)/1.2 ${MONO}`, color: "#55585e", textAlign: "center", padding: "1cqw", boxSizing: "border-box" }}>
      {label}
    </div>
  );
}

/** The product row (v4's cans): up to three products, photos first, so each reads as the hero; a missing photo, or no
 *  product at all, is a hatched tile. */
export const shownProducts = (d: MockData) => [...d.products].sort((a, b) => Number(!a.src) - Number(!b.src)).slice(0, 3);
function Products({ d, width, max = 18, names, justify = "flex-start" }: { d: MockData; width: number; max?: number; names?: boolean; justify?: string }) {
  const items = shownProducts(d);
  if (!items.length) return <div style={{ display: "flex", justifyContent: justify }}><NoImage w={Math.min(max * 1.6, width)} /></div>;
  const gap = width * 0.06, w = Math.min(max, (width - gap * (items.length - 1)) / items.length);
  return (
    <div style={{ display: "flex", gap: `${gap}cqw`, alignItems: "flex-end", justifyContent: justify, position: "relative" }}>
      {items.map((p, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1cqw" }}>
          {p.src ? <img src={p.src} alt={p.title} style={{ width: `${w}cqw`, aspectRatio: "3 / 4", objectFit: "contain", display: "block",
            filter: "drop-shadow(0 1.2cqw 1.6cqw rgba(20,50,61,.25))" }} /> : <NoImage w={w} />}
          {names && <span style={{ font: f(500, 1.5, 1.2, MONO), color: "#55585e", maxWidth: `${w + 2}cqw`, textAlign: "center",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.title.toUpperCase()}</span>}
        </div>
      ))}
    </div>
  );
}

const box = (s: CSSProperties): CSSProperties => ({ boxSizing: "border-box", overflow: "hidden", position: "relative", ...s });

function Body({ kind, d }: { kind: MockKind; d: MockData }) {
  const brand = d.brand, BRAND = brand.toUpperCase();
  switch (kind) {
    case "shelf":
      return (
        <div role="img" aria-label={`Illustrative shelf talker: ${d.concept.name}.`} style={box({ aspectRatio: "3 / 2", background: "#f3ede1",
          borderRadius: "1.2cqw", boxShadow: "inset 0 0 0 0.3cqw #1f4e5f", display: "grid", gridTemplateColumns: "1.25fr 1fr", color: "#14323d" })}>
          <div style={{ padding: "7cqw 0 6cqw 7cqw", display: "flex", flexDirection: "column", gap: "2.2cqw", minWidth: 0 }}>
            <span style={{ font: f(600, 2.6, 1.2), letterSpacing: "0.14em", textTransform: "uppercase", color: "#c25b3f" }}>Visiting maker · {d.city}</span>
            <span style={{ font: f(400, 6.4, 1.02, SERIF), letterSpacing: "-0.01em" }}>{d.concept.name}</span>
            {d.concept.line && <span style={{ font: f(400, 2.6, 1.35) }}>{d.concept.line}</span>}
            <span style={{ flex: 1 }} />
            <div style={{ display: "flex", alignItems: "center", gap: "2cqw" }}>
              <span style={{ font: f(700, 3.6, 1), letterSpacing: "0.2em" }}>{BRAND}</span>
              <span style={{ height: "0.3cqw", flex: 1, background: "#14323d", opacity: 0.3 }} />
            </div>
          </div>
          <div style={{ background: "#e5ddcc", display: "flex", alignItems: "flex-end", justifyContent: "center", padding: "0 3cqw 6cqw" }}>
            <Products d={d} width={38} max={17} justify="center" />
          </div>
        </div>
      );
    case "sampling":
      return (
        <div role="img" aria-label={`Illustrative sampling card: ${brand} in ${d.city}.`} style={box({ aspectRatio: "3 / 4", background: "#1f4e5f",
          borderRadius: "1.6cqw", color: "#f3ede1", padding: "14cqw 9cqw 8cqw", display: "flex", flexDirection: "column", gap: "3cqw" })}>
          <span style={{ font: f(600, 3.6, 1.2), letterSpacing: "0.14em", textTransform: "uppercase", color: "#e9c46a" }}>{d.concept.name}</span>
          <span style={{ font: f(400, 13, 0.98, SERIF), letterSpacing: "-0.015em", overflowWrap: "anywhere" }}>{brand}</span>
          <span style={{ font: f(400, 7, 1.1, SERIF), color: "#f6c8b6" }}>Try it in {d.city}.</span>
          {d.concept.line && <span style={{ font: f(400, 4.2, 1.35), maxWidth: "90%" }}>{d.concept.line}</span>}
          <div style={{ marginTop: "2cqw" }}><Products d={d} width={78} max={24} /></div>
          <span style={{ flex: 1 }} />
          <span style={{ font: f(400, 3.8, 1.35), borderTop: "0.3cqw solid rgba(243,237,225,0.4)", paddingTop: "3cqw" }}>
            {d.partner ? <>Find us with {d.partner}, {d.city}.</> : <>Find us in {d.city}.</>}</span>
        </div>
      );
    case "social":
      return (
        <div role="img" aria-label={`Illustrative social post from ${brand}'s account announcing ${d.city}.`}
          style={box({ background: "#fff", border: "0.3cqw solid #dcdee1", borderRadius: "2cqw", color: "#181b1f" })}>
          <div style={{ display: "flex", alignItems: "center", gap: "2.6cqw", padding: "3.4cqw 4cqw" }}>
            <span style={{ width: "8cqw", height: "8cqw", borderRadius: "50%", background: "#1f4e5f", color: "#f3ede1", display: "grid",
              placeItems: "center", font: f(400, 4.4, 1, SERIF), flex: "none" }}>{brand.slice(0, 1).toUpperCase()}</span>
            <span style={{ display: "flex", flexDirection: "column" }}><b style={{ font: f(700, 3.8, 1.2) }}>{brand}</b>
              <span style={{ font: f(400, 3.2, 1.2), color: "#55585e" }}>{d.city}</span></span>
          </div>
          <div style={box({ aspectRatio: "1 / 1", background: "#e07a5f", display: "flex", flexDirection: "column", justifyContent: "space-between", padding: "8cqw" })}>
            {d.scene ? <Scene src={d.scene} tint="#f3ede1" />
              : <div style={{ position: "absolute", right: "-14cqw", top: "-14cqw", width: "62cqw", height: "62cqw", borderRadius: "50%", background: "#e9c46a", opacity: 0.9 }} />}
            <span style={{ position: "relative", font: f(400, 11, 0.98, SERIF), color: "#14323d", letterSpacing: "-0.015em", maxWidth: "80%" }}>{brand} is visiting {d.city}.</span>
            <div style={{ position: "relative" }}><Products d={d} width={84} max={28} /></div>
          </div>
          <div style={{ padding: "3.6cqw 4cqw 4.4cqw", font: f(400, 3.6, 1.45) }}><b>{brand}</b> {sentence(d.concept.line || d.concept.name)}
            {d.partner && <> With {d.partner}.</>}</div>
        </div>
      );
    case "brief": {
      const n = d.products.length;
      return (
        <div role="img" aria-label={`Product image brief: ${n || "no"} product${n === 1 ? "" : "s"} on a neutral ground.`} style={box({ aspectRatio: "16 / 9",
          background: "#f6f4ef", borderRadius: "1cqw", backgroundImage: "linear-gradient(#e7e3da 1px,transparent 1px),linear-gradient(90deg,#e7e3da 1px,transparent 1px)",
          backgroundSize: "5cqw 5cqw" })}>
          <span style={{ position: "absolute", left: "4cqw", top: "3.4cqw", font: f(500, 1.9, 1.3, MONO), color: "#55585e" }}>
            SHOT 1 OF 1 · {n ? `${Math.min(n, 3)} PRODUCT${n === 1 ? "" : "S"}` : "PRODUCT TO CHOOSE"} · NEUTRAL GROUND · 16:9</span>
          <div style={{ position: "absolute", left: "22%", right: "22%", top: "20%", bottom: "16%", border: "0.25cqw dashed #898c91", display: "flex",
            alignItems: "flex-end", justifyContent: "center", paddingBottom: "3cqw" }}>
            <Products d={d} width={52} max={14} names justify="center" />
          </div>
          <span style={{ position: "absolute", right: "4cqw", bottom: "3.4cqw", font: f(500, 1.9, 1.3, MONO), color: "#55585e", textAlign: "right" }}>
            Use existing site photos if rights allow</span>
        </div>
      );
    }
    case "ad1": case "ad2": case "ad3": {
      const i = kind === "ad1" ? 0 : kind === "ad2" ? 1 : 2;
      const [bg, fg, cb, cf] = d.looks?.[i] || AD[kind], c = adCopy(d, i);
      return (
        <div role="img" aria-label={`Illustrative ad: ${c.h}`} style={box({ aspectRatio: "9 / 16", background: bg, color: fg, borderRadius: "4cqw",
          padding: "9cqw 7cqw 8cqw", display: "flex", flexDirection: "column", gap: "3.4cqw" })}>
          {d.scene ? <Scene src={d.scene} tint={bg} />
            : <div style={{ position: "absolute", right: "-18cqw", top: "-14cqw", width: "56cqw", height: "56cqw", borderRadius: "50%", background: "#e9c46a", opacity: 0.85 }} />}
          <span style={{ position: "relative", font: f(600, 3.6, 1.2), letterSpacing: "0.1em", textTransform: "uppercase" }}>Sponsored · {d.city}</span>
          <span style={{ position: "relative", font: f(400, 11, 1, SERIF), letterSpacing: "-0.01em", overflowWrap: "anywhere" }}>{c.h}</span>
          <span style={{ position: "relative", font: f(400, 4.6, 1.35), display: "-webkit-box", WebkitLineClamp: 5, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{c.b}</span>
          <div style={{ marginTop: "auto", position: "relative" }}><Products d={d} width={86} max={28} /></div>
          <span style={{ position: "relative", background: cb, color: cf, borderRadius: 99, font: f(600, 4.4, 1), padding: "3.4cqw", textAlign: "center" }}>{cta(d)}</span>
        </div>
      );
    }
    case "waitlist":
      return (
        <div role="img" aria-label={`Illustrative sign-up page: ${brand} in ${d.city}? Email, the shop you would buy from, and consent wording to check.`}
          style={box({ aspectRatio: "3 / 4", background: "#fff", borderRadius: "2cqw", boxShadow: "inset 0 0 0 0.5cqw #dcdee1", padding: "9cqw 8cqw",
            display: "flex", flexDirection: "column", gap: "4cqw", color: "#14323d" })}>
          <span style={{ font: f(700, 3.6, 1), letterSpacing: "0.2em" }}>{BRAND}</span>
          <span style={{ font: f(400, 10, 1.02, SERIF) }}>{brand} in {d.city}?</span>
          <span style={{ font: f(400, 4, 1.35), color: "#55585e" }}>Not sold in {d.city} yet. Tell us where you’d buy it and we’ll try to be there.</span>
          {["Email", "Shop you’d buy from"].map((t) => <span key={t} style={{ height: "9.4cqw", border: "0.25cqw solid #898c91", borderRadius: "0.8cqw",
            font: `400 3.8cqw/9.4cqw ${SANS}`, color: "#898c91", padding: "0 1.2cqw", flex: "none" }}>{t}</span>)}
          <span style={{ font: f(500, 3, 1.35, MONO), color: "#6f4fa1", border: "0.4cqw dashed #6f4fa1", padding: "1.4cqw 2cqw" }}>Consent wording · to check</span>
          <span style={{ marginTop: "auto", background: "#1f4e5f", color: "#f3ede1", borderRadius: "1.6cqw", font: f(600, 4, 1), padding: "3cqw", textAlign: "center" }}>{cta(d)}</span>
        </div>
      );
  }
}

const BADGE = (color: string): CSSProperties => ({ font: `500 max(9px,1.9cqw)/1.4 ${MONO}`, background: "#fff", color, border: `1px solid ${color}`,
  borderRadius: 3, padding: "0 0.5em", textAlign: "right" });

export function CampaignMockup({ kind, d }: { kind: MockKind; d: MockData }) {
  return (
    <div className="campaign-mockup" style={{ containerType: "inline-size", width: "100%", position: "relative", fontFamily: SANS }}>
      <Body kind={kind} d={d} />
      <div style={{ position: "absolute", right: "2.4cqw", top: "2.4cqw", display: "flex", gap: "1cqw", flexWrap: "wrap", justifyContent: "flex-end", maxWidth: "70%" }}>
        <span style={BADGE("#181b1f")}>ILLUSTRATIVE</span>
        {NAMED.includes(kind) && <span style={BADGE("#935a11")}>PROPOSAL · PARTNER NOT CONFIRMED</span>}
      </div>
    </div>
  );
}
