"""Slice 6 · delivery (BUILD-PLAN "Slice 6 · Delivery"; CONTRACTS B8 "Slice 6 delivery").

One package, approval, persistence and execution path. Provider specifics are four ordinary modules in
`landfall/adapters/`; this file never names a provider endpoint.

- Artifacts [P6-R4]: bytes saved once, content-addressed by sha256; preview, export and upload read those bytes.
- DeliveryPackage vN from a 4.5 ExperimentDraft (the pre-slice-5 route): copy written by `delivery_copy@1` and judged
  segment by segment, the brand's own product images (`delivery_fetch`), founder edits as YOU SAID.
- Exports are deterministic bytes of one version and never a SENT record.
- Approval binds the version, the artifact manifest, the ProviderPlan and the account identity.
- Execution [P6-R1]: a write is never retried; anything but a documented non-commit is UNCERTAIN, and only the founder
  resolves it. The Action Center summary puts an action's outcome before any capability prompt [P6-R2].
"""
import base64
import email.policy
import hashlib
import io
import json
import re
import sqlite3
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, getaddresses
from typing import Literal

import httpx
from pydantic import Field
from strands import Agent

from . import config, db, fetch, markets, research, runner, specialist
from .cassette import NoRecording
from .nova import make_model
from .runner import HANDLERS, Run

DESTINATIONS = ("EMAIL", "META_ADS", "GOOGLE_ADS", "TIKTOK_ADS")
OUTREACH = ("retail", "popup", "creator", "community")


def adapters() -> dict:
    from .adapters import ADAPTERS
    return ADAPTERS


class Refused(research.Refused):
    pass


def h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _payload(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}


# ---- artifacts [P6-R4] ----
IMAGE_TYPES = ("image/jpeg", "image/png")
ATTACH_TYPES = IMAGE_TYPES + ("application/pdf",)
MAX_ARTIFACT = 8_000_000


def save_artifact(pid: str, data: bytes, filename: str, media_type: str, source: dict, rights: str,
                  constraints: list[str]) -> dict:
    """Immutable: the bytes are stored under their digest, so a later file with the same name is a different artifact."""
    sha = hashlib.sha256(data).hexdigest()
    db.x("INSERT OR IGNORE INTO artifact_bytes VALUES(?,?)", sha, data)
    rec = db.put_record("artifact", pid, {"sha256": sha, "media_type": media_type, "filename": filename, "size": len(data),
                                          "bytes_ref": sha, "source": source, "rights": rights,
                                          "usage_constraints": constraints}, rid=db.new_id("art"))
    return {"id": rec["id"], "sha256": sha}


def artifact_bytes(ref: dict) -> bytes | None:
    """The saved bytes of one artifact ref, or None: missing or not matching its digest is never substituted."""
    row = db.q("SELECT bytes FROM artifact_bytes WHERE sha256=?", ref.get("sha256") or "")
    if not row or hashlib.sha256(row[0]["bytes"]).hexdigest() != ref["sha256"]:
        return None
    return row[0]["bytes"]


def manifest(pkg: dict) -> list[dict]:
    out = []
    for a in pkg["assets"]:
        art = db.get_record(a["artifact"]["id"]) or {}
        out.append({"sha256": a["artifact"]["sha256"], "filename": art.get("filename"), "media_type": art.get("media_type"),
                    "role": a["role"], "usage_constraints": art.get("usage_constraints", []), "rights": art.get("rights")})
    return out


# ---- money: moved to money.py (slice 5) ----
from . import money as money_  # noqa: E402
from .money import EXPONENT, minor_to, money, money_text  # noqa: E402,F401


# ---- preparing a package from a draft (delivery_copy@1 + delivery_fetch) ----
COPY_VERSION = "delivery_copy@7"  # @2 required schemas; @3 cite or it is checked; @4 built from the facts; @5 written for a reader; @6 partner questions only; @7 ads styled from Qloo (owner, 28 Oct 2026)
CHECK_VERSION = "delivery_check@3"  # @2 each sentence against its own citations + guards (P6-I7); @3 the delivery rubric
# both run on config.NOVA_COPY_MODEL (recorded in the package)
# the writer may take one corrective retry when its structured answer fails validation (LIVE L6-B: required fields
# left out); one call stays reserved, so model 3 = two writer attempts. The check is counted separately (prose: 1).
COPY_CAPS = {"model": 3, "search": 0, "page": 0, "presence": 0, "qloo": 0, "urn": 0, "check": 0, "prose": 1,
             "locate": 0, "profile": 0}
COPY_USD, COPY_EXPECTED = 0.10, 0.03  # Nova Pro: ~$0.002–0.003 per writer or check call at the measured sizes
COPY_SYSTEM = """You write a founder's launch materials for one experiment, in the language given. A reviewer then
checks every line against the evidence it cites, and flags anything it can't verify, so write only what the evidence
says.

Rules for every line:
- A line that states anything about the brand, a product, a place or a person is built from listed evidence, and its
  `cites` holds those evidence ids (E1, E2…). Product lines use the products' own words (names, materials, what the
  brand sells), e.g. "Wrinkle-free cotton shirts" citing the shirt products.
- A call to action, an invitation or a question states no fact and needs no citation ("Shop now", "Join the waitlist",
  "Would you try…?"). A proposal that names the brand or its products still cites them ("we'd bring our recycled bags"
  cites the product facts).
- No quality or superlative words the evidence doesn't itself use: premium, luxury, exclusive, best, finest, leading,
  high-quality, award-winning, iconic, ultimate, world-class.
- Never imply the brand is from, made for, inspired by, or already present in the city ("<brand> <city>",
  "<city>-inspired", "designed for the <city> …", "our <city> store"). The city may appear only in an invitation, such
  as "Join the <city> waitlist".
- Audience and founder items guide tone only; they never support a fact, and no line describes the audience's traits.
- Never state prices, discounts, budgets, reach, results or forecasts, or say a partner agreed to anything.
- Keep within every field's character limit. You only write; the founder reviews before anything is used.

Write for the person reading it:
- Ads: turn the facts into what a customer gets ("Wrinkle-free cotton shirts", "Bags made from recycled materials"),
  not a list of product names. Vary the headlines. A shipping or returns rule is never an ad line.
- Keywords: 2–4 word phrases a local searcher would type for what the brand sells (e.g. "wrinkle free dress shirts"),
  never single words and never the city alone.
- Email: write to the reader's own group or shop as "you": introduce the brand, propose the experiment to them, and
  ask the questions. Never narrate Landfall's internal plan ("we will identify a group…"), and never ask the reader to
  prove anything about themselves."""
COPY_CHECK = specialist.PROSE + (" These sentences are ad copy and a first email: a quality or superlative word (premium, "
                                 "exclusive, best…) and any claim that the brand is from, made for, inspired by or present in "
                                 "a place are statements of fact, and need their evidence like any other.")
CTAS = ("LEARN_MORE", "SHOP_NOW", "SIGN_UP")
MAX_IMAGES = 4


class SegmentIn(research.Lenient):
    text: str = ""
    cites: list[str] = Field(default_factory=list, description="Evidence ids (E1…) for every fact stated")


class EmailCopyIn(research.Lenient):
    subject: str = Field(description="Email subject, under 80 characters")
    paragraphs: list[SegmentIn] = Field(description="2 to 4 body paragraphs; no greeting name you don't know, no signature")


class AdCopyIn(research.Lenient):
    headlines: list[SegmentIn] = Field(description="3 to 15 headlines, each at most 30 characters")
    descriptions: list[SegmentIn] = Field(description="2 to 4 descriptions, each at most 90 characters")
    primary_text: SegmentIn = Field(description="The main ad text, at most 125 characters")
    ad_text: SegmentIn = Field(description="A short ad text, at most 100 characters")
    keywords: list[str] = Field(description="3 to 10 search keywords a local customer would type")
    cta: Literal["LEARN_MORE", "SHOP_NOW", "SIGN_UP"] = Field(description="The call to action")


LIMITS = {"headlines": 30, "descriptions": 90, "primary_text": 125, "ad_text": 100, "subject": 80}


LANGUAGES = json.loads((markets.DATA / "languages.json").read_text())["countries"]


def locale_of(city: dict) -> str:
    """The country's first official language (languages@1); a country not listed writes English and says so."""
    return f"{(LANGUAGES.get(city['country']) or ['en'])[0]}-{city['country']}"


def evidence_of(d: dict, dr: dict) -> list[dict]:
    """E-numbered evidence for the writer and the check, each with what it refers to: the dossier's checked city claims
    and the brief's SUPPORTED or STATED facts (claim ids), the brand's products (product ids), then Qloo audiences and the
    founder's words, which can inform a sentence but never support a fact."""
    ri = d["input"]["research_input"]
    claims = [{**db.get_record(i), "cid": i} for i in d["facts"]]
    ev = [{"role": "CLAIM", "text": f"{c['subject_name']}: {c['text']} (\"{c['quote']}\")", "claim_id": c["cid"]}
          for c in claims if c and c.get("state") == "SUPPORTED"]
    brief = db.get_record((ri.get("brief") or {}).get("id") or "")
    if brief:
        ev.append({"role": "BRAND_FACT", "text": f"The brand is {brief['brand_name']}", "claim_id": f"{brief['id']}:brand_name"})
    ev += [{"role": "BRAND_FACT", "text": f"{f['label']}: {f['value']}", "claim_id": f["claim_id"]} for f in ri["facts"]
           if f["state"] in ("SUPPORTED", "STATED")]
    ev += [{"role": "PRODUCT", "text": p["title"] + (f": {', '.join(p['category_terms'] + p['materials'])}"
                                                    if p["category_terms"] or p["materials"] else ""), "claim_id": p["id"]}
           for p in specialist._products(d["input"])]
    ev += [{**e, "claim_id": None} for e in specialist.bundle(d["input"], []) if e["role"] in ("QLOO", "FOUNDER")]
    if dr.get("you_said"):
        ev += [{"role": "FOUNDER", "text": f"{k}: {json.dumps(dr[k], ensure_ascii=False)[:300]}", "claim_id": None}
               for k in dr["you_said"]]
    return [{**e, "id": f"E{n}"} for n, e in enumerate(ev, 1)]


def copy_prompt(d: dict, dr: dict, ev: list[dict], kind: str, locale: str, seed: dict | None = None,
                style: dict | None = None) -> str:
    city = d["input"]["research_input"]
    part = ", ".join(p["name"] for p in dr["partners"])
    ask = (f"Write the first inquiry email to {part} (participation not confirmed): a subject and 2 to 4 paragraphs. "
           if part else "Write a first inquiry email the founder can send to a local partner they choose (no partner is "
           "named yet, so don't address one by name): a subject and 2 to 4 paragraphs. ") + \
        "Introduce the brand, propose the experiment, and ask the most important open questions. No signature." \
        if kind == "outreach" else ("Write the Google Search ad copy for one local test: headlines, descriptions, "
                                     "primary_text, ad_text, keywords and cta.")
    cues = style and [*style.get("themes", []), *style.get("audiences", [])]
    return (f"Language: {locale}\nCity: {city['name']}\nTask: {ask}\n"
            f"Experiment: {dr['concept']['name']}: {dr['concept']['line']}\n"
            f"Activities (proposals):\n" + "\n".join(f"- {a}" for a in dr["activities"]) + "\n"
            f"Partner (participation not confirmed): {part or 'none named yet'}\n"
            f"Audience hypothesis (not a fact): {dr['audience_hypothesis']['text']}\n"
            f"Questions for the partner (ask at most three, in your own words):\n"
            + ("\n".join(f"- {t['text']}" for t in dr["validation_tasks"] if t["from"] == "question")
               or "- whether they'd welcome the experiment, and what format and timing would suit them") + "\n"
            f"Evidence:\n" + "\n".join(f"- {e['id']} {e['role']}: {e['text']}" for e in ev)
            + ((f"\nStyle cues (Qloo: tastes and interests related to the brand's audience in {city['name']}; "
                f"{specialist.NOT_DEMAND}): {', '.join(cues)}. Let them shape the tone, the words and the imagery of "
                "the ads. They are never a fact: don't cite them, don't name or describe the audience, and never say "
                "anything about demand, popularity or how many people.") if cues and kind != "outreach" else "")
            + (("\nStart from this standard message (the founder's template). Keep its structure, its proposal and its "
                "questions, and make it more specific to this partner with what the evidence says, citing it. Leave out "
                "the closing line.\nSubject: " + ((seed.get("subject") or {}).get("text") or "") + "\n"
                + "\n".join((sg["text"] for sg in seed.get("segments", [])))) if seed else ""))


def copy_call(ctx: research.Ctx, prompt: str) -> dict:
    agent = Agent(model=make_model(ctx.run.cassette, "delivery_copy", research.usage_sink(ctx, "model"), max_tokens=1500,
                                   admit=research.admitter(ctx, "model"), model_id=config.NOVA_COPY_MODEL),
                  system_prompt=COPY_SYSTEM, callback_handler=None)
    model = EmailCopyIn if "inquiry email" in prompt else AdCopyIn
    out = agent(prompt + f"\nReturn {model.__name__}.", structured_output_model=model, cancel_signal=ctx.run.cancel).structured_output
    return out.model_dump() if out else {}


def judge_copy(ctx: research.Ctx, listing: str) -> dict:
    """One prose check of every segment (the 4.5 rubric), memoized; fails closed to {"error"}."""
    def go():
        try:
            agent = Agent(model=make_model(ctx.run.cassette, "delivery_copy", research.usage_sink(ctx, "check"),
                                           max_tokens=2000, admit=research.admitter(ctx, "check"),
                                           model_id=config.NOVA_COPY_MODEL),
                          system_prompt=COPY_CHECK, callback_handler=None)
            out = agent("Sort each sentence:\n" + listing, structured_output_model=research.ProseVerdicts,
                        cancel_signal=ctx.run.cancel).structured_output
        except (NoRecording, runner.Cancelled):
            raise
        except Exception as e:
            return {"error": type(e).__name__, "verdicts": []}
        return {"verdicts": [v.model_dump() for v in out.verdicts]} if out else {"error": "no answer", "verdicts": []}
    res, refused = research.memo(ctx, "prose", "copy", go)
    return res or {"error": refused, "verdicts": []}


def segments_from(sub: dict, ev: list[dict], kind: str, faults: list) -> dict:
    """Deterministic first: money and reach refused, limits enforced, citations resolved to claim ids."""
    by_id = {e["id"]: e for e in ev}

    def seg(s: dict | None, field: str, limit: int | None = None) -> dict | None:
        text = " ".join(((s or {}).get("text") or "").split())
        if not text:
            return None
        if specialist.money_or_reach(text):
            faults.append({"field": field, "reason": f"\"{text[:80]}\": money and reach figures are slice 5's; left out"})
            return None
        if limit and len(text) > limit:
            faults.append({"field": field, "reason": f"\"{text[:80]}\": over {limit} characters; left out"})
            return None
        cited = [by_id[c] for c in (s or {}).get("cites", []) if c in by_id]
        return {"field": field, "text": text, "cites": [e["id"] for e in cited],
                "claim_ids": [e["claim_id"] for e in cited if e["claim_id"]], "state": "UNCHECKED"}
    if kind == "outreach":
        subject = " ".join((sub.get("subject") or "").split())
        if specialist.money_or_reach(subject) or len(subject) > LIMITS["subject"]:
            faults.append({"field": "subject", "reason": f"\"{subject[:80]}\": over the limit or a figure; left out"})
            subject = ""
        segs = [seg(p, f"paragraphs[{i}]") for i, p in enumerate(sub.get("paragraphs") or [])]
        return {"subject": {"field": "subject", "text": subject, "cites": [], "claim_ids": [], "state": "UNCHECKED"} if subject else None,
                "segments": [s for s in segs if s]}
    out = {"headlines": [s for i, x in enumerate(sub.get("headlines") or []) if (s := seg(x, f"headlines[{i}]", 30))][:15],
           "descriptions": [s for i, x in enumerate(sub.get("descriptions") or []) if (s := seg(x, f"descriptions[{i}]", 90))][:4],
           "primary_text": seg(sub.get("primary_text"), "primary_text", 125), "ad_text": seg(sub.get("ad_text"), "ad_text", 100),
           "keywords": [{"text": k.strip().lower(), "match_type": "PHRASE"} for k in dict.fromkeys(sub.get("keywords") or [])
                        if k.strip() and len(k) <= 80 and not specialist.money_or_reach(k)][:10],
           "cta": sub.get("cta") if sub.get("cta") in CTAS else None}
    return out


def all_segments(copy: dict) -> list[dict]:
    segs = [copy.get("subject"), *copy.get("segments", []), *copy.get("headlines", []), *copy.get("descriptions", []),
            copy.get("primary_text"), copy.get("ad_text")]
    return [s for s in segs if s]


QUALITY = re.compile(r"\b(premium|luxur\w*|best|finest|leading|award[- ]?winning|high[- ]quality|top[- ]rated|world[- ]class|"
                     r"number one|#1|ultimate|unbeatable|iconic|exclusive)\b", re.I)
LOCAL_NOUNS = r"(?:store|shop|boutique|showroom|location|branch|outlet)s?"
IDIOM = re.compile(r"best\s+(?:way|ways|time|times|approach|option|options|fit|place|places|to)\b", re.I)


def guard(text: str, cited: list[dict], brand: str | None, city: str | None) -> str | None:
    """Deterministic, whatever the judge said (P6-I7): a quality word its cited evidence doesn't use, or wording that
    reads as a local presence the cited evidence doesn't show. Only statements count: a question claims nothing
    ("Are you based in Brisbane?", LIVE L6-A)."""
    text = " ".join(x for x in re.split(r"(?<=[.!?])\s+", text) if not x.rstrip().endswith("?"))
    seen = markets.fold(" ".join(e["text"] for e in cited))
    for m in QUALITY.finditer(text):
        if m.group(0).lower() == "best" and (IDIOM.match(text[m.start():]) or not re.search(
                r"(?:^|[.!?]\s+|\b(?:the|our|world'?s|city'?s))\s*$", text[:m.start()], re.I)):
            continue  # "best" claims only as "the/our best …" or a sentence opener: "work best", "the best way" don't (LIVE L6-A)
        if markets.fold(m.group(0)) not in seen:
            return f"\"{m.group(0)}\" is a quality claim its evidence doesn't make"
    if city:
        t, c, b = markets.fold(text), re.escape(markets.fold(city)), re.escape(markets.fold(brand or "")) if brand else None
        local = (b and re.search(rf"\b{b}\s+{c}\b|\b{c}\s+{b}\b", t)) or re.search(
            rf"\bour\s+{c}\b|\b{c}\s+{LOCAL_NOUNS}\b|\b{c}\s+(?:inspired|made|based|born|designed)\b|"
            rf"\b(?:inspired by|made in|based in|born in|designed (?:in|for)(?: the)?(?: \w+)?)\s+{c}\b", t)
        if local and not (markets.fold(city) in seen and (not brand or markets.fold(brand) in seen)):
            return "reads as a local presence its evidence doesn't show"
    return None


def check_segments(ctx: research.Ctx, copy: dict, ev: list[dict], partners: list[dict], city: str | None = None) -> None:
    """Each sentence is judged against the evidence it cites, and only that (P6-I7): SUPPORTED needs a judged fact that
    cites a checked claim or product; a proposal or question is PLAIN; anything else, unjudged, or caught by a guard is
    TO_CHECK (blocking until you edit it, remove it or keep it as yours)."""
    segs = all_segments(copy)
    if not segs:
        return
    by_id = {e["id"]: e for e in ev}
    brand = next((e["text"].removeprefix("The brand is ") for e in ev if e["text"].startswith("The brand is ")), None)
    items = [{"id": f"s{n}", "field": s["field"], "text": s["text"], "evidence": [by_id[c] for c in s["cites"] if c in by_id]}
             for n, s in enumerate(segs, 1)]
    res = judge_copy(ctx, specialist.prose_listing(items))
    keep = specialist.prose_keep(items, res["verdicts"], [{"name": p["name"]} for p in partners])
    kinds = {v["id"]: v["kind"] for v in res["verdicts"]}
    for it, s in zip(items, segs):
        if why := guard(s["text"], it["evidence"], brand, city):
            s.update(state="TO_CHECK", why=why)
        elif not keep[it["id"]]:
            s.update(state="TO_CHECK", why="states something its cited evidence doesn't" if it["evidence"]
                     else "states a fact without naming its evidence")
        elif kinds.get(it["id"]) != "SUPPORTED_FACT":
            s["state"] = "PLAIN"
        elif s["claim_ids"]:  # a fact resolves to a SUPPORTED or STATED claim or a product the brand sells (CONTRACTS B8)
            s["state"] = "SUPPORTED"
        else:
            s.update(state="TO_CHECK", why="states a fact without naming its evidence")


def image_of(run: Run, product: dict) -> str | None:
    """The product's own image address: its structured data or og:image, from the page Landfall already read."""
    page = fetch.get(product["product_url"], run.cassette, "delivery_fetch")
    body = page.get("body") or ""
    from .products import _ld_product
    img = _ld_product(body).get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    if isinstance(img, dict):
        img = img.get("url")
    if not img:
        m = re.search(r"""<meta[^>]+property=["']og:image(?::secure_url)?["'][^>]+content=["']([^"']+)""", body, re.I) or \
            re.search(r"""<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:image["']""", body, re.I)
        img = m and m.group(1)
    if not img:
        return None
    img = img.strip()
    return "https:" + img if img.startswith("//") else img


GIFT = re.compile(r"\b(?:e-?gift|gift ?cards?|gift ?vouchers?|vouchers?)\b", re.I)


def fetch_images(run: Run, pid: str, dr: dict) -> dict:
    """≤ 4 product images, each robots-checked; a failure is recorded as an attempt, never as "no image"."""
    attempts, refs, robots = [], [], {}
    for p in dr["products"]:
        if len(refs) >= MAX_IMAGES:
            break
        prod = db.get_record(p["id"], p["version"])
        if GIFT.search(prod["title"]):  # LIVE L6-A: a gift-card graphic attached as if it were a bag
            attempts.append({"product": prod["title"], "outcome": "SKIPPED", "reason": "a gift card, not a product to show"})
            continue
        try:
            url = image_of(run, prod)
        except (fetch.FetchBlocked, ValueError) as e:
            attempts.append({"product": prod["title"], "outcome": "FAILED", "reason": str(e)[:120]})
            continue
        if not url:
            attempts.append({"product": prod["title"], "outcome": "EMPTY", "reason": "no image on the product page"})
            continue
        host = re.sub(r"^https?://", "", url).split("/")[0]
        if host not in robots:
            robots[host] = fetch.robots_for(url, run.cassette, "delivery_fetch")
        if not fetch.robots_allows(url, robots[host]):
            attempts.append({"product": prod["title"], "url": url, "outcome": "REFUSED", "reason": "robots.txt"})
            continue
        got = fetch.get_bytes(url, run.cassette, "delivery_fetch")
        ctype = (got.get("content_type") or "").split(";")[0].strip()
        data = base64.b64decode(got.get("b64") or "")
        if got.get("status") != 200 or ctype not in IMAGE_TYPES or not data:
            attempts.append({"product": prod["title"], "url": url, "outcome": "FAILED",
                             "reason": f"HTTP {got.get('status')} {ctype or got.get('error') or ''}".strip()})
            continue
        ext = "png" if ctype == "image/png" else "jpg"
        ref = save_artifact(pid, data, f"{re.sub(r'[^a-z0-9]+', '-', prod['title'].lower()).strip('-')[:50]}.{ext}", ctype,
                            {"kind": "FETCHED", "url": got.get("url") or url, "fetched_at": db.now(),
                             "page": prod["product_url"]},
                            "brand_own_unconfirmed",
                            ["Your own product image from your site; confirm you hold the rights before any public use"])
        refs.append({"artifact": ref, "product": prod["title"]})
        attempts.append({"product": prod["title"], "url": url, "outcome": "OK"})
    return {"refs": refs, "attempts": attempts}


def destinations_for(dr: dict) -> list[str]:
    if dr["source"]["method"] in OUTREACH:
        return ["EMAIL"]
    ch = dr.get("digital_outline", {}).get("channel", {})
    out = []
    for c in (ch.get("primary"), ch.get("alternative")):
        p = specialist.channel_platform(c or "")
        d = {"Meta": "META_ADS", "Google": "GOOGLE_ADS", "TikTok": "TIKTOK_ADS"}.get(p or "")
        if d and d not in out:
            out.append(d)
    return out or ["META_ADS"]


def prepare(pid: str, draft_id: str, base_version: int | None, partner_lead_id: str | None = None, launch: bool = False) -> dict:
    """A package for one (draft, recipient) [P65-R2], prepared from the signed plan's pinned draft version [P65-R4] and
    refused by the earlier-plan fence [P65-R1]. Without a beachhead or plan (the pre-6.5 route) it is the draft's
    latest version and its first kept partner, as before."""
    from . import plans
    dr = db.get_record(draft_id)
    if not dr or dr["project_id"] != pid or dr["kind"] != "draft":
        raise Refused(404, "No such draft.")
    cid, method = dr["source"]["city_id"], dr["source"]["method"]
    if why := plans.fence(pid, cid, method):
        raise Refused(409, why)
    pin, plan_ref = plans.pin_of(pid, cid, method)
    version = pin["draft"]["version"] if pin and pin["draft"]["id"] == draft_id else dr["version"]
    if base_version is not None and base_version != version:
        raise Refused(409, f"The plan uses draft v{version}." if pin else "The draft changed since you opened it.")
    lead = None
    if partner_lead_id:
        if method not in OUTREACH:
            raise Refused(422, "Only outreach goes to a chosen recipient.")
        lead = db.get_record(partner_lead_id)
        d = db.get_record(dr["source"]["dossier_id"], db.get_record(draft_id, version)["source"]["dossier_version"])
        c = lead and lead["project_id"] == pid and next((c for c in d["candidates"] if c.get("ref") == lead["key"]), None)
        standalone = bool(lead) and lead["project_id"] == pid and not c and lead.get("city_id") == cid and \
            lead_method(lead) == method  # P66-R2: a founder-added or provider-found lead, not one of the candidates
        if not standalone and (not c or c["decision"] not in ("KEPT", "PENDING")):
            raise Refused(422, "Choose a kept or pending candidate of the investigation this plan uses, or a contact you added.")
    pkg_id = package_id_for(draft_id, lead and lead["key"])
    cur = db.get_record(pkg_id)
    if cur and cur.get("status") == "set_aside" and cur["source"]["version"] == version:  # chosen again: reactivated
        db.put_record("package", pid, {**_payload(cur), "status": "active", "set_aside": None}, inputs=cur["inputs"], rid=pkg_id)
        return {"run_id": None, "package_id": pkg_id}
    rk = lead and specialist._k(lead["key"])
    key = f"{draft_id}:{version}" if not (rk or plan_ref) else f"{draft_id}:{version}:{rk or '-'}:{(plan_ref or {}).get('version', '-')}"
    rid = runner.start(pid, "delivery_prepare", key + ":launch" * launch,
                       {"draft_id": draft_id, "draft_version": version, "package_id": pkg_id,
                        "partner_lead_id": partner_lead_id, "plan_ref": plan_ref, **({"launch": True} if launch else {})})
    return {"run_id": rid, "package_id": pkg_id}


def package_id_of(draft_id: str) -> str:
    return f"pkg_{draft_id[4:]}"


def partner_key(pkg: dict) -> str | None:
    """A legacy partnered package's recipient key, read from its Lead when it predates `lead_key` [P65-R2]."""
    p = pkg.get("partner") or {}
    return p.get("lead_key") or (p.get("lead_id") and (db.get_record(p["lead_id"]) or {}).get("key"))


def package_id_for(draft_id: str, lead_key: str | None) -> str:
    """Stable per recipient across investigations: `pkg_{draft key}~{rk}`. The unsuffixed legacy package is adopted
    when its partner is that recipient; with no recipient it stays the method's "recipient you entered" package."""
    base = package_id_of(draft_id)
    if not lead_key:
        return base
    legacy = db.get_record(base)
    if legacy and partner_key(legacy) == lead_key:
        return base
    return f"{base}~{specialist._k(lead_key)}"


def packages_of(pid: str, cid: str | None = None, method: str | None = None) -> list[dict]:
    """The latest version of every package in the project (optionally one city and method)."""
    seen: dict = {}
    for r in db.records("package", pid):
        seen[r["id"]] = r
    return [p for p in seen.values() if (cid is None or p["city_id"] == cid) and (method is None or p["method"] == method)]


def prepare_run(run: Run):
    pid, did, dv = run.project_id, run.params["draft_id"], run.params["draft_version"]
    dr = db.get_record(did, dv)
    d = db.get_record(dr["source"]["dossier_id"], dr["source"]["dossier_version"])
    city = markets.CITIES[d["city_id"]]
    kind = "outreach" if dr["source"]["method"] in OUTREACH else "digital"
    pkg_id = run.params.get("package_id") or package_id_of(did)
    prev = db.get_record(pkg_id)
    lead = run.params.get("partner_lead_id") and db.get_record(run.params["partner_lead_id"])
    rk = lead and specialist._k(lead["key"])
    content = dict(dr)  # what the writer sees: the chosen recipient, and the products still selected [P65-R5, R6]
    if lead:
        content["partners"] = [{"lead_id": lead["id"], "name": lead["name"], "participation": "NOT_CONFIRMED"}]
    if prev and prev.get("products_by") == "YOU_SAID":
        keep = {p["id"] for p in prev["products"]}
        content["products"] = [p for p in dr["products"] if p["id"] in keep]
    ctx = research.Ctx(run, pid, d["city_id"], dv, d["input"]["research_input"], caps=COPY_CAPS, usd_cap=COPY_USD,
                       expected_usd=COPY_EXPECTED, forced_usd=0.0, stage="delivery_copy",
                       cr_key=f"{pid}:{did}:{dv}:delivery" + (f":{rk}" if rk else ""))  # L6 keys unchanged without a recipient
    ev = evidence_of(d, dr)
    locale = locale_of(city)
    research._activity(run, f"Prepare {'outreach' if kind == 'outreach' else 'the campaign'} for {city['name']}",
                       "delivery copy", f"writing in {locale}", status="RUNNING")
    partner = lead and partner_of(pid, lead, d)
    if run.params.get("launch") and kind == "outreach":
        return launch_outreach(run, ctx, dr, d, content, ev, locale, pkg_id, partner)
    style = qloo_style(pid, d["city_id"]) if kind != "outreach" else None
    pre = kind != "outreach" and written_ads(did, dv, [p["id"] for p in content["products"]])
    if pre:  # written as the plan was built (draft_ads): adopted, never written again
        got, style = pre["got"], pre["style"]
    else:
        sub = run.step("copy", lambda: copy_call(ctx, copy_prompt(d, content, ev, kind, locale, style=style)))
        if kind != "outreach":
            sub = fill_ad_copy(sub, (db.latest("brief", pid) or {}).get("brand_name") or "",
                               [(db.get_record(x["id"], x.get("version")) or {}).get("title") or "" for x in content["products"]], city["name"])
        got = run.step("check", lambda: checked_copy(ctx, sub, ev, kind, content["partners"], city["name"]))
    imgs = run.step("images", lambda: fetch_images(run, pid, content))
    rec = run.step("publish", lambda: publish_package(pid, dr, d, kind, locale, got, imgs, ev, pkg_id, partner,
                                                       run.params.get("plan_ref"), [p["id"] for p in content["products"]],
                                                       launch=bool(run.params.get("launch")),
                                                       extra={"ad_style": style} if style else None))
    if run.params.get("launch"):
        run.step("settings", lambda: apply_settings(pid, d["city_id"], [pkg_id]))
    research._activity(run, f"Prepare {'outreach' if kind == 'outreach' else 'the campaign'} for {city['name']}",
                       "delivery copy", f"package v{rec['version']} · {len(imgs['refs'])} images saved")


HANDLERS["delivery_prepare"] = prepare_run


def fill_ad_copy(sub: dict, brand: str, titles: list[str], city: str) -> dict:
    """Google needs 3+ headlines (≤ 30 characters) and 2+ descriptions (≤ 90). The model often writes them too long and
    they're dropped (LIVE 28 Oct 2026: 2 headlines left, campaign blocked). Top up from the brand's own records; the
    check below still labels every line."""
    def fits(xs, n):
        return [x for x in xs or [] if 0 < len(" ".join(((x or {}).get("text") or "").split())) <= n]
    hs, ds = list(sub.get("headlines") or []), list(sub.get("descriptions") or [])
    have = {" ".join((x.get("text") or "").split()).lower() for x in hs + ds if isinstance(x, dict)}
    pool = [t for t in dict.fromkeys([brand, f"Shop {brand} online", f"{brand} in {city}", *titles]) if t and len(t) <= 30]
    for t in pool:
        if len(fits(hs, 30)) >= 3:
            break
        if t.lower() not in have:
            hs.append({"text": t}); have.add(t.lower())
    named = ", ".join(t for t in titles[:3] if t)
    for t in [f"{brand}: {named}." if named else "", f"Shop {brand} online."]:
        if len(fits(ds, 90)) >= 2:
            break
        if t and len(t) <= 90 and t.lower() not in have:
            ds.append({"text": t}); have.add(t.lower())
    return {**sub, "headlines": hs, "descriptions": ds}


STYLE_TYPES = ("Lifestyle Preferences Beliefs", "Hobbies And Interests", "Communities", "Global Issues")  # aesthetic, values, lifestyle
STYLE_SKIP = ("urn:tag:genre:place", "urn:tag:industry")  # "Bar", "Hotel": where, not a taste


def qloo_style(pid: str, cid: str, run: Run | None = None) -> dict | None:
    """What the ads are styled from (owner, 28 Oct 2026): the brand's taste tags in this city (`urn:tag` with
    `signal.location`, the Qloo target's themes) and the brand's lifestyle, values and hobby audiences. Tone, words and
    setting only, never demand or audience size. Stored answers first; with a run and no stored themes, one request."""
    t = db.get_record(f"qt_{pid}_{cid}") or db.get_record(f"qs_{pid}_{cid}") or {}
    themes = [x["name"] for x in t.get("themes") or [] if x.get("subtype") not in STYLE_SKIP]
    if not t.get("themes") and run:
        themes = _style_themes(run, pid, cid)
    ap = db.latest("audience_profile", pid) or {}
    live = [a for a in ap.get("audiences") or [] if a["uuid"] not in ap.get("removed", [])]
    auds = [a["name"] for a in ([a for a in live if a.get("type") in STYLE_TYPES] or live)][:4]
    people = [a["name"] for a in live if a.get("type") in specialist.TARGET_TYPES][:3]  # who's in the ad scene: racing, gamers
    return {"themes": themes[:4], "audiences": auds, "people": people} if themes or auds else None


def _style_themes(run: Run, pid: str, cid: str) -> list[str]:
    """The Qloo target's taste request, made once for a city that has none (the same parameters, so its cache serves both)."""
    from . import qloo, ranking
    sig, city = ranking.ranking_signal(pid), markets.CITIES[cid]
    if not sig:
        return []
    try:
        out = qloo.get("/v2/insights", {"filter.type": "urn:tag", "signal.interests.entities": ",".join(sig["entity_ids"]),
                                        "signal.location": f"POINT({city['lon']} {city['lat']})", "take": 20},
                       run.cassette, "ad_style", pid, run.id)
    except (runner.Cancelled, NoRecording):
        raise
    except Exception:  # noqa: BLE001  the ads are written without themes
        return []
    tags = [{"name": (x.get("name") or "").strip(), "subtype": x.get("subtype") or x.get("type") or ""} for x in qloo.rows(out)[1]]
    tags = [x for x in tags if x["name"] and x["subtype"] not in specialist.THEME_SKIP][:8]
    db.put_record("qloo_style", pid, {"city_id": cid, "themes": tags, "caveat": specialist.NOT_DEMAND}, rid=f"qs_{pid}_{cid}")
    return [x["name"] for x in tags if x["subtype"] not in STYLE_SKIP]


def ad_copy_id(draft_id: str) -> str:
    return f"adc_{draft_id[4:]}"


def written_ads(draft_id: str, version: int, products: list[str]) -> dict | None:
    """The ad copy draft_ads wrote for exactly this draft version and these products, by the current writer."""
    r = db.get_record(ad_copy_id(draft_id))
    return r if r and r["draft"] == {"id": draft_id, "version": version} and r["products"] == sorted(products) \
        and r["copy_version"] == COPY_VERSION else None


def draft_ads(run: Run, pid: str, cid: str) -> dict | None:
    """Owner, 28 Oct 2026: "each time we will write ads … front load that for when they navigate to the initial plan
    page." The Google Search ad copy is written and checked as the plan is built, right after the digital draft, and kept
    beside it (`adc_…`). Nothing is prepared or sent: the Launch package still comes only from the signed plan
    (plans.fence) and adopts this copy when it pins the same draft version and products (prepare_run)."""
    dr = db.get_record(f"drf_{pid}_{cid}_digital")
    if not dr:
        return None
    prods = [p["id"] for p in dr["products"]]
    if cur := written_ads(dr["id"], dr["version"], prods):
        return {"id": cur["id"], "version": cur["version"]}
    d = db.get_record(dr["source"]["dossier_id"], dr["source"]["dossier_version"])
    city, locale = markets.CITIES[cid], locale_of(markets.CITIES[cid])
    style = qloo_style(pid, cid, run)
    ctx = research.Ctx(run, pid, cid, dr["version"], d["input"]["research_input"], caps=COPY_CAPS, usd_cap=COPY_USD,
                       expected_usd=COPY_EXPECTED, forced_usd=0.0, stage="delivery_copy",
                       cr_key=f"{pid}:{dr['id']}:{dr['version']}:ads")
    ev = evidence_of(d, dr)
    research._activity(run, f"Write the Google ads for {city['name']}", "delivery copy", f"writing in {locale}", status="RUNNING")
    sub = fill_ad_copy(copy_call(ctx, copy_prompt(d, dr, ev, "digital", locale, style=style)),
                       (db.latest("brief", pid) or {}).get("brand_name") or "", [p["title"] for p in dr["products"]], city["name"])
    got = checked_copy(ctx, sub, ev, "digital", dr["partners"], city["name"])
    rec = db.put_record("ad_copy", pid, {"city_id": cid, "draft": {"id": dr["id"], "version": dr["version"]},
                                         "products": sorted(prods), "locale": locale, "copy_version": COPY_VERSION,
                                         "check_version": CHECK_VERSION, "got": got, "style": style},
                        inputs=[{"id": dr["id"], "version": dr["version"], "keys": []}], rid=ad_copy_id(dr["id"]))
    research._activity(run, f"Write the Google ads for {city['name']}", "delivery copy",
                       f"{len(got['copy']['headlines'])} headlines · {len(got['copy']['descriptions'])} descriptions")
    return {"id": rec["id"], "version": rec["version"]}


def checked_copy(ctx: research.Ctx, sub: dict, ev: list[dict], kind: str, partners: list[dict], city: str) -> dict:
    faults: list = []
    c = segments_from(sub, ev, kind, faults)
    check_segments(ctx, c, ev, partners, city)
    return {"copy": c, "faults": faults}


def launch_outreach(run: Run, ctx: research.Ctx, dr: dict, d: dict, content: dict, ev: list[dict], locale: str,
                    pkg_id: str, partner: dict | None):
    """The Launch path (owner, 28 Oct 2026): the standard template, filled from records, is published first so the
    message is on the checklist at once; then one model pass tailors it (checked like any copy) and the product photos
    are saved. A failed pass leaves the template message as it is; nothing waits on it."""
    pid, cid, method = run.project_id, d["city_id"], dr["source"]["method"]
    t = next(x for x in templates(pid) if x["id"] == f"tpl_{pid}_{method}_0")
    sender = launch_settings(pid, cid)["sender_name"]
    first = run.step("template", lambda: {"copy": template_copy(pid, cid, dr, partner and partner["name"], content["products"],
                                                                sender, t), "faults": []})
    run.step("publish", lambda: publish_package(
        pid, dr, d, "outreach", locale, first, {"refs": [], "attempts": []}, ev, pkg_id, partner, run.params.get("plan_ref"),
        [p["id"] for p in content["products"]], launch=True,
        extra={"template_ref": {"id": t["id"], "version": t["version"]}, "copy_origin": "TEMPLATE"}))
    run.step("settings", lambda: apply_settings(pid, cid, [pkg_id]))
    attach_picked_ad(pid, cid)  # an ad picked before this email was prepared goes with it too
    got = imgs = None
    try:
        sub = run.step("copy", lambda: copy_call(ctx, copy_prompt(d, content, ev, "outreach", locale, seed=first["copy"])))
        got = run.step("check", lambda: checked_copy(ctx, sub, ev, "outreach", content["partners"], markets.CITIES[cid]["name"]))
    except runner.Cancelled:
        raise
    except Exception as e:  # noqa: BLE001  the template message stands
        research._activity(run, "Tailor the message", "delivery copy", f"kept the standard message ({type(e).__name__})")
    try:
        imgs = run.step("images", lambda: fetch_images(run, pid, content))
    except runner.Cancelled:
        raise
    except Exception:  # noqa: BLE001  photos are optional; the attempt is visible as none saved
        imgs = None
    run.step("enrich", lambda: enrich_package(pid, pkg_id, got, imgs))
    attach_picked_ad(pid, cid)


def enrich_package(pid: str, pkg_id: str, got: dict | None, imgs: dict | None) -> dict:
    """v+1 with the tailored copy and the saved photos, unless the founder already edited the message or approved,
    sent or recorded it (then the photos are still added only if nothing was approved yet)."""
    cur = db.get_record(pkg_id)
    touched = bool(approvals(pid, pkg_id, "EMAIL") or latest_actions(pid, pkg_id, "EMAIL") or manuals_of(pid, pkg_id, "EMAIL"))
    if touched or not cur:
        return {"id": pkg_id, "version": cur and cur["version"]}
    p, changed = _payload(cur), False
    if got and got["copy"].get("segments") and not {"subject", "segments"} & set(cur.get("you_said", [])):
        p["copy"] = {**p["copy"], **{k: v for k, v in got["copy"].items() if v}}
        p["faults"], p["copy_origin"], changed = got["faults"], "ENRICHED", True
        p["claim_versions"] = {c: (db.get_record(c) or {}).get("version") for c in
                               {i for sg in all_segments(p["copy"]) for i in sg["claim_ids"]} if db.get_record(c)}
    if imgs and imgs["refs"]:
        have = {a["artifact"]["sha256"] for a in p["assets"]} | set(p.get("excluded", []))
        new = [{"artifact": r["artifact"], "role": "ATTACHMENT", "alt_text": r["product"]} for r in imgs["refs"]
               if r["artifact"]["sha256"] not in have]
        if new:
            p["assets"], p["asset_attempts"], changed = [*p["assets"], *new], imgs["attempts"], True
    if not changed:
        return {"id": pkg_id, "version": cur["version"]}
    rec = db.put_record("package", pid, p, inputs=cur["inputs"], rid=pkg_id)
    return {"id": rec["id"], "version": rec["version"]}


def lead_method(lead: dict) -> str | None:
    return "creator" if lead.get("creator") else {"shop": "retail", "venue": "popup", "group": "community"}.get(lead.get("type"))


def partner_of(pid: str, lead: dict, d: dict) -> dict:
    """B10 partner: KEPT, or YOUR_CHOICE for a PENDING candidate (or a provider-found lead) the founder chose, or
    YOU_SAID for one they added; participation never confirmed. `from_candidate` decides what staleness means."""
    c = next((c for c in d["candidates"] if c.get("ref") == lead["key"]), {})
    ct = lead.get("contact") or {}
    fc = db.get_record(f"fc_{pid}_{research._key(lead['key'])}")  # a contact the founder added (YOU SAID)
    contact = {"value": ct["value"], "source_id": ct.get("source_id"),
               **{k: ct[k] for k in ("source", "email_status") if ct.get(k)}} if ct.get("kind") == "email" else \
        {"value": fc["value"], "by": "founder"} if fc and ADDR.match(fc["value"]) else None
    return {"lead_id": lead["id"], "lead_key": lead["key"], "name": lead["name"], "from_candidate": bool(c),
            "basis": "YOU_SAID" if lead.get("origin") == "YOU_SAID" else "KEPT" if c.get("decision") == "KEPT" else "YOUR_CHOICE",
            "participation": "NOT_CONFIRMED", "contact": contact}


def refresh_contact(pid: str, lead: dict) -> list[str]:
    """A contact found after the package was prepared (6.6 Find contact) [P66-I2]: each active package for that
    recipient becomes v+1 with it as To, unless you set To yourself; no copy call, and the approval is outdated."""
    out = []
    for pkg in packages_of(pid, lead.get("city_id")):
        part = pkg.get("partner") or {}
        if pkg.get("status", "active") != "active" or part.get("lead_key") != lead["key"]:
            continue
        d = db.get_record(db.get_record(pkg["source"]["id"], pkg["source"]["version"])["source"]["dossier_id"])
        new = partner_of(pid, lead, d)
        if new["contact"] == part.get("contact"):
            continue
        p = {**_payload(pkg), "partner": {**part, "contact": new["contact"]}}
        if new["contact"] and "to" not in pkg.get("you_said", []):
            p["recipients"] = {**pkg["recipients"], "to": [new["contact"]["value"]]}
            p["recipients_by"] = {**pkg.get("recipients_by", {}), "to": "SOURCE"}
        out.append(db.put_record("package", pid, p, inputs=pkg["inputs"], rid=pkg["id"])["id"])
    return out


def publish_package(pid: str, dr: dict, d: dict, kind: str, locale: str, got: dict, imgs: dict, ev: list[dict],
                    pkg_id: str | None = None, partner: dict | None = None, plan_ref: dict | None = None,
                    wrote_for: list[str] | None = None, launch: bool = False, extra: dict | None = None) -> dict:
    city = markets.CITIES[d["city_id"]]
    brief = db.latest("brief", pid) or {}
    project = db.q("SELECT url FROM projects WHERE id=?", pid)[0]
    pkg_id = pkg_id or package_id_of(dr["id"])
    if not partner and dr["partners"]:  # the pre-6.5 route: the draft's first kept partner
        p0 = dr["partners"][0]
        lead = db.get_record(p0["lead_id"]) if p0.get("lead_id") else None
        c = (lead or {}).get("contact") or {}
        partner = {"lead_id": p0.get("lead_id"), "lead_key": (lead or {}).get("key"), "name": p0["name"], "basis": "KEPT",
                   "from_candidate": True,
                   "participation": "NOT_CONFIRMED",
                   "contact": {"value": c["value"], "source_id": c.get("source_id")} if c.get("kind") == "email" else None}
    copy = {"locale": locale, **got["copy"]}
    dg = dr.get("digital_outline") or {}
    audience = d["input"]["research_input"].get("audience") or {}
    qloo = [t["element"] for t in dg.get("targeting_description", []) if t["basis"] == "QLOO_DERIVED"]
    qloo += [a["name"] for a in (audience.get("offered") or []) if a["name"] not in qloo]
    prev = db.get_record(pkg_id)
    plan_budget, selection = from_signed_plan(plan_ref, dr["source"]["method"])
    payload = {
        "source": {"kind": "experiment_draft", "id": dr["id"], "version": dr["version"]},
        "city_id": d["city_id"], "method": dr["source"]["method"], "flow": kind, "concept": dr["concept"],
        "destinations": list(plan_budget["allocations"]) if plan_budget else destinations_for(dr),
        "products": [{"id": p["id"], "version": p["version"], "title": p["title"]} for p in dr["products"]],
        "partner": partner,
        "recipients": {"to": [partner["contact"]["value"]] if partner and partner["contact"] else [], "cc": [], "bcc": []},
        "recipients_by": {"to": ("YOU_SAID" if partner["contact"].get("by") == "founder" else "SOURCE")
                          if partner and partner["contact"] else None},
        "from_name": None,
        "assets": [{"artifact": r["artifact"], "role": "ATTACHMENT" if kind == "outreach" else "CREATIVE",
                    "alt_text": r["product"]} for r in imgs["refs"]],
        "asset_attempts": imgs["attempts"],
        "copy": copy, "copy_version": COPY_VERSION, "check_version": CHECK_VERSION, "copy_model": config.NOVA_COPY_MODEL, "faults": got["faults"],
        "evidence": [{k: e[k] for k in ("id", "role", "text", "claim_id")} for e in ev],
        "audience_rationale": {"hypothesis": dr["audience_hypothesis"]["text"], "caveat": dr["audience_hypothesis"].get("caveat"),
                               "qloo_interests": qloo, "label": "QLOO · informs the recommendation; never sent as targeting"},
        "targeting": {"geo": {"city_id": d["city_id"], "name": city["name"], "country": city["country"],
                              "lat": city["lat"], "lon": city["lon"],
                              "radius_km": (dg.get("geography") or {}).get("radius_km_hypothesis"),
                              "radius_by": "HYPOTHESIS" if (dg.get("geography") or {}).get("radius_km_hypothesis") else None},
                      "platform_interests": [{"name": t["element"], "basis": "PLATFORM_OPTION" if t["basis"] == "PLATFORM_OPTION" else "TO_CHECK",
                                              "sent": False} for t in dg.get("targeting_description", []) if t["basis"] != "QLOO_DERIVED"]},
        "budget": plan_budget or dict(NO_BUDGET),
        "timing": {"start": None, "end": None},
        "creative": {"display_name": brief.get("brand_name"), "cta": copy.get("cta"),
                     "landing_url": project["url"], "landing_by": "DERIVED"},
        "settings": {"objective": dg.get("objective"), "currency_hint": dg.get("currency")},
        "rights_confirmed": [],
        "selection": selection,
        "sources": sorted({e["claim_id"] for e in ev if e["claim_id"]}),
        "assumptions": [t["text"] for t in dr["validation_tasks"]],
        "you_said": [], "vc": "I", "status": "active", "plan_ref": plan_ref, "excluded": [],
        "copy_for": sorted(wrote_for if wrote_for is not None else [p["id"] for p in dr["products"]]),
        "claim_versions": {c: (db.get_record(c) or {}).get("version") for c in
                           {i for s in all_segments(copy) for i in s["claim_ids"]} if db.get_record(c)}, **(extra or {})}
    if launch and kind == "digital":  # owner, 28 Oct 2026: the Launch checklist's ads are Google Ads only (Meta, TikTok stay in code)
        payload["destinations"] = ["GOOGLE_ADS"]
    if prev and prev["source"] == payload["source"] and \
            prev.get("copy_for", sorted(p["id"] for p in dr["products"])) == payload["copy_for"]:
        # P65-I3: the same draft version and products. Only the signed plan or the recipient's contact can be new;
        # either is a new version (outdating any approval) with the founder's copy as it stands.
        upd = {"plan_ref": plan_ref} if plan_ref and prev.get("plan_ref") != plan_ref else {}
        if plan_ref and prev.get("selection") != selection:
            upd["selection"] = selection
        new_budget = {**(plan_budget or NO_BUDGET), "max_cpc": prev["budget"].get("max_cpc")}  # §5.10: re-prepare takes the signed budget, or its absence (REVIEW-SLICE-05 R1)
        if plan_ref and prev["budget"].get("source") != "YOU_SAID" and prev["budget"] != new_budget:
            upd["budget"] = new_budget
            if plan_budget:
                upd["destinations"] = list(plan_budget["allocations"])
        if partner and partner != prev.get("partner"):
            upd["partner"] = partner
            if partner["contact"] and "to" not in prev.get("you_said", []):
                upd["recipients"] = {**prev["recipients"], "to": [partner["contact"]["value"]]}
                upd["recipients_by"] = {**prev.get("recipients_by", {}), "to": payload["recipients_by"]["to"]}
        if not upd:
            return {"id": prev["id"], "version": prev["version"]}  # nothing new to write
        rec = db.put_record("package", pid, {**_payload(prev), **upd}, inputs=prev["inputs"], rid=pkg_id)
        return {"id": rec["id"], "version": rec["version"]}
    if prev:
        carry_forward(prev, payload)
    rec = db.put_record("package", pid, payload, inputs=[{"id": dr["id"], "version": dr["version"], "keys": []}], rid=pkg_id)
    return {"id": rec["id"], "version": rec["version"]}


COPY_FIELDS = ("subject", "segments", "headlines", "descriptions", "primary_text", "ad_text", "keywords")


def carry_forward(prev: dict, p: dict) -> None:
    """Re-preparing from a newer draft version [P65-R5]: the founder's independent decisions are carried with the
    provenance they had; copy is regenerated, and wording the founder had edited is kept beside the new text for an
    explicit Keep mine or Use the new text. Approvals never carry (a new version needs a new approval)."""
    p["from_name"] = prev.get("from_name")
    if prev["budget"].get("source") == "YOU_SAID" or not p.get("plan_ref"):
        p["budget"] = prev["budget"]  # a YOU SAID budget is kept (flagged when it differs); from a signed plan, its budget or none
    p["budget"] = {**p["budget"], "max_cpc": prev["budget"].get("max_cpc")}  # the founder's own bid cap stays theirs
    p["timing"] = prev["timing"]
    p["targeting"]["geo"].update({k: prev["targeting"]["geo"].get(k) for k in ("radius_km", "radius_by")})
    p["creative"] = {**p["creative"], **{k: prev["creative"].get(k) for k in ("landing_url", "landing_by", "display_name", "cta")}}
    if set(prev["destinations"]) <= set(DESTINATIONS) and ("EMAIL" in prev["destinations"]) == (p["flow"] == "outreach") and \
            (p["budget"].get("source") != "PLAN" or "destinations" in prev.get("you_said", [])):
        p["destinations"] = prev["destinations"]
    p["recipients"], p["recipients_by"] = prev["recipients"], prev.get("recipients_by", {})  # same package id, same recipient
    p["excluded"] = prev.get("excluded", [])
    uploads = [a for a in prev["assets"] if (db.get_record(a["artifact"]["id"]) or {}).get("source", {}).get("kind") == "FOUNDER_UPLOAD"]
    p["assets"] = [a for a in p["assets"] if a["artifact"]["sha256"] not in p["excluded"]] + uploads
    have = {a["artifact"]["sha256"] for a in p["assets"]}
    p["rights_confirmed"] = [s for s in prev.get("rights_confirmed", []) if s in have]
    if prev.get("products_by") == "YOU_SAID":
        keep = {x["id"] for x in prev["products"]}
        dropped = [x["title"] for x in prev["products"] if x["id"] not in {y["id"] for y in p["products"]}]
        p["products"], p["products_by"] = [x for x in p["products"] if x["id"] in keep], "YOU_SAID"
        p["notices"] = [f"No longer in the draft: {t}" for t in dropped]
    yours = set(prev.get("you_said", []))
    if "confirm" in yours:
        yours |= {s["field"].split("[")[0].replace("paragraphs", "segments") for s in all_segments(prev["copy"])
                  if s.get("confirmed_by") == "founder"}
    p["earlier_wording"] = {**(prev.get("earlier_wording") or {}),  # unresolved until Keep mine / Use new [P65-I5]
                            **{f: prev["copy"].get(f) for f in COPY_FIELDS if f in yours and prev["copy"].get(f) != p["copy"].get(f)}}
    p["you_said"] = sorted(yours & {"from_name", "budget", "max_cpc", "start", "end", "radius_km", "landing_url",
                                    "display_name", "cta", "destinations", "to", "cc", "bcc", "rights_confirmed",
                                    "remove_assets", "products", "assets"})


def choose_wording(pid: str, pkg_id: str, base_version: int, field: str, choice: str) -> dict:
    """POST …/packages/{id}/wording: Keep mine (YOU SAID, re-checked by the deterministic guards) or Use the new text."""
    cur = _current(pid, pkg_id, base_version)
    p = _payload(cur)
    if field not in (p.get("earlier_wording") or {}) or choice not in ("mine", "new"):
        raise Refused(422, "There is no earlier wording of that field to choose.")
    mine = p["earlier_wording"].pop(field)
    if choice == "mine":
        p["copy"][field] = mine
        brand = (db.latest("brief", pid) or {}).get("brand_name")
        for s in all_segments({field: mine}):
            if why := guard(s["text"], [], brand, markets.CITIES[p["city_id"]]["name"]):
                s.update(state="TO_CHECK", why=why)
        p["you_said"] = sorted(set(p.get("you_said", [])) | {field})
    rec = db.put_record("package", pid, p, inputs=cur["inputs"], rid=pkg_id)
    return {"version": rec["version"]}


def set_aside(pid: str, pkg_id: str, base_version: int, reason: str) -> dict:
    """Deselected or replaced: v+1, status set_aside. Its history, actions and execution slot stay [P65-R2]."""
    if reason not in ("deselected", "replaced", "method_removed"):
        raise Refused(422, "Give a reason: deselected, replaced or method_removed.")
    cur = _current(pid, pkg_id, base_version)
    rec = db.put_record("package", pid, {**_payload(cur), "status": "set_aside", "set_aside": {"at": db.now(), "reason": reason}},
                        inputs=cur["inputs"], rid=pkg_id)
    db.event(pid, "founder", {"stage": "Act", "task": "Set a package aside", "detail": f"{pkg_id} ({reason})"})
    return {"version": rec["version"]}


def _current(pid: str, pkg_id: str, base_version: int) -> dict:
    cur = db.get_record(pkg_id)
    if not cur or cur["project_id"] != pid or cur["kind"] != "package":
        raise Refused(404, "No such package.")
    if base_version != cur["version"]:
        raise Refused(409, f"The package changed since you opened it (now v{cur['version']}).")
    return cur


# ---- readiness: what each destination still needs (computed, never stored) ----
def shared_missing(pkg: dict) -> list[dict]:
    out = []
    for s in all_segments(pkg["copy"]):
        if s["state"] == "TO_CHECK":
            out.append({"field": s["field"], "text": f"\"{s['text'][:60]}\" {s.get('why') or 'is not supported'}: edit it, "
                                                      "remove it, or keep it as yours"})
    for a in pkg["assets"]:
        if artifact_bytes(a["artifact"]) is None:
            out.append({"field": "assets", "text": f"{a['alt_text'] or 'A file'}: the saved file is missing; upload it again"})
    for f in pkg.get("earlier_wording") or {}:
        out.append({"field": f, "text": f"Your earlier wording of {f} is kept beside the new text: keep yours or use the new text"})
    for f in sorted({m for sg in all_segments(pkg["copy"]) for m in MERGE.findall(sg["text"])}):
        out.append({"field": "template", "text": f"Needs {f.replace('_', ' ')}: fill it in, then apply the template again or edit the text"})
    return out


def unresolved(pkg: dict) -> dict[str, list[dict]]:
    """Per destination: blocking items (adapter required fields + shared checks). READY only when the list is empty."""
    shared = shared_missing(pkg)
    return {dst: shared + adapters()[dst].required(pkg) for dst in pkg["destinations"]}


def info_items(pkg: dict) -> list[dict]:
    """Non-blocking lines: the pre-plan route keeps its "not known" lines (the L6 replays); a package from a signed plan
    shows its role there and its method's costs from that version (S5-9, S5-10)."""
    if pkg["selection"]["state"] != "IN_SIGNED_PLAN":
        out = [{"field": "selection", "text": pkg["selection"]["text"]}]
        if pkg["flow"] == "outreach":
            out.append({"field": "budget", "text": "Costs for this experiment are not known yet (slice 5)"})
        return out
    from . import plans
    sel = pkg["selection"]
    mm = plans.method_money(db.get_record(sel["plan"]["id"], sel["plan"]["version"]), pkg["method"])
    return [{"field": "selection", "text": f"In your signed plan v{sel['plan']['version']} as "
                                          f"{'the main test' if sel['role'] == 'MAIN' else 'support for the main test'}"},
            {"field": "budget", "text": f"Costs in your signed plan: {mm['text']}" if mm else "Not costed in your plan yet"}]


# ---- slice 5: the signed plan's money in a package (S5-8, S5-11, S5-24; §5.10) ----
NO_BUDGET = {"amount": None, "period": "DAY", "source": None, "max_cpc": None}
def daily_for(pkg: dict, dst: str) -> dict | None:
    """One total daily budget split across the package's ad platforms (D5): the signed plan's allocations while they
    cover exactly these platforms, otherwise the equal split of the total. A platform never spends the whole total."""
    from .scenarios import split
    b, ads = pkg["budget"], [d for d in pkg["destinations"] if d != "EMAIL"]
    if b.get("allocations") and set(b["allocations"]) == set(ads):
        return b["allocations"].get(dst)
    return split(b["amount"], ads)[dst] if b.get("amount") and dst in ads else None


def from_signed_plan(plan_ref: dict | None, method: str) -> tuple[dict | None, dict]:
    """(budget, selection) for a package prepared from a signed plan: the budget only for digital, from that version's
    pinned scenario; None when the scenario has no daily budget (the founder sets one, YOU SAID)."""
    from . import plans, scenarios
    plan = plan_ref and db.get_record(plan_ref["id"], plan_ref["version"])
    if not plan:
        return None, {"state": "NOT_COMPARED", "text": "You chose this draft; costs and the comparison of experiments arrive with slice 5"}
    sel = {"state": "IN_SIGNED_PLAN", "plan": plan_ref, "method": method, "role": plans.roles_of(plan).get(method)}
    scn = method == "digital" and scenarios.of_plan(plan)
    if not scn or not scn["total_daily"]:
        return None, sel
    return {"amount": scn["total_daily"], "allocations": {p["platform"]: p["allocation"] for p in scn["platforms"]},
            "period": "DAY", "source": "PLAN", "plan": plan_ref, "scenario": plan["scenario"], "max_cpc": None}, sel


def budget_view(pid: str, pkg: dict) -> dict | None:
    """What Deliver shows (S5-23): the split per platform, and whether a YOU SAID budget differs from the signed plan."""
    if pkg["flow"] != "digital":
        return None
    from . import plans
    b = pkg["budget"]
    signed = plans.latest_signed(pid, pkg["city_id"])
    plan_b, _ = from_signed_plan(signed and {"id": signed["id"], "version": signed["version"]}, "digital")
    ads = [d for d in pkg["destinations"] if d != "EMAIL"]
    return {"total": b.get("amount"), "total_text": money_.display(b.get("amount")), "source": b.get("source"),
            "plan": b.get("plan"), "split": {d: money_.display(daily_for(pkg, d)) for d in ads},
            "differs_from_plan": bool(plan_b and b.get("source") == "YOU_SAID" and b.get("amount") != plan_b["amount"]),
            "plan_total_text": plan_b and money_.display(plan_b["amount"])}


# ---- founder edits (v+1, YOU SAID) ----
EDITABLE = ("confirm", "to", "cc", "bcc", "from_name", "subject", "segments", "headlines", "descriptions", "primary_text", "ad_text",
            "keywords", "cta", "landing_url", "display_name", "budget", "max_cpc", "start", "end", "radius_km",
            "rights_confirmed", "remove_assets", "destinations", "products", "ad_pick")
ADDR = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")


def _same(seg: dict | None, text: str | None) -> dict | None:
    """A field saved with its text unchanged keeps its checked state: a save never launders a flag."""
    return seg if seg and " ".join((text or "").split()) == seg["text"] else None


def _seg(text: str, field: str) -> dict:
    return {"field": field, "text": " ".join(text.split()), "cites": [], "claim_ids": [], "state": "YOU_SAID"}


def edit_package(pid: str, pkg_id: str, base_version: int, fields: dict) -> dict:
    cur = db.get_record(pkg_id)
    if not cur or cur["project_id"] != pid or cur["kind"] != "package":
        raise Refused(404, "No such package.")
    if base_version != cur["version"]:
        raise Refused(409, f"The package changed since you opened it (now v{cur['version']}).")
    bad = set(fields) - set(EDITABLE)
    if bad:
        raise Refused(422, f"Not editable: {', '.join(sorted(bad))}")
    p = _payload(cur)
    c = p["copy"]
    for k, v in fields.items():
        if k in ("to", "cc", "bcc"):
            addrs = [a.strip() for a in (v or []) if a.strip()]
            if bad_a := [a for a in addrs if not ADDR.match(a)]:
                raise Refused(422, f"Not an email address: {', '.join(bad_a)}")
            p["recipients"][k] = addrs
            p.setdefault("recipients_by", {})[k] = "YOU_SAID"
        elif k == "from_name":
            p["from_name"] = (v or "").strip() or None
        elif k == "subject":
            c["subject"] = _same(c.get("subject"), v) or (_seg(v, "subject") if (v or "").strip() else None)
        elif k in ("segments", "headlines", "descriptions"):
            olds = {s["text"]: s for s in c.get(k, [])}
            prefix = "paragraphs" if k == "segments" else k
            c[k] = [olds.get(" ".join(t.split())) or _seg(t, f"{prefix}[{i}]") for i, t in enumerate(v or []) if t.strip()]
        elif k in ("primary_text", "ad_text"):
            c[k] = _same(c.get(k), v) or (_seg(v, k) if (v or "").strip() else None)
        elif k == "confirm":  # "Keep it as mine": a flagged sentence becomes the founder's statement (STATED, YOU SAID)
            for sg in all_segments(c):
                if sg["field"] in (v or []) and sg["state"] == "TO_CHECK":
                    sg.update(state="YOU_SAID", confirmed_by="founder")
        elif k == "keywords":
            c["keywords"] = [{"text": x["text"].strip().lower(), "match_type": x.get("match_type", "PHRASE")}
                             for x in (v or []) if x.get("text", "").strip() and x.get("match_type", "PHRASE") in ("EXACT", "PHRASE", "BROAD")]
        elif k == "cta":
            if v not in CTAS:
                raise Refused(422, f"Choose one of {', '.join(CTAS)}.")
            p["creative"]["cta"] = v
        elif k == "landing_url":
            try:
                p["creative"]["landing_url"] = fetch.check_url(v)
            except (fetch.FetchBlocked, ValueError) as e:
                raise Refused(422, f"That landing page can't be used: {e}")
            p["creative"]["landing_by"] = "YOU_SAID"
        elif k == "display_name":
            p["creative"]["display_name"] = (v or "").strip() or None
        elif k in ("budget", "max_cpc"):
            m = money(v["amount"], v["ccy"]) if v else None
            if k == "budget":
                p["budget"].update({"amount": m, "source": "YOU_SAID" if m else None, "allocations": None})
            else:
                p["budget"]["max_cpc"] = m
        elif k in ("start", "end"):
            if v:
                try:  # a real calendar date, not just its shape (P6-I5)
                    date.fromisoformat(v)
                except (TypeError, ValueError):
                    raise Refused(422, f"{v} isn't a date: give it as YYYY-MM-DD.")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    raise Refused(422, "Give the date as YYYY-MM-DD.")
            p["timing"][k] = v or None
        elif k == "radius_km":
            if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and 1 <= v <= 80):
                raise Refused(422, "Give a radius between 1 and 50 miles.")
            v = round(v, 2) if isinstance(v, float) else v  # stored in km; the page shows miles (owner, 28 Oct 2026)
            p["targeting"]["geo"].update({"radius_km": v, "radius_by": "YOU_SAID" if v else None})
        elif k == "rights_confirmed":
            p["rights_confirmed"] = [a["artifact"]["sha256"] for a in p["assets"]] if v else []
        elif k == "remove_assets":  # recorded by digest, so a re-prepare never brings the image back [P65-R5]
            gone = [a for a in p["assets"] if a["artifact"]["id"] in set(v or [])]
            p["excluded"] = sorted(set(p.get("excluded", [])) | {a["artifact"]["sha256"] for a in gone})
            p["assets"] = [a for a in p["assets"] if a not in gone]
        elif k == "products":  # A4a [P65-R6]: a subset of the pinned draft's products
            dr = db.get_record(p["source"]["id"], p["source"]["version"])
            offered = {x["id"]: x for x in dr["products"]}
            if not isinstance(v, list) or set(v) - set(offered):
                raise Refused(422, "Choose products from this proposal's draft.")
            out = [x for i, x in offered.items() if i not in v]
            out_ids, titles = {x["id"] for x in out}, {x["title"] for x in out}
            gone = [a for a in p["assets"] if a.get("alt_text") in titles and
                    (db.get_record(a["artifact"]["id"]) or {}).get("source", {}).get("kind") == "FETCHED"]
            p["excluded"] = sorted(set(p.get("excluded", [])) | {a["artifact"]["sha256"] for a in gone})
            p["assets"] = [a for a in p["assets"] if a not in gone]
            p["products"] = [{"id": x["id"], "version": x["version"], "title": x["title"]} for i, x in offered.items() if i in v]
            p["products_by"] = "YOU_SAID"
            for sg in all_segments(c):
                if set(sg["claim_ids"]) & out_ids and sg["state"] != "YOU_SAID":
                    sg.update(state="TO_CHECK", why="cites a product you took out")
        elif k == "ad_pick":  # Launch: the ad you chose leads the campaign's headlines and descriptions
            vs = ad_variants(cur)
            if p["flow"] != "digital" or not isinstance(v, int) or not 0 <= v < len(vs):
                raise Refused(422, "Choose one of the drafted ads.")
            for f, t in (("headlines", vs[v]["headline"]), ("descriptions", vs[v]["description"])):
                c[f] = [x for x in c.get(f, []) if x["text"] == t] + [x for x in c.get(f, []) if x["text"] != t]
            p["ad_pick"] = v
        elif k == "destinations":
            if not v or set(v) - set(DESTINATIONS) or ("EMAIL" in v) != (p["flow"] == "outreach"):
                raise Refused(422, "Choose destinations that fit this experiment.")
            p["destinations"] = list(dict.fromkeys(v))
    t = p["timing"]
    if t.get("start") and t.get("end") and t["end"] < t["start"]:
        raise Refused(422, "The end date is before the start date.")
    p["you_said"] = sorted(set(p.get("you_said", [])) | set(fields))
    rec = db.put_record("package", pid, p, inputs=cur["inputs"], rid=pkg_id)
    db.event(pid, "founder", {"stage": "Pursue", "task": "Edited the delivery package", "detail": ", ".join(sorted(fields))})
    if "ad_pick" in fields:
        attach_picked_ad(pid, p["city_id"])
    return {"version": rec["version"]}


def upload_asset(pid: str, pkg_id: str, base_version: int, filename: str, media_type: str, data_b64: str, role: str) -> dict:
    cur = db.get_record(pkg_id)
    if not cur or cur["project_id"] != pid or cur["kind"] != "package":
        raise Refused(404, "No such package.")
    if base_version != cur["version"]:
        raise Refused(409, f"The package changed since you opened it (now v{cur['version']}).")
    allowed = IMAGE_TYPES if role == "CREATIVE" else ATTACH_TYPES
    if media_type not in allowed:
        raise Refused(422, f"Upload {' or '.join(t.split('/')[1].upper() for t in allowed)}.")
    try:
        data = base64.b64decode(data_b64, validate=True)
    except ValueError:
        raise Refused(422, "The file couldn't be read.")
    if not data or len(data) > MAX_ARTIFACT:
        raise Refused(422, "Upload a file under 8 MB.")
    name = re.sub(r"[^\w.-]+", "-", filename)[:80] or "upload"
    ref = save_artifact(pid, data, name, media_type, {"kind": "FOUNDER_UPLOAD", "at": db.now()}, "founder_supplied",
                        ["Supplied by you; you confirm you may use it"])
    p = _payload(cur)
    p["assets"] = [*p["assets"], {"artifact": ref, "role": role, "alt_text": name}]
    p["you_said"] = sorted(set(p.get("you_said", [])) | {"assets"})
    rec = db.put_record("package", pid, p, inputs=cur["inputs"], rid=pkg_id)
    db.event(pid, "founder", {"stage": "Pursue", "task": "Added a file to the package", "detail": name})
    return {"version": rec["version"], "artifact_id": ref["id"]}


# ---- message templates (6.5 UI.13, G13): merge fields filled only from records; no model call ----
STARTERS = json.loads((markets.DATA / "templates.json").read_text())
MERGE = re.compile(r"\{(recipient_name|city|brand|products|concept|sender_name)\}")


def templates(pid: str) -> list[dict]:
    """The project's templates; the starters are copied in on first use (one deterministic id per method)."""
    with db._lock:
        for m, t in STARTERS["starters"].items():
            cur = db.get_record(f"tpl_{pid}_{m}_0")  # a starter you never saved follows the current standard template
            if not cur or (cur.get("origin") == "LANDFALL_STARTER" and any(cur.get(k) != t[k] for k in ("name", "subject", "body"))):
                db.put_record("template", pid, {"method": m, **t, "origin": "LANDFALL_STARTER", "status": "active"},
                              rid=f"tpl_{pid}_{m}_0")
    seen: dict = {}
    for t in db.records("template", pid):
        seen[t["id"]] = t
    return [{k: v for k, v in t.items() if k not in ("project_id", "kind", "inputs")} for t in seen.values()]


def save_template(pid: str, tid: str | None, base_version: int | None, fields: dict) -> dict:
    """POST (new) or PUT (v+1) a template; every founder-saved template is YOU SAID."""
    if tid:
        cur = db.get_record(tid)
        if not cur or cur["project_id"] != pid or cur.get("kind") != "template":
            raise Refused(404, "No such template.")
        if base_version != cur["version"]:
            raise Refused(409, f"The template changed since you opened it (now v{cur['version']}).")
        t = {**_payload(cur), **{k: v for k, v in fields.items() if v is not None}}
    else:
        t = {"status": "active", **fields}
        tid = f"tpl_{pid}_{t.get('method')}_{len([x for x in templates(pid) if x['method'] == t.get('method')])}"
    if t.get("method") not in OUTREACH or not (t.get("subject") or "").strip() or not (t.get("body") or "").strip() \
            or t.get("status") not in ("active", "retired"):
        raise Refused(422, "A template needs an outreach method, a subject and a body.")
    rec = db.put_record("template", pid, {**t, "name": (t.get("name") or "My template").strip(), "origin": "YOU_SAID"}, rid=tid)
    return {"id": rec["id"], "version": rec["version"]}


def apply_template(pid: str, pkg_id: str, base_version: int, tref: dict) -> dict:
    """Package v+1 whose subject and paragraphs come from the template; the deterministic guards run; an unfilled
    field stays as {field} and is listed as NEEDS INFORMATION. The approval is outdated by the new version."""
    cur = _current(pid, pkg_id, base_version)
    fenced(pid, cur)
    t = db.get_record(tref.get("id") or "", tref.get("version"))
    if not t or t["project_id"] != pid or t.get("kind") != "template" or t["method"] != cur["method"] or cur["flow"] != "outreach":
        raise Refused(422, "Choose a template for this kind of outreach.")
    dr = db.get_record(cur["source"]["id"], cur["source"]["version"])
    p = _payload(cur)
    p["copy"] = {**p["copy"], **template_copy(pid, cur["city_id"], dr, (cur.get("partner") or {}).get("name"),
                                              cur["products"], cur.get("from_name"), t)}
    p["template_ref"] = {"id": t["id"], "version": t["version"]}
    p["earlier_wording"] = {k: v for k, v in (p.get("earlier_wording") or {}).items() if k not in ("subject", "segments")}
    p["you_said"] = sorted(set(p.get("you_said", [])) | {"subject", "segments"})  # founder-chosen wording [P65-R5]
    rec = db.put_record("package", pid, p, inputs=cur["inputs"], rid=pkg_id)
    missing = sorted({m for sg in all_segments(p["copy"]) for m in MERGE.findall(sg["text"])})
    db.event(pid, "founder", {"stage": "Act", "task": "Applied a message template", "detail": t["name"]})
    return {"version": rec["version"], "missing": missing}


def template_copy(pid: str, cid: str, dr: dict, recipient: str | None, products: list[dict], sender: str | None,
                  t: dict) -> dict:
    """{subject, segments} from a template: merge fields filled only from records, an unfilled one left as {field}, and
    the deterministic guards run on every line."""
    brand = (db.latest("brief", pid) or {}).get("brand_name")
    city = markets.CITIES[cid]["name"]
    values = {"recipient_name": recipient, "city": city, "brand": brand, "concept": dr["concept"]["line"] or None,
              "products": ", ".join([x["title"] for x in products if not GIFT.search(x["title"])][:3]) or None, "sender_name": sender}
    fill = lambda text: MERGE.sub(lambda m: values[m.group(1)] or m.group(0), text)  # noqa: E731
    state = "PLAIN" if t["origin"] == "LANDFALL_STARTER" else "YOU_SAID"

    def seg(text: str, field: str) -> dict:
        sg = {"field": field, "text": re.sub(r"(?<!\.)\.\.(?!\.)", ".", " ".join(fill(text).split())), "cites": [], "claim_ids": [],
              "state": state, "origin": "template"}  # "Co.." when a brand name ends in a full stop
        if why := guard(sg["text"], [], brand, city):
            sg.update(state="TO_CHECK", why=why)
        return sg
    paras = [x for x in re.split(r"\n\s*\n", t["body"]) if x.strip()]
    return {"subject": seg(t["subject"], "subject"), "segments": [seg(x, f"paragraphs[{i}]") for i, x in enumerate(paras)]}


# ---- Launch (owner, 28 Oct 2026): one checklist, each item ending in one sign-off; prepared in the background ----
LAUNCH_FIELDS = ("sender_name", "budget", "radius_km", "start", "end")
LAUNCH_RADIUS_KM = 16.09  # 10 miles (owner, 28 Oct 2026); stored in km, shown in miles
_ic_threads: dict[tuple[str, str], threading.Thread] = {}
_ic_notes: dict[tuple[str, str], str] = {}


def next_monday(today: date | None = None) -> str:
    t = today or date.today()
    return (t + timedelta(days=7 - t.weekday())).isoformat()


def launch_defaults(pid: str, cid: str) -> dict:
    """What the founder would otherwise type per package: a sender name from the brief, the daily ad budget from the
    signed plan (its scenario, else its media-spend estimate over the scenario's days), 15 km, next Monday."""
    from . import plans, scenarios
    brand = (db.latest("brief", pid) or {}).get("brand_name")
    signed = plans.latest_signed(pid, cid)
    scn = (signed and scenarios.of_plan(signed)) or {}
    line = next((x for x in (signed or {}).get("cost_lines") or [] if x.get("item_ref") == "digital:MEDIA_SPEND"), None) or {}
    days = line.get("days") or scn.get("horizon_days") or scenarios.base_of(pid, cid).get("horizon_days") or 14
    per_day = lambda m: {"minor": max(100, m["minor"] // days), "ccy": m["ccy"]}  # noqa: E731
    budget, by = None, None
    if scn.get("total_daily"):  # the plan's digital scenario stays the source
        budget, by = scn["total_daily"], "PLAN"
    elif line.get("amount") and line.get("by") != "strategist":  # the founder's own amount over the plan's days
        budget, by = per_day(line["amount"]), "PLAN"
    elif line.get("daily"):  # the strategist sized it when it allocated the ceiling
        budget, by = line["daily"], "PLAN"
    elif signed and (est := plans.estimator(signed, cid)) and (e := est("digital:MEDIA_SPEND")) and e.get("amount"):
        budget, by = per_day(e["amount"]), "PLAN_ESTIMATE"
    acct = ((conn_for(pid, "GOOGLE_ADS") or {}).get("selected") or {}).get("currency")
    if budget and acct and acct != budget["ccy"] and signed:  # a default in the account's own currency, at the plan's dated rate
        fx = plans.fx_of(signed) or plans.latest_fx(pid)
        a, b = money_.usd_rate(budget["ccy"], fx), money_.usd_rate(acct, fx)
        if a and b:
            budget, by = money_.of_major((money_.major(budget) * a / b).quantize(1) or 1, acct), "PLAN_CONVERTED"
    start = next_monday()
    return {"sender_name": f"The {brand} team" if brand else None, "budget": budget, "budget_by": by,
            "radius_km": LAUNCH_RADIUS_KM, "start": start, "end": (date.fromisoformat(start) + timedelta(days=days - 1)).isoformat(),
            "days": days}


def launch_settings(pid: str, cid: str) -> dict:
    d, saved = launch_defaults(pid, cid), db.get_record(f"launch_{pid}_{cid}") or {}
    out = {k: saved[k] if saved.get(k) is not None else d[k] for k in LAUNCH_FIELDS}
    by = {k: "YOU_SAID" if saved.get(k) is not None else (d.get(f"{k}_by") or "DEFAULT") for k in LAUNCH_FIELDS}
    return {**out, "by": by, "budget_text": money_.display(out["budget"]), "version": saved.get("version")}


def save_launch_settings(pid: str, cid: str, fields: dict) -> dict:
    """Asked once at the top of the checklist; every active package of the plan takes them (each a new version)."""
    cur = db.get_record(f"launch_{pid}_{cid}") or {}
    out = {k: cur.get(k) for k in LAUNCH_FIELDS}
    for k, v in fields.items():
        if k not in LAUNCH_FIELDS:
            raise Refused(422, f"Not a launch setting: {k}")
        if k == "sender_name":
            v = (v or "").strip()[:80] or None
        elif k == "budget":
            v = v and money(v["amount"], v["ccy"])
        elif k == "radius_km" and v is not None and not (isinstance(v, int) and 1 <= v <= 80):
            raise Refused(422, "Give a radius between 1 and 80 km.")
        elif k in ("start", "end") and v:
            try:
                date.fromisoformat(v)
            except (TypeError, ValueError):
                raise Refused(422, f"{v} isn't a date: give it as YYYY-MM-DD.")
        out[k] = v
    db.put_record("launch_settings", pid, out, rid=f"launch_{pid}_{cid}")
    apply_settings(pid, cid, force=True)
    return launch_settings(pid, cid)


def _went(pid: str, pkg: dict) -> bool:
    return any(latest_actions(pid, pkg["id"], d) or manuals_of(pid, pkg["id"], d) for d in pkg["destinations"])


def apply_settings(pid: str, cid: str, only: list[str] | None = None, force: bool = False) -> list[str]:
    """The launch settings into each active package that hasn't gone out: blanks (and a hypothesis radius) are filled;
    `force` (the founder saved them) overwrites. Each change is the package's v+1, so an earlier approval is outdated."""
    from . import plans
    with _settings_lock:  # the run that prepares a package and the page's prewarm may both apply them
        return _apply_settings(pid, cid, only, force, plans)


_settings_lock = threading.Lock()


def _apply_settings(pid: str, cid: str, only: list[str] | None, force: bool, plans) -> list[str]:
    s, done = launch_settings(pid, cid), []
    for pkg in packages_of(pid, cid):
        if pkg.get("status", "active") != "active" or (only is not None and pkg["id"] not in only) \
                or plans.fence(pid, cid, pkg["method"]) or _went(pid, pkg):
            continue
        f: dict = {}
        if pkg["flow"] == "outreach":
            if s["sender_name"] and pkg.get("from_name") != s["sender_name"] and (force or not pkg.get("from_name")):
                f["from_name"] = s["sender_name"]
        else:
            b, geo = pkg["budget"], pkg["targeting"]["geo"]
            if s["budget"] and b.get("amount") != s["budget"] and (force and s["by"]["budget"] == "YOU_SAID" or not b.get("amount")
                                                                       or b["amount"]["ccy"] != s["budget"]["ccy"]):  # a currency the account can't bill blocks anyway
                f["budget"] = {"amount": str(money_.major(s["budget"])), "ccy": s["budget"]["ccy"]}
            if s["radius_km"] and geo.get("radius_km") != s["radius_km"] and (force or geo.get("radius_by") != "YOU_SAID"):
                f["radius_km"] = s["radius_km"]
            for k in ("start", "end"):
                if s[k] and pkg["timing"].get(k) != s[k] and (force or not pkg["timing"].get(k)):
                    f[k] = s[k]
        if f:
            edit_package(pid, pkg["id"], pkg["version"], f)
            done.append(pkg["id"])
    return done


def contact_route(pid: str, lead: dict) -> dict | None:
    """How Landfall could reach a lead (owner, 28 Oct 2026: "only show people we can contact"): an email address (a
    page, the feed, Influencers.club or you), or for a creator a public profile whose identity is confirmed."""
    ct = lead.get("contact") or {}
    if ct.get("kind") in ("email", "phone") and ct.get("value"):
        return {"kind": ct["kind"], "value": ct["value"]}
    fc = db.get_record(f"fc_{pid}_{research._key(lead['key'])}")
    if fc and ADDR.match(fc.get("value") or ""):
        return {"kind": "email", "value": fc["value"], "by": "founder"}
    cr = lead.get("creator") or {}
    if cr.get("canonical_url") and cr.get("identity") in ("IDENTIFIED", "PROVIDER", "YOU_SAID"):
        return {"kind": "profile", "value": cr["canonical_url"], "platform": cr.get("platform")}
    return None


def _pinned_dossier(pid: str, cid: str, method: str) -> tuple[dict | None, dict | None]:
    from . import plans
    pin, _ = plans.pin_of(pid, cid, method)
    if not pin:
        return None, None
    dr = db.get_record(pin["draft"]["id"], pin["draft"]["version"])
    return dr, db.get_record(dr["source"]["dossier_id"], dr["source"]["dossier_version"])


def launch_options(pid: str, cid: str, method: str) -> list[dict]:
    """Who the item may go to: contactable people only. Research candidates (kept first), leads you added or picked,
    and for creators the Influencers.club search, biggest first. Anyone without a route stays in the research."""
    from . import creators
    dr, d = _pinned_dossier(pid, cid, method)
    if not d:
        return []
    out, seen = [], set()
    cands = {c.get("ref"): c for c in d["candidates"]}
    leads = list({r["id"]: r for r in db.records("lead", pid)}.values())
    for c in sorted(d["candidates"], key=lambda c: c["decision"] != "KEPT"):
        lead = c["decision"] in ("KEPT", "PENDING") and c.get("lead_id") and db.get_record(c["lead_id"])
        if lead and (r := contact_route(pid, lead)):
            seen.add(lead["key"])
            out.append({"key": lead["key"], "lead_id": lead["id"], "name": lead["name"], "route": r, "source": "RESEARCH",
                        "why": c.get("reason") if c["decision"] == "KEPT" else "Found by research · fit not confirmed"})
    for lead in leads:  # standalone: added by you or found by Influencers.club and picked [P66-R2]
        if lead.get("city_id") != cid or lead_method(lead) != method or lead["key"] in seen or lead["key"] in cands \
                or not (lead.get("found_by") or lead.get("origin") == "YOU_SAID"):
            continue
        if r := contact_route(pid, lead):
            seen.add(lead["key"])
            out.append({"key": lead["key"], "lead_id": lead["id"], "name": lead["name"], "route": r,
                        "source": "INFLUENCERS_CLUB" if lead.get("found_by") else "YOU_SAID", "why": None,
                        "followers": next((m["value"] for m in (lead.get("creator") or {}).get("metrics", []) if m["name"] == "followers"), None)})
    if method == "creator":
        searches = [x for x in {r["id"]: r for r in db.records("creator_search", pid)}.values() if x["city_id"] == cid]
        found = []
        for sr in searches:
            for i, a in enumerate(sr["accounts"]):
                p = a.get("profile_url") and creators.canonical(a["profile_url"]["value"])
                key = p and f"creator|{p['canonical_url'].lower()}"
                if key and key not in seen:
                    seen.add(key)
                    found.append({"key": key, "lead_id": None, "search": {"id": sr["id"], "index": i}, "name": a["full_name"] or a["username"],
                                  "route": {"kind": "profile", "value": p["canonical_url"], "platform": sr["platform"]},
                                  "source": "INFLUENCERS_CLUB", "followers": a.get("followers"), "why": None,
                                  "engagement_percent": a.get("engagement_percent"),
                                  "audience_location": (a.get("matched_filters") or {}).get("audience_location")})
        out += sorted(found, key=lambda o: -(o["followers"] or 0))
    return out


def start_creator_search(pid: str, cid: str) -> str | None:
    """One Influencers.club search for the beachhead, in the background, once per city (none if any exists). Its pool
    is the project's own session's, as for a search the founder starts; nothing else is dispatched here."""
    from . import influencers, plans
    if cid != plans.beachhead(pid):
        return "Creator search runs for your beachhead city."
    if any(x["city_id"] == cid for x in db.records("creator_search", pid)):
        return None
    if why := influencers.unavailable(pid):
        return why
    t = _ic_threads.get((pid, cid))
    if t and t.is_alive():
        return None
    session = db.q("SELECT session FROM projects WHERE id=?", pid)[0]["session"]

    def go():
        try:
            influencers.search(pid, cid, session)
        except influencers.Refused as e:
            _ic_notes[(pid, cid)] = e.detail
        except Exception as e:  # noqa: BLE001  shown on the item; never retried on its own
            _ic_notes[(pid, cid)] = f"The creator search didn't finish ({type(e).__name__})."
    t = _ic_threads[(pid, cid)] = threading.Thread(target=go, daemon=True)
    t.start()
    return None


def prewarm_launch(pid: str, city: str) -> dict:
    """Everything the signed plan's Launch checklist needs, started in the background; idempotent (a prepared method,
    a running run, a saved search or a current mockup is left alone), so it is safe to call twice or per city."""
    from . import mockups, plans
    out: dict = {"city": city, "started": [], "skipped": []}
    signed = plans.latest_signed(pid, city)
    if not signed:
        out["skipped"].append({"what": "plan", "why": "Sign off a plan first."})
        return out
    for m in signed["methods"]:
        method = m["method"]
        if m["role"] not in plans.INCLUDED or not m.get("draft"):
            continue
        if why := plans.fence(pid, city, method):
            out["skipped"].append({"what": method, "why": why})
            continue
        mine = [p for p in packages_of(pid, city, method) if p.get("status", "active") == "active"]
        try:
            if method == "creator" and (why := start_creator_search(pid, city)):
                out["skipped"].append({"what": "creator_search", "why": why})
            if method in OUTREACH and not mine:
                top = next((o for o in launch_options(pid, city, method) if o["lead_id"] and o["route"]["kind"] in ("email", "phone")), None)
                if top:
                    out["started"].append(prepare(pid, m["draft"]["id"], None, top["lead_id"], launch=True)["package_id"])
            elif method not in OUTREACH and not mine:
                out["started"].append(prepare(pid, m["draft"]["id"], None, launch=True)["package_id"])
            elif method not in OUTREACH:
                for p in mine:  # Google Ads only on the Launch path; Meta and TikTok stay in code
                    if p["destinations"] != ["GOOGLE_ADS"] and not _went(pid, p):
                        edit_package(pid, p["id"], p["version"], {"destinations": ["GOOGLE_ADS"]})
        except (Refused, runner.NotAllowed) as e:
            out["skipped"].append({"what": method, "why": getattr(e, "detail", None) or str(e)})
    try:
        mockups.start(pid, city)
    except Exception as e:  # noqa: BLE001  the kit is illustrative; the checklist never waits on it
        out["skipped"].append({"what": "mockups", "why": getattr(e, "detail", None) or type(e).__name__})
    apply_settings(pid, city)
    return out


def launch_pick(pid: str, cid: str, session: str, method: str, lead_id: str | None = None, search_id: str | None = None,
                index: int | None = None) -> dict:
    """The founder picks who an item goes to (one per method on the Launch path). A creator from the search becomes a
    lead, and only now is its contact looked up (one enrichment); the package is prepared from the template at once."""
    from . import influencers, plans
    pin, _ = plans.pin_of(pid, cid, method)
    if method not in OUTREACH or not pin:
        raise Refused(409, "Sign off a plan with this method first.")
    note = None
    if search_id:
        lead_id = influencers.choose(pid, search_id, index or 0)["lead_id"]
    lead = lead_id and db.get_record(lead_id)
    if lead_id and (not lead or lead["project_id"] != pid):
        raise Refused(404, "No such lead.")
    if lead and not contact_route(pid, lead):
        raise Refused(422, "Landfall has no way to reach them: no email address or confirmed profile. They stay in the research.")
    if lead and lead.get("found_by") and not lead.get("contact"):
        try:
            influencers.enrich(pid, lead_id, session)
        except influencers.Refused as e:
            note = e.detail
        lead = db.get_record(lead_id)
        if not lead.get("contact"):
            note = note or "No email address came back. You can send the message as an Instagram DM yourself."
    pkg_id = package_id_for(pin["draft"]["id"], lead and lead["key"])
    for p in packages_of(pid, cid, method):
        if p.get("status", "active") == "active" and p["id"] != pkg_id and not _went(pid, p):
            set_aside(pid, p["id"], p["version"], "deselected")
    cur = db.get_record(pkg_id)
    if cur and cur.get("status", "active") == "active":
        return {"run_id": None, "package_id": pkg_id, "note": note}
    return {**prepare(pid, pin["draft"]["id"], None, lead_id, launch=True), "note": note}


def _idx(sg: dict) -> int:
    m = re.search(r"\[(\d+)\]", sg.get("field") or "")
    return int(m.group(1)) if m else 99


def ad_variants(pkg: dict) -> list[dict]:
    """Three ads to choose from, in the order the copy was written: headline i with description i (cycled)."""
    c = pkg.get("copy") or {}
    hs, ds = sorted(c.get("headlines") or [], key=_idx), sorted(c.get("descriptions") or [], key=_idx)
    return [{"index": i, "headline": h["text"], "description": ds[i % len(ds)]["text"] if ds else None,
             "to_check": [x["text"] for x in (h, ds[i % len(ds)] if ds else None) if x and x["state"] == "TO_CHECK"]}
            for i, h in enumerate(hs[:3])]


# the three Launch ads' looks without a scene to take colors from (mockups.looks_of), as CampaignMockup.tsx AD
AD_LOOK = [("#f3ede1", "#14323d", "#14323d", "#f3ede1"), ("#1f4e5f", "#f3ede1", "#e9c46a", "#14323d"),
           ("#e07a5f", "#14323d", "#14323d", "#f3ede1")]
AD_ALT = "The ad you picked"


def picked_ad(pid: str, cid: str) -> dict | None:
    """Owner, 28 Oct 2026: "the same chosen ad should be sent in the email". The ad picked on Launch, composed as one
    PNG (the Qloo-styled scene, its headline and description, the first product photo, the button), saved once per
    content. None until an ad is picked. ponytail: a flat composition of the on-screen mockup, not a pixel copy."""
    import io
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    from . import mockups
    pkg = next((p for p in packages_of(pid, cid, "digital") if p.get("status", "active") == "active"
                and p.get("ad_pick") is not None), None)
    if not pkg:
        return None
    i = pkg["ad_pick"]
    v = ad_variants(pkg)[i]
    rec = mockups._record(pid, cid, "digital")
    bg, fg, cb, cf = (mockups.looks_of(rec) or AD_LOOK)[i % 3]
    W, H = 1080, 1350
    im = Image.new("RGB", (W, H), bg)
    scene = (rec or {}).get("image")
    raw = i != 2 and scene and artifact_bytes(scene["artifact"])  # the third ad is the colour one, no scene
    try:
        pic = raw and Image.open(io.BytesIO(raw)).convert("RGB")
    except (OSError, ValueError):
        pic = None
    if pic:
        im.paste(ImageOps.fit(pic, (W, H)))
        shade = Image.new("RGBA", (W, H))
        sd = ImageDraw.Draw(shade)
        r, g, b = Image.new("RGB", (1, 1), bg).getpixel((0, 0))
        for y in range(H):  # darker at the top for the words and at the bottom for the products, as the page's wash
            t = y / H
            a = 0.85 - t * 1.2 if t < 0.6 else (t - 0.6) * 2
            sd.line([(0, y), (W, y)], fill=(r, g, b, int(255 * max(0.0, min(0.9, a)))))
        im = Image.alpha_composite(im.convert("RGBA"), shade).convert("RGB")
    d = ImageDraw.Draw(im)

    def font(size: int, weight: int = 400):  # Inter (OFL, landfall/data/fonts): accents and other scripts' Latin letters
        f = ImageFont.truetype(str(markets.DATA / "fonts" / "Inter.ttf"), size)
        f.set_variation_by_axes([min(32, max(14, size)), weight])
        return f

    def wrap(text: str, f, width: int) -> list[str]:
        lines, cur = [], ""
        for w in text.split():
            if cur and d.textlength(f"{cur} {w}", font=f) > width:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        return [*lines, cur] if cur else lines

    y = 90
    d.text((80, y), f"SPONSORED · {markets.CITIES[cid]['name'].upper()}", font=font(34, 600), fill=fg)
    y += 80
    for ln in wrap(v["headline"], font(96, 800), W - 160)[:3]:
        d.text((80, y), ln, font=font(96, 800), fill=fg)
        y += 108
    y += 20
    for ln in wrap(v["description"] or "", font(44), W - 160)[:4]:
        d.text((80, y), ln, font=font(44), fill=fg)
        y += 58
    for a in pkg["assets"]:  # the first product photo that opens
        b = a["role"] == "CREATIVE" and artifact_bytes(a["artifact"])
        try:
            ph = b and ImageOps.contain(Image.open(io.BytesIO(b)).convert("RGBA"), (520, 420))
        except (OSError, ValueError):
            continue
        if ph:
            im.paste(ph, ((W - ph.width) // 2, H - 220 - ph.height), ph)
            break
    label = (pkg["creative"].get("cta") or "Learn more").replace("_", " ").capitalize()
    d.rounded_rectangle([80, H - 170, W - 80, H - 80], radius=45, fill=cb)
    d.text((W // 2, H - 125), label, font=font(42, 600), fill=cf, anchor="mm")
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    data = out.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    for r in db.records("artifact", pid):  # the same picture is saved once
        if r["sha256"] == sha:
            return {"id": r["id"], "sha256": sha}
    return save_artifact(pid, data, f"ad-{cid}.png", "image/png", {"kind": "COMPOSED_AD", "package": pkg["id"], "at": db.now()},
                         "landfall_generated", ["Composed by Landfall from your picked ad, its generated scene and your product photo"])


def attach_picked_ad(pid: str, cid: str) -> None:
    """The picked ad goes with every outreach email in the city that hasn't gone out, replacing an earlier pick."""
    ref = picked_ad(pid, cid)
    if not ref:
        return
    for pkg in packages_of(pid, cid):
        if pkg["flow"] != "outreach" or pkg.get("status", "active") != "active" or _went(pid, pkg):
            continue
        if any(a["artifact"]["sha256"] == ref["sha256"] for a in pkg["assets"]):
            continue
        p = _payload(pkg)
        p["assets"] = [{"artifact": ref, "role": "ATTACHMENT", "alt_text": AD_ALT},
                       *[a for a in p["assets"] if a.get("alt_text") != AD_ALT]]
        db.put_record("package", pid, p, inputs=pkg["inputs"], rid=pkg["id"])


def _reachable(pid: str, pkg: dict) -> bool:
    """A package for someone Landfall can't reach (an earlier pick with no email or confirmed profile) isn't the item's
    pick on the Launch path; its history stays in the package. One that went out always stays."""
    part = pkg.get("partner") or {}
    if pkg["flow"] != "outreach" or not part.get("lead_id") or pkg["recipients"]["to"] or _went(pid, pkg):
        return True
    lead = db.get_record(part["lead_id"])
    return bool(lead and contact_route(pid, lead))


def launch_view(pid: str, cid: str, session: str) -> dict:
    """GET …/plans/{city}/launch: the signed plan's items, each with who it can go to and its package; the launch
    settings; never dispatches or prepares anything."""
    from . import influencers, plans
    a = plans.act(pid, cid)
    if not a["plan"]:
        return {"plan": None, "reason": a["reason"], "plan_changed": False, "items": [], "settings": None, "earlier_plan": a["earlier_plan"]}
    signed, cur = plans.latest_signed(pid, cid), db.get_record(plans.plan_id(pid, cid))
    s = launch_settings(pid, cid)
    items = []
    for m in signed["methods"]:
        method = m["method"]
        if m["role"] not in plans.INCLUDED or plans.role_of(cur, method) not in plans.INCLUDED or not m.get("draft"):
            continue
        pkgs = [p for p in packages_of(pid, cid, method) if p.get("status", "active") == "active" and not plans.fence(pid, cid, method)
                and _reachable(pid, p)]
        run = db.q("SELECT status FROM runs WHERE project_id=? AND kind='delivery_prepare' AND input_key LIKE ? "
                   "ORDER BY started_at DESC, rowid DESC LIMIT 1", pid, f"{m['draft']['id']}:%")
        it = {"method": method, "label": research.METHOD_LABELS[method], "role": m["role"], "draft": m["draft"],
              "packages": [{"id": p["id"], "version": p["version"]} for p in pkgs],
              "preparing": bool(run and run[0]["status"] in ("queued", "running")),
              "failed": bool(run and run[0]["status"] == "failed" and not pkgs)}
        if method in OUTREACH:
            it["options"] = opts = launch_options(pid, cid, method)
            if not pkgs and opts:
                dr = db.get_record(m["draft"]["id"], m["draft"]["version"])
                t = next(x for x in templates(pid) if x["id"] == f"tpl_{pid}_{method}_0")
                c = template_copy(pid, cid, dr, opts[0]["name"], dr["products"], s["sender_name"], t)
                it["preview"] = {"to": opts[0]["name"], "subject": c["subject"]["text"],
                                 "body": "\n\n".join(x["text"] for x in c["segments"])}
            if method == "creator":
                th = _ic_threads.get((pid, cid))
                srch = [x for x in {r["id"]: r for r in db.records("creator_search", pid)}.values() if x["city_id"] == cid]
                pool = influencers.pool_of(session)
                it["creators"] = {"available": influencers.unavailable(pid) is None, "reason": influencers.unavailable(pid),
                                  "searching": bool(th and th.is_alive()), "searched": bool(srch),
                                  "status": srch[-1]["status"] if srch else None, "note": _ic_notes.get((pid, cid)),
                                  "enrich_ok": influencers.costs(pid, pool)["enrich"]["ok"], "attribution": influencers.ATTRIBUTION}
        else:
            st = (pkgs[0].get("ad_style") if pkgs else None) or (db.get_record(ad_copy_id(m["draft"]["id"])) or {}).get("style") or {}
            it["styled"] = [*st.get("themes", []), *st.get("audiences", [])]
            if pkgs:
                it["variants"], it["picked"] = ad_variants(pkgs[0]), pkgs[0].get("ad_pick")
            elif adc := db.get_record(ad_copy_id(m["draft"]["id"])):  # written as the plan was built: shown at once
                it["variants"], it["picked"] = ad_variants({"copy": adc["got"]["copy"]}), None
        if it["method"] != "digital" and not pkgs and not it.get("options") and not it["preparing"] \
                and not (it.get("creators") or {}).get("searching"):
            continue  # nobody Landfall can reach: no item to sign off (owner, 28 Oct 2026)
        items.append(it)
    items.sort(key=lambda x: x["method"] != "digital")  # the ads first (owner, 28 Oct 2026)
    return {"plan": a["plan"], "plan_changed": a["plan_changed"], "settings": s, "items": items, "earlier_plan": a["earlier_plan"]}


# ---- exports: deterministic bytes of one version; never a SENT record ----
EPOCH = (1980, 1, 1, 0, 0, 0)


def email_text(pkg: dict) -> tuple[str, str]:
    c = pkg["copy"]
    body = "\n\n".join(s["text"] for s in c.get("segments", []))
    if pkg.get("from_name") and not body.rstrip().endswith(pkg["from_name"]):  # a template signs off with it already
        body += f"\n\n{pkg['from_name']}"
    return ((c.get("subject") or {}).get("text") or ""), body


def mime(pkg: dict, sender: str | None, include_bcc: bool = True) -> bytes:
    """RFC 5322 bytes from the package version and its saved artifacts; a deterministic boundary, no Date or Message-ID."""
    subject, body = email_text(pkg)
    m = EmailMessage(policy=email.policy.SMTP)
    if sender:  # an exported draft has no From: a bare name isn't an address, and your mail app sets its own
        m["From"] = formataddr((pkg.get("from_name") or "", sender))
    for k, hdr in (("to", "To"), ("cc", "Cc"), ("bcc", "Bcc")):
        if pkg["recipients"][k] and (k != "bcc" or include_bcc):
            m[hdr] = ", ".join(pkg["recipients"][k])
    m["Subject"] = subject
    m.set_content(body)
    for a in pkg["assets"]:
        if a["role"] != "ATTACHMENT":
            continue
        data = artifact_bytes(a["artifact"])
        art = db.get_record(a["artifact"]["id"])
        if data is None:
            raise Refused(422, f"{art['filename']}: the saved file is missing; nothing was exported")
        main, sub = art["media_type"].split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=art["filename"])
    if m.is_multipart():
        m.set_boundary("landfall-" + hashlib.sha256(body.encode()).hexdigest()[:24])
    return m.as_bytes()


def _zip(files: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files):
            zi = zipfile.ZipInfo(name, EPOCH)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
    return buf.getvalue()


def _asset_files(pkg: dict, folder: str) -> list[tuple[str, bytes]]:
    out = []
    for a in pkg["assets"]:
        data = artifact_bytes(a["artifact"])
        art = db.get_record(a["artifact"]["id"])
        if data is None:
            raise Refused(422, f"{art['filename']}: the saved file is missing; nothing was exported")
        out.append((f"{folder}/{a['artifact']['sha256'][:8]}-{art['filename']}", data))
    return out


def public_json(pkg: dict) -> dict:
    return {"landfall_package": {"id": pkg["id"], "version": pkg["version"]}, "label": "MANUAL SETUP AND REFERENCE PACKAGE",
            "note": "Not a direct-import file for any platform. Nothing in it was sent or created by Landfall.",
            **{k: pkg[k] for k in ("source", "city_id", "method", "concept", "destinations", "products", "partner", "recipients",
                                   "from_name", "copy", "audience_rationale", "targeting", "budget", "timing", "creative",
                                   "settings", "selection", "sources", "assumptions", "evidence")},
            "artifact_manifest": manifest(pkg), "unresolved": unresolved(pkg)}


def copy_csv(pkg: dict) -> bytes:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["field", "text", "state", "claim_ids"])
    for s in all_segments(pkg["copy"]):
        w.writerow([s["field"], s["text"], s["state"], " ".join(s["claim_ids"])])
    for k in pkg["copy"].get("keywords", []):
        w.writerow(["keyword", k["text"], k["match_type"], ""])
    return buf.getvalue().encode()


def export_bytes(pid: str, pkg: dict, fmt: str, artifact_id: str | None = None) -> tuple[bytes, str, str]:
    """(bytes, media type, filename) for one export of exactly this package version."""
    stem = f"landfall-{pkg['id'][4:12]}-v{pkg['version']}"
    if fmt == "eml":
        if pkg["flow"] != "outreach":
            raise Refused(422, "An email export is for outreach packages.")
        # P6-I6: an export is a function of the package version alone; your mail app sets the sending address
        return mime(pkg, None), "message/rfc822", f"{stem}.eml"
    if fmt == "json":
        return json.dumps(public_json(pkg), indent=2, ensure_ascii=False).encode(), "application/json", f"{stem}.json"
    if fmt == "csv":
        return copy_csv(pkg), "text/csv", f"{stem}-copy.csv"
    if fmt == "settings_csv":  # v4 B09: a settings sheet for setting up by hand, never an import file; no account values
        if pkg["flow"] != "digital":
            raise Refused(422, "A settings sheet is for digital experiments.")
        import csv
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["platform", "setting", "value", "basis"])
        for dst in pkg["destinations"]:
            for v in adapters()[dst].plan(pkg, None)["values"]:
                if v["label"] != "ACCOUNT":
                    val = v["value"]
                    w.writerow([adapters()[dst].LABEL, v["field"], ", ".join(map(str, val)) if isinstance(val, list) else
                                "" if val is None else val, v["label"]])
        return buf.getvalue().encode(), "text/csv", f"{stem}-settings.csv"
    if fmt == "asset":
        a = next((a for a in pkg["assets"] if a["artifact"]["id"] == artifact_id), None)
        data = a and artifact_bytes(a["artifact"])
        if data is None:
            raise Refused(404, "That file isn't in this package version, or its saved bytes are missing.")
        art = db.get_record(artifact_id)
        return data, art["media_type"], art["filename"]
    readme = ("Prepared by Landfall. This is a manual setup and reference package: nothing in it was sent, published or "
              "created by Landfall, and it is not a direct-import file for any platform.\n"
              f"Package {pkg['id']} v{pkg['version']}. Every file is listed with its sha256 in manifest.json.\n")
    if fmt == "outreach_zip":
        if pkg["flow"] != "outreach":
            raise Refused(422, "An outreach package is for outreach experiments.")
        subject, body = email_text(pkg)
        files = [("README.txt", readme.encode()), ("message.eml", export_bytes(pid, pkg, "eml")[0]),
                 ("message.txt", f"Subject: {subject}\n\n{body}\n".encode()),
                 ("manifest.json", json.dumps(public_json(pkg), indent=2, ensure_ascii=False).encode()),
                 *_asset_files(pkg, "attachments")]
        return _zip(files), "application/zip", f"{stem}-outreach.zip"
    if fmt == "campaign_zip":
        if pkg["flow"] != "digital":
            raise Refused(422, "A campaign package is for digital experiments.")
        files = [("README.txt", readme.encode()), ("package.json", export_bytes(pid, pkg, "json")[0]),
                 ("copy.csv", copy_csv(pkg)), *_asset_files(pkg, "assets")]
        for dst in pkg["destinations"]:
            ad = adapters()[dst]
            plan = ad.plan(pkg, None)  # P6-I6: no account, connection or deployment value enters an export
            files.append((f"plans/{dst.lower()}.json", json.dumps(
                {"label": "SETUP VALUES · not a request, not provider validated", "note": "ACCOUNT values come from your own "
                 "account when you set it up", "values": [v for v in plan["values"] if v["label"] != "ACCOUNT"],
                 "account_values": [v["field"] for v in plan["values"] if v["label"] == "ACCOUNT"],
                 "still_needed": unresolved(pkg)[dst]}, indent=2, ensure_ascii=False).encode()))
            files.append((f"setup-{dst.lower()}.txt", ("\n".join(f"{i}. {s}" for i, s in enumerate(ad.setup_steps(pkg), 1)) + "\n").encode()))
        return _zip(files), "application/zip", f"{stem}-campaign.zip"
    raise Refused(404, "No such export format.")


def strip_bulk(requests: list[dict]) -> list[dict]:
    """Raw MIME and file bodies are referenced by digest in exported and displayed plans."""
    out = []
    for r in requests:
        r = json.loads(json.dumps(r))
        if "raw" in (r.get("json") or {}):
            r["json"]["raw"] = f"<RFC 5322 message, sha256 {hashlib.sha256(r['json']['raw'].encode()).hexdigest()[:16]}…>"
        if "bytes" in (r.get("form") or {}):
            r["form"]["bytes"] = "<image bytes from the approved artifact>"
        out.append(r)
    return out


def record_export(pid: str, pkg: dict, fmt: str, data: bytes) -> dict:
    rec = db.put_record("export", pid, {"package_id": pkg["id"], "package_version": pkg["version"], "format": fmt,
                                        "sha256": hashlib.sha256(data).hexdigest(), "at": db.now()}, rid=db.new_id("exp"))
    db.event(pid, "founder", {"stage": "Pursue", "task": "Downloaded", "detail": f"{fmt} of package v{pkg['version']} (nothing was sent)"})
    return rec


def mailto(pkg: dict) -> str:
    from urllib.parse import quote
    subject, body = email_text(pkg)
    q = [f"subject={quote(subject)}", f"body={quote(body)}"]
    q += [f"{k}={quote(','.join(pkg['recipients'][k]))}" for k in ("cc", "bcc") if pkg["recipients"][k]]
    return f"mailto:{quote(','.join(pkg['recipients']['to']), safe='@,')}?{'&'.join(q)}"


# ---- connections (delegated OAuth; tokens encrypted outside records) ----
def _fernet():
    key = config.env("LANDFALL_TOKEN_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def token_store_ready() -> bool:
    return _fernet() is not None


def put_token(conn_id: str, token: dict) -> None:
    f = _fernet()
    if f is None:
        raise Refused(503, "Token storage isn't configured in this deployment.")
    db.x("INSERT OR REPLACE INTO connection_tokens VALUES(?,?,?)", conn_id, f.encrypt(json.dumps(token).encode()), db.now())


def get_token(conn_id: str) -> dict | None:
    f = _fernet()
    row = db.q("SELECT token FROM connection_tokens WHERE connection_id=?", conn_id)
    if not row or f is None:
        return None
    return json.loads(f.decrypt(row[0]["token"]))


def simulated(pid: str) -> bool:
    """Replay and demo projects never call a delivery provider: their actions are SIMULATED and say so."""
    p = db.q("SELECT mode FROM projects WHERE id=?", pid)[0]
    return p["mode"] == "replay" or bool(config.REPLAY_CASES)


def conn_id(pid: str, dst: str) -> str:
    return f"con_{pid}_{dst.lower()}"


def _own(pid: str, dst: str) -> dict | None:
    c = db.get_record(conn_id(pid, dst))
    return c if c and c["state"] != "DISCONNECTED" else None


# ---- owner-connected shared delivery (BUILD-PLAN §6.7.10 G-J; owner instruction 27 Oct) ----
SHARED = "_shared"  # ponytail: the deployment's own project id for shared connections (no projects row): con__shared_{dst}
SHARED_LIMITS = {"EMAIL": (20, 2), "GOOGLE_ADS": (20, 2)}  # J5: (total, per project); LANDFALL_SHARED_{DST}_TOTAL|_PER_PROJECT
SHARED_DISCLOSURE = {
    "EMAIL": "Sent through Landfall's own test sender, set up by the Landfall team; you don't connect an account.",
    "GOOGLE_ADS": "Created in Landfall's Google Ads test account (never serves, never bills); you don't connect an account."}
SHARED_REFUSED = "This uses Landfall's own test connection, set up by the Landfall team; you don't connect, choose or check an account here."
SEND_UNAVAILABLE = "Sending is unavailable right now. Your package is kept: download or copy it to use it yourself"
USED_UP = "Landfall's test-delivery allowance for judges is used up. Exports remain available."


def shared_on() -> bool:
    return (config.env("LANDFALL_SHARED_DELIVERY") or "").strip().lower() == "on"


def shared_enabled(dst: str) -> bool:
    """Only test environments: Gmail in TEST_REDIRECT with both test addresses, Google Ads with TEST access and both test
    ids. Never DIRECT email, production ad access or TikTok."""
    if not shared_on():
        return False
    if dst == "EMAIL":
        return "to" in email_routing()
    if dst == "GOOGLE_ADS":
        from .adapters import google_ads as g
        return g.test_access() and bool(g.digits("google_ads_test_manager_id")) and bool(g.digits("google_ads_test_customer_id"))
    # extension point: META_ADS joins here (sandbox only) once the owner accepts its arrangement (G-J table)
    return False


def live_project(pid: str) -> bool:
    """Judge or owner grant (DEPLOYMENT A1); the recorded demo for everyone else is SIMULATED by `simulated()`."""
    try:
        from . import grants
    except ImportError:
        return True
    return grants.live_project(pid)


def via_shared(pid: str, dst: str) -> bool:
    """The judge path: no connection of the project's own, and shared delivery enabled for this test destination."""
    return (pid != SHARED and _own(pid, dst) is None and shared_enabled(dst) and not simulated(pid)
            and live_project(pid))


def conn_for(pid: str, dst: str) -> dict | None:
    """The one resolver [P67-R5]: the project's own connection (customers), else the shared one when enabled, else none.
    The card, the plan, approval and execution all read it, so they always name the same connection."""
    return _own(pid, dst) or (_own(SHARED, dst) if via_shared(pid, dst) else None)


def is_shared(c: dict | None) -> bool:
    return bool(c) and c["project_id"] == SHARED


def no_judge(pid: str, dst: str) -> None:
    """Judges never connect, choose, disconnect or validate the shared connection."""
    if via_shared(pid, dst):
        raise Refused(403, SHARED_REFUSED)


def shared_limits(dst: str) -> tuple[int, int]:
    total, per = SHARED_LIMITS[dst]
    return (int(config.env(f"LANDFALL_SHARED_{dst}_TOTAL") or total), int(config.env(f"LANDFALL_SHARED_{dst}_PER_PROJECT") or per))


def shared_used(dst: str) -> list[str]:
    """The project of every action that dispatched (or is dispatching) through the shared connection, from the stored
    action records. One that ended before any request was sent (an expired token) isn't counted."""
    out = []
    for r in db.q("SELECT project_id, payload, MAX(version) FROM records WHERE kind='execution' "
                  "AND json_extract(payload, '$.connection_id')=? GROUP BY id", conn_id(SHARED, dst)):
        a = json.loads(r["payload"])
        if a["status"] == "EXECUTING" or any(s["status"] != "PENDING" for s in a["steps"]):
            out.append(r["project_id"])
    return out


def allowance_left(pid: str, dst: str) -> bool:
    used, (total, per) = shared_used(dst), shared_limits(dst)
    return len(used) < total and used.count(pid) < per


def shared_view(pid: str, dst: str, conn: dict | None) -> dict | None:
    if not via_shared(pid, dst):
        return None
    why = (SEND_UNAVAILABLE + "." if not conn or conn["state"] != "ACTIVE" or not conn.get("account_identity")
           or not token_store_ready() else None if allowance_left(pid, dst) else USED_UP)
    return {"available": why is None, "why": why, "disclosure": SHARED_DISCLOSURE[dst]}


def private_values() -> dict[str, str]:
    """Configured private values → their labels (G2 extended to Google Ads ids, with and without dashes)."""
    from .adapters.google_ads import TEST_LABEL, digits
    out = {}
    for name in ("google_ads_test_manager_id", "google_ads_test_customer_id"):
        d = digits(name)
        if len(d) >= 6:
            out.update({config.env(name).strip(): TEST_LABEL, d: TEST_LABEL, f"{d[:3]}-{d[3:6]}-{d[6:]}": TEST_LABEL})
    for name, label in (("gmail_test_sender", SENDER_LABEL), ("gmail_test_recipient", INBOX_LABEL)):
        if (v := (config.env(name) or "").strip()):
            out[v] = label
    return out


def scrub(obj):
    """The final pass on judge-visible responses (package, destination, execute, Act) while shared delivery is on."""
    if not shared_on():
        return obj
    text = json.dumps(obj)
    vals = private_values()
    for k in sorted(vals, key=len, reverse=True):  # the dashed form before its digits
        text = text.replace(k, vals[k])
    return json.loads(text)


def public_url() -> str:
    return (config.env("LANDFALL_PUBLIC_URL") or "http://127.0.0.1:8000").rstrip("/")


def redirect_uri() -> str:
    return f"{public_url()}/api/connections/callback"


def _new_state(pid: str, dst: str, pkg_id: str | None) -> str:
    state = base64.urlsafe_b64encode(hashlib.sha256(f"{pid}{dst}{time.time_ns()}".encode()).digest()).decode().rstrip("=")
    db.x("INSERT INTO oauth_states VALUES(?,?,?,?,?,0)", state, pid, dst, pkg_id, db.now())
    return state


def owner_connect_start(dst: str) -> str:
    """scripts/owner_connect.py: an OAuth state flagged shared (its project is SHARED); the existing callback saves it."""
    if not shared_enabled(dst):
        raise Refused(409, f"Shared delivery isn't enabled for {dst}: LANDFALL_SHARED_DELIVERY=on and the test settings are needed.")
    cap = capability(dst)
    if not cap["can_connect"]:
        raise Refused(409, cap["why_not"])
    return adapters()[dst].auth_url(_new_state(SHARED, dst, None), redirect_uri())


def owner_save(dst: str, got: dict) -> None:
    """The shared Gmail must be the configured test sender; the shared Google Ads account is the configured test client,
    chosen here (the only allowed account), so a judge never chooses."""
    if dst == "EMAIL" and got["user"]["email"].lower() != (email_routing().get("sender") or "").lower():
        raise ValueError(WRONG_SENDER)
    save_connection(SHARED, dst, got)
    if dst == "GOOGLE_ADS":
        from .adapters.google_ads import digits
        select_account(SHARED, dst, digits("google_ads_test_customer_id"), {})


def connect_start(pid: str, dst: str, pkg_id: str | None) -> dict:
    ad = adapters()[dst]
    no_judge(pid, dst)
    if simulated(pid):
        rec = db.put_record("connection", pid, {
            "provider": ad.PROVIDER, "destination": dst, "state": "ACTIVE", "simulated": True,
            "account_label": "Demonstration account · SIMULATED", "account_identity": ad.SIMULATED_IDENTITY,
            "accounts": [], "scopes": [], "connected_at": db.now(), "last_ok_at": db.now()}, rid=conn_id(pid, dst))
        return {"simulated": True, "connection_version": rec["version"]}
    cap = capability(dst)
    if not cap["can_connect"]:
        raise Refused(409, cap["why_not"])
    if (direct := getattr(ad, "direct_connect", lambda: None)()):  # 6.7 T1: a portal-issued sandbox token, no browser step
        save_connection(pid, dst, direct)
        return {"connected": True}
    return {"url": ad.auth_url(_new_state(pid, dst, pkg_id), redirect_uri())}


def connect_callback(params: dict) -> str:
    """The shared OAuth callback: one use of a fresh state, then the adapter's code exchange and account reads."""
    state = params.get("state") or ""
    with db._lock:
        row = db.q("SELECT * FROM oauth_states WHERE state=? AND used=0", state)
        if not row:
            raise Refused(400, "This sign-in link has expired or was already used. Start again from Landfall.")
        db.x("UPDATE oauth_states SET used=1 WHERE state=?", state)
    row = row[0]
    if (datetime.now(timezone.utc) - datetime.fromisoformat(row["at"])).total_seconds() > 900:
        raise Refused(400, "This sign-in link has expired. Start again from Landfall.")
    pid, dst = row["project_id"], row["provider"]
    back = "/" if pid == SHARED else f"/p/{pid}/deliver/{row['package_id']}" if row["package_id"] else f"/p/{pid}"
    if params.get("error"):
        db.event(pid, "founder", {"stage": "Pursue", "task": "Connect", "detail": f"{dst}: not connected ({params['error']})"})
        return back
    ad = adapters()[dst]
    try:
        got = ad.connect(params, redirect_uri())
        if pid == SHARED:
            owner_save(dst, got)
            return back
    except (ValueError, KeyError, TypeError) as e:  # back to the package with the reason, never a dead-end error page
        db.event(pid, "founder", {"stage": "Pursue", "task": "Connect", "detail": f"{ad.LABEL}: not connected ({e})"})
        from urllib.parse import quote
        return f"{back}?connect_error={quote(f'{ad.LABEL} wasn’t connected: {e}. Nothing was changed; try again.')}"
    save_connection(pid, dst, got)
    return back


def save_connection(pid: str, dst: str, got: dict) -> None:
    ad = adapters()[dst]
    prev = db.get_record(conn_id(pid, dst))
    rid = conn_id(pid, dst)
    put_token(rid, got["token"])
    accounts = got.get("accounts", [])
    # P6-I2: authorization only discovers accounts. An ad account becomes executable only through `select`, which reads
    # its currency (and Page, location); a single account is pre-chosen in the form, never silently completed here.
    db.put_record("connection", pid, {
        "provider": ad.PROVIDER, "destination": dst, "state": "ACTIVE", "simulated": False,
        "user": got["user"], "accounts": accounts, "selected": None,
        "account_identity": None if ad.NEEDS_ACCOUNT else ad.identity(got["user"], None),
        "account_label": ad.label_of(got["user"], None), "scopes": got.get("scopes", []),
        "connected_at": db.now(), "last_ok_at": db.now(), "previous_identity": (prev or {}).get("account_identity")}, rid=rid)
    db.event(pid, "founder", {"stage": "Pursue", "task": "Connected", "detail": f"{ad.LABEL}: {ad.label_of(got['user'], None)}"})


def select_account(pid: str, dst: str, account_id: str, extra: dict) -> dict:
    no_judge(pid, dst)
    c = conn_for(pid, dst)
    if not c or c["state"] != "ACTIVE":
        raise Refused(409, "Connect your account first.")
    ad = adapters()[dst]
    acc = next((a for a in c.get("accounts", []) if a["id"] == account_id), None)
    if not acc:
        raise Refused(422, "Choose one of the accounts this connection can use.")
    sel = ad.select(acc, extra, c, get_token(c["id"]))
    if not sel.get("currency"):  # never a guessed currency for a budget (P6-I2)
        raise Refused(422, f"{ad.LABEL} didn't say which currency this account bills in; it can't be used yet.")
    rec = db.put_record("connection", pid, {**_payload(c), "selected": sel, "account_identity": ad.identity(c["user"], sel),
                                            "account_label": ad.label_of(c["user"], sel), "last_ok_at": db.now()}, rid=c["id"])
    return {"version": rec["version"]}


def disconnect(pid: str, dst: str) -> dict:
    no_judge(pid, dst)
    c = db.get_record(conn_id(pid, dst))
    if not c:
        raise Refused(404, "Nothing is connected.")
    db.x("DELETE FROM connection_tokens WHERE connection_id=?", c["id"])
    rec = db.put_record("connection", pid, {**_payload(c), "state": "DISCONNECTED", "ended_at": db.now()}, rid=c["id"])
    db.event(pid, "founder", {"stage": "Pursue", "task": "Disconnected", "detail": adapters()[dst].LABEL})
    return {"version": rec["version"]}


def mark_connection(c: dict, state: str) -> None:
    db.put_record("connection", c["project_id"], {**_payload(c), "state": state, "ended_at": db.now()}, rid=c["id"])


def capability(dst: str) -> dict:
    """Adapter implemented · deployment configured · platform access, as the owner records them; never inferred."""
    ad = adapters()[dst]
    missing = ad.missing_config() + ([] if token_store_ready() else ["LANDFALL_TOKEN_KEY"])
    access = ad.access()
    can = not missing and access["connectable"]
    why = ("Direct delivery unavailable in this deployment" if missing else
           "Platform verification pending" if access["state"] == "PENDING" and not access["connectable"] else
           "Platform approval required" if not access["connectable"] else "")
    return {"adapter": "IMPLEMENTED", "deployment": "NOT_CONFIGURED" if missing else "CONFIGURED", "missing_config": missing,
            "platform_access": access["state"], "access_note": access["note"], "can_connect": can, "why_not": why,
            "provider_validation": "SUPPORTED" if ad.VALIDATES else "NOT_SUPPORTED"}


# ---- provider plans (P6-R3): built from one package version and the connected account ----
def provider_plan(pid: str, pkg: dict, dst: str) -> dict:
    ad = adapters()[dst]
    c = conn_for(pid, dst)
    live = c if c and c["state"] == "ACTIVE" else None
    plan = ad.plan(pkg, live, sim=simulated(pid)) if dst == "EMAIL" else ad.plan(pkg, live)
    plan["blockers"] = [*plan["blockers"], *unresolved(pkg)[dst]]
    core = {"package": {"id": pkg["id"], "version": pkg["version"]}, "destination": dst, "requests": plan["requests"],
            "identity": (c or {}).get("account_identity") if c and c["state"] == "ACTIVE" else None,
            **({"routing": plan["routing"]} if plan.get("routing") else {})}  # G1: mode, inbox and sender bind the approval
    return {**plan, **core, "connection_id": c and c["id"], "plan_hash": h(core), "manifest_hash": h(manifest(pkg)),
            "label": "SYNTHETIC REQUEST PLAN · not provider validated"}


# ---- approvals ----
def approvals(pid: str, pkg_id: str, dst: str) -> list[dict]:
    return [a for a in db.records("approval", pid) if a["package_id"] == pkg_id and a["destination"] == dst]


def fenced(pid: str, pkg: dict) -> None:
    """The earlier-plan fence [P65-R1], on the server for every route that prepares, approves, validates or executes."""
    from . import plans
    if pkg.get("status") == "set_aside":
        raise Refused(409, "This package was set aside; it belongs to an earlier plan.")
    if why := plans.fence(pid, pkg["city_id"], pkg["method"]):
        raise Refused(409, why)


def approve(pid: str, pkg_id: str, dst: str, base_version: int) -> dict:
    pkg = db.get_record(pkg_id)
    if not pkg or pkg["project_id"] != pid or dst not in pkg.get("destinations", []):
        raise Refused(404, "No such package or destination.")
    fenced(pid, pkg)
    if base_version != pkg["version"]:
        raise Refused(409, f"The package changed since you opened it (now v{pkg['version']}).")
    if missing := unresolved(pkg)[dst]:
        raise Refused(422, f"Resolve {len(missing)} item{'s' * (len(missing) > 1)} to approve: " + "; ".join(m["text"] for m in missing))
    plan = provider_plan(pid, pkg, dst)
    executable = not plan["blockers"] and plan["identity"] is not None
    rec = db.put_record("approval", pid, {
        "package_id": pkg_id, "package_version": pkg["version"], "destination": dst, "action_kind": adapters()[dst].KIND,
        "manifest_hash": plan["manifest_hash"], "plan_hash": plan["plan_hash"] if executable else None,
        "payload_hash": h(plan["requests"]) if executable else None, "identity": plan["identity"] if executable else None,
        "use": "execute" if executable else "manual", "by": "founder", "at": db.now()}, rid=db.new_id("apv"))
    db.event(pid, "founder", {"stage": "Pursue", "task": "Approved", "detail":
                              f"{adapters()[dst].LABEL}: package v{pkg['version']}" + ("" if executable else " (for your own use)")})
    return {"approval_id": rec["id"], "use": rec["use"]}


def approval_state(pkg: dict, apv: dict | None, plan: dict) -> str:
    if not apv:
        return "NONE"
    if db.q("SELECT 1 FROM approval_claims WHERE approval_id=?", apv["id"]):
        return "CONSUMED"  # used by an attempt: never offered again (P6-I3)
    if apv["package_version"] != pkg["version"] or apv["manifest_hash"] != plan["manifest_hash"]:
        return "OUTDATED"
    return "APPROVED"


# ---- execution: one code path for every adapter [P6-R1] ----
class Transport(Exception):
    """No response: a timeout, a reset or a refused connection."""


_CLIENT = httpx.Client(transport=httpx.HTTPTransport(retries=0), timeout=30.0, follow_redirects=False)


def transport(req: dict, headers: dict) -> tuple[int, object]:
    """Plain HTTP, automatic retries off: the action's own state machine decides (P6-R1)."""
    try:
        files = None
        if req.get("multipart"):
            mp = req["multipart"]
            data = artifact_bytes({"sha256": mp["file"]["sha256"]})
            files = {mp["file"]["field"]: (mp["file"]["filename"], data, mp["file"]["media_type"])}
        form = dict(req.get("form") or {})
        if "bytes" in form:
            form["bytes"] = base64.b64encode(artifact_bytes({"sha256": form["bytes_sha256"]}) or b"").decode()
            form.pop("bytes_sha256", None)
        r = _CLIENT.request(req["method"], req["url"], headers=headers, params=req.get("params"), json=req.get("json"),
                            data=(req["multipart"]["fields"] if req.get("multipart") else form) or None, files=files)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise Transport(type(e).__name__)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:2000]


SEND = transport  # tests replace this with a fake provider


def fill(obj, ids: dict):
    """Placeholders "{{step}}" become the id that step returned."""
    if isinstance(obj, str):
        m = re.fullmatch(r"\{\{(\w+)\}\}", obj)
        if m:
            return ids[m.group(1)]
        return re.sub(r"\{\{(\w+)\}\}", lambda x: str(ids[x.group(1)]), obj)
    if isinstance(obj, list):
        return [fill(x, ids) for x in obj]
    if isinstance(obj, dict):
        return {k: fill(v, ids) for k, v in obj.items()}
    return obj


def read(req: dict, headers: dict, tries: int = 3) -> tuple[int, object]:
    """Reads, refreshes and validate_only: ≤ 3 attempts with backoff (§6.2a); a 4xx answer is returned at once."""
    last: tuple[int, object] = (0, "no attempt")
    for n in range(tries):
        try:
            last = SEND(req, headers)
        except Transport as e:
            last = (0, str(e))
        if last[0] and last[0] < 500 and last[0] != 429:
            return last
        time.sleep(0 if config.PROVIDER_MODE == "replay" else 0.5 * 2 ** n)
    return last


def _save(a: dict) -> dict:
    pid = a.get("project_id") or db.get_record(a["id"])["project_id"]
    return {**db.put_record("execution", pid, _payload(a), rid=a["id"]), "project_id": pid}


def latest_actions(pid: str, pkg_id: str, dst: str) -> list[dict]:
    seen = {}
    for a in db.records("execution", pid):
        if a["package"]["id"] == pkg_id and a["destination"] == dst:
            seen[a["id"]] = a
    return sorted(seen.values(), key=lambda a: a["at"])


def allow_listed(dst: str, ident: dict, plan: dict) -> str | None:
    """Development allow-list (CONTRACTS B8): the designated test recipient and the owner's accounts. Unset refuses every
    live write; `*` is the production setting (customers deliver only to their own connected accounts)."""
    raw = config.env("LANDFALL_DELIVERY_ALLOWLIST") or ""
    if raw.strip() == "*":
        return None
    allowed = {x.strip().lower() for x in raw.split(",") if x.strip()}
    targets = adapters()[dst].allow_targets(ident, plan)
    bad = [t for t in targets if t.lower() not in allowed]
    if bad and is_test(plan):
        return "The configured test addresses aren't on the delivery allow-list"
    return f"Not on the development allow-list: {', '.join(bad)}" if bad else None


def execute(pid: str, pkg_id: str, dst: str, approval_id: str) -> dict:
    """Prepare the ExecutionAction from the approved plan, claim the approval once, then run the steps."""
    ad = adapters()[dst]
    pkg = db.get_record(pkg_id)
    if not pkg or pkg["project_id"] != pid:
        raise Refused(404, "No such package.")
    apv = db.get_record(approval_id)
    if not apv or apv["package_id"] != pkg_id or apv["destination"] != dst:
        raise Refused(404, "No such approval.")
    fenced(pid, pkg)
    sim = simulated(pid)
    plan = provider_plan(pid, pkg, dst)
    if dst == "EMAIL" and (who := open_overlap(pid, pkg, plan["recipients"])):  # P65-R2: one person reached through two leads
        raise Refused(409, f"Another message to {who} hasn't been resolved. Check it before sending this one.")
    c = conn_for(pid, dst)
    judge = via_shared(pid, dst)
    for a in latest_actions(pid, pkg_id, dst):
        if is_test(a) and test_sent(a):
            raise Refused(409, "A test email was already sent from this package.")
        if a["status"] in ("EXECUTING", "UNCERTAIN"):
            raise Refused(409, "An earlier attempt hasn't been resolved. Check it before doing anything else.")
        if a["status"] in ("SUCCEEDED", "FAILED_VERIFICATION") and a["mode"] == "LIVE":
            raise Refused(409, f"Already {ad.VERB} from this package; Landfall never does it twice.")
    if approval_state(pkg, apv, plan) != "APPROVED":
        raise Refused(409, "The package changed after your approval. Approve this version.")
    if not sim:
        cap = capability(dst)
        if cap["deployment"] != "CONFIGURED":
            raise Refused(409, "Direct delivery unavailable in this deployment.")
        if not c or c["state"] != "ACTIVE":
            raise Refused(409, SEND_UNAVAILABLE + "." if judge else "Reconnect your account. Your package and approval are kept.")
    if plan["blockers"]:
        raise Refused(422, "Can't run yet: " + "; ".join(b["text"] for b in plan["blockers"]))
    if apv["use"] != "execute" or apv["plan_hash"] != plan["plan_hash"] or apv["identity"] != plan["identity"]:
        raise Refused(409, "Your approval doesn't cover this exact request (the account or plan changed). Approve it again.")
    if not sim and (why := allow_listed(dst, plan["identity"], plan)):
        return _refused_action(pid, pkg, dst, apv, plan, why)
    if not sim and (why := ad.precheck(plan)):  # e.g. Gmail recipients re-projected from the final MIME (P45-R1)
        return _refused_action(pid, pkg, dst, apv, plan, why)
    # B13 (P1-4): objects an earlier definitive failure created stay in the account, paused; this attempt reuses their ids
    # and dispatches only the missing steps, so a retry never makes a second campaign or ad set
    reused = created_before(pid, pkg_id, dst, plan["identity"]) if ad.PAUSES else {}
    requests = [r for r in plan["requests"] if r["step"] not in reused]
    # the shared allowance is counted and this action's record written under one lock, so two projects racing for the
    # last allowed action can't both pass. ponytail: process lock (one uvicorn worker, DEPLOYMENT §1); a db.tx() if more
    with db._lock:
        if judge and not allowance_left(pid, dst):
            raise Refused(409, USED_UP)
        aid = db.new_id("exe")
        try:  # P6-I1: one attempt per package × destination at a time, whatever the approval; a unique row, so atomic
            db.x("INSERT INTO execution_slots VALUES(?,?,?,?)", pkg_id, dst, aid, db.now())
        except sqlite3.IntegrityError:
            raise Refused(409, f"Another attempt for {ad.LABEL} is running or already done. Nothing was {ad.VERB} twice.")
        if dst == "EMAIL" and (who := claim_recipients(pid, plan["recipients"], aid)):
            release(pkg_id, dst, aid)
            raise Refused(409, f"Another message to {who} hasn't been resolved. Check it before sending this one.")
        try:
            db.x("INSERT INTO approval_claims VALUES(?,?,?)", approval_id, aid, db.now())
        except sqlite3.IntegrityError:
            release(pkg_id, dst, aid)
            db.x("DELETE FROM recipient_claims WHERE action_id=?", aid)
            raise Refused(409, "This approval was already used. Nothing was sent twice.")
        action = {"project_id": pid, **db.put_record("execution", pid, {
            "package": {"id": pkg_id, "version": pkg["version"]}, "destination": dst, "action_kind": ad.KIND,
            "identity": plan["identity"], "connection_id": c and c["id"], "approval_id": approval_id,
            "plan_hash": plan["plan_hash"], "payload_hash": apv["payload_hash"], "artifact_manifest_hash": plan["manifest_hash"],
            "steps": [{"step": r["step"], "status": "PENDING", "provider_id": None} for r in requests],
            "requests": requests, "mode": "SIMULATED" if sim else "LIVE", "status": "EXECUTING", "ids": dict(reused),
            "reused": reused or None,
            "result": None, "verification": None, "error": None, "reference": f"LF-{aid[4:12]}", "at": db.now(),
            # P67-R1: classification (Act, card, resolution, recovery) reads these, never the current config
            **({"routing": plan.get("routing"), "partner_name": (pkg.get("partner") or {}).get("name"),
                "recipients": plan["recipients"]} if dst == "EMAIL" else {})}, rid=aid)}
    db.event(pid, "founder", {"stage": "Pursue", "task": f"{ad.LABEL}: {'simulate' if sim else ad.VERB_NOW}",
                              "detail": f"package v{pkg['version']} · {action['reference']}"})
    return run_action(action, sim)


def created_before(pid: str, pkg_id: str, dst: str, identity: dict | None) -> dict:
    """The ids each earlier live attempt that failed definitively had confirmed, in the same account (B13). An attempt in
    another account, an uncertain one, or one you closed as "It didn't happen" is never reused."""
    # ponytail: reused ids keep the earlier version's names and image; add a per-step "replace" choice if founders need it
    ids: dict = {}
    for a in latest_actions(pid, pkg_id, dst):
        if a["mode"] == "LIVE" and a["status"] == "FAILED" and a.get("identity") == identity:
            ids.update({s["step"]: s["provider_id"] for s in a["steps"] if s["status"] == "CONFIRMED"})
    return ids


def open_overlap(pid: str, pkg: dict, actual: list[str]) -> str | None:
    """An address this send actually reaches that another package's open email action also reaches (6.7 G1: the actual
    recipients, so concurrent test sends serialize on the test inbox; the intended partner isn't reached, so not claimed)."""
    mine = {a.lower() for a in actual}
    for other in packages_of(pid):
        if other["id"] == pkg["id"]:
            continue
        for a in latest_actions(pid, other["id"], "EMAIL"):
            if a["status"] in ("EXECUTING", "UNCERTAIN") and not test_sent(a):
                if "recipients" in a:
                    sent = set(a["recipients"])
                else:  # actions from before 6.7 reached their package's recipients
                    r = db.get_record(other["id"], a["package"]["version"])["recipients"]
                    sent = {x.lower() for k in ("to", "cc", "bcc") for x in r.get(k, [])}
                if hit := mine & sent:
                    return "the private test inbox" if is_test(a) else sorted(hit)[0]
    return None


def claim_recipients(pid: str, actual: list[str], aid: str) -> str | None:
    """P65-I1: every address an email reaches is claimed in one transaction (a unique row per project and address, so
    atomic across packages, threads and workers) until its action is resolved; the first address already held is
    returned and nothing is claimed."""
    mine = sorted({a.strip().lower() for a in actual})
    with db._lock:
        c = db.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            for a in mine:
                c.execute("INSERT INTO recipient_claims VALUES(?,?,?,?)", (pid, a, aid, db.now()))
        except sqlite3.IntegrityError:
            c.execute("ROLLBACK")
            return "the private test inbox" if a == (email_routing().get("to") or "").lower() else a
        c.execute("COMMIT")
    return None


HOLDS_SLOT = ("EXECUTING", "UNCERTAIN", "SUCCEEDED", "FAILED_VERIFICATION")


def release(pkg_id: str, dst: str, action_id: str) -> None:
    """A definitive failure or "It didn't happen" frees the destination for a new, newly approved attempt."""
    db.x("DELETE FROM execution_slots WHERE package_id=? AND destination=? AND action_id=?", pkg_id, dst, action_id)


def _end(a: dict) -> dict:
    a = _save(a)
    if a["status"] not in HOLDS_SLOT or a["mode"] == "SIMULATED":
        release(a["package"]["id"], a["destination"], a["id"])
    if a["status"] not in ("EXECUTING", "UNCERTAIN"):  # the recipients are free once the outcome is known
        db.x("DELETE FROM recipient_claims WHERE action_id=?", a["id"])
    return a


def _refused_action(pid, pkg, dst, apv, plan, why) -> dict:
    rec = db.put_record("execution", pid, {
        "package": {"id": pkg["id"], "version": pkg["version"]}, "destination": dst, "action_kind": adapters()[dst].KIND,
        "identity": plan["identity"], "approval_id": apv["id"], "plan_hash": plan["plan_hash"], "steps": [], "requests": [],
        "mode": "LIVE", "status": "REFUSED", "ids": {}, "result": None, "verification": None, "error": why,
        "reference": None, "at": db.now()}, rid=db.new_id("exe"))
    return rec


def run_action(a: dict, sim: bool) -> dict:
    ad = adapters()[a["destination"]]
    if sim:
        for s in a["steps"]:
            s.update(status="SIMULATED", provider_id=f"simulated-{s['step']}")
            a["ids"][s["step"]] = s["provider_id"]
        a.update(status="SUCCEEDED", result={"simulated": True, "note": "SIMULATED: no provider was called"})
        return _end(a)
    c = db.get_record(a["connection_id"])
    shared = is_shared(c)
    try:
        token = ad.fresh_token(c, get_token(c["id"]), read)
    except ad.Expired:  # the packages and approvals are kept; only the connection changes state
        mark_connection(c, "EXPIRED")
        a.update(status="FAILED", error=f"{SEND_UNAVAILABLE}; nothing was sent" if shared else
                 "Your connection expired before anything was sent. Reconnect; nothing was sent.")
        return _end(a)
    except ad.Unavailable:
        a.update(status="FAILED", error=f"{ad.LABEL} couldn't be reached, so nothing was sent. Try again later" +
                 (", or download or copy the package to use it yourself" if shared else ""))
        return _end(a)
    if token is None:
        a.update(status="FAILED", error=f"{SEND_UNAVAILABLE}; nothing was sent" if shared else
                 "The connection's token is missing. Reconnect; nothing was sent.")
        return _end(a)
    headers = ad.auth_headers(token, c)
    a["ids"].update(ad.dispatch_ids(a))
    for i, req in enumerate(a["requests"]):
        step = a["steps"][i]
        step["status"] = "SENT"  # dispatched or about to be: on restart this is UNCERTAIN, never redispatched
        a = _save(a)
        step = a["steps"][i]
        try:
            status, body = SEND(fill(req, a["ids"]), headers)
            outcome, detail = ad.outcome(req["step"], status, body)
        except Transport as e:
            outcome, detail = "unknown", f"no response ({e})"
        except KeyError as e:  # a placeholder whose id is missing: never dispatched
            outcome, detail = "unknown", f"missing id {e}"
        if outcome == "ok":
            step.update(status="CONFIRMED", provider_id=detail["id"])
            a["ids"][req["step"]] = detail["id"]
            a.setdefault("responses", {})[req["step"]] = detail.get("extra")
            a = _save(a)  # P6-I4: durable before anything else is sent or read
            continue
        if outcome == "expired":  # a documented non-commit: the token was refused before anything was done
            mark_connection(c, "EXPIRED")
            outcome, detail = "rejected", (f"{SEND_UNAVAILABLE}: the provider refused Landfall's test connection" if shared
                                           else "Reconnect your account: the provider refused the connection's token")
        if outcome == "rejected":
            step.update(status="REJECTED", error=detail)
            created = [*(a.get("reused") or {}), *(s["step"] for s in a["steps"] if s["status"] == "CONFIRMED")]
            a.update(status="FAILED", error=detail, partial=created or None)
            if created and ad.PAUSES:  # partial resources are kept and their state reported
                a["verification"] = ad.read_back(a, c, headers, read)
            return _end(a)
        step.update(status="UNKNOWN", error=detail)
        a.update(status="UNCERTAIN", error=detail)
        if ad.FINDS_BY_REFERENCE:
            a["found"] = ad.find(a, c, headers, read)
        return _end(a)
    a["result"] = ad.result(a, c)
    if ad.PAUSES:
        a["phase"] = "VERIFYING"
        a = _save(a)
        v = ad.read_back(a, c, headers, read)
        a["verification"] = v
        a = _save(a)
        if not v["verified"]:
            for req in ad.repause(a, c, v):
                a.setdefault("safety", []).append({"step": "repause", "url": req["url"], "status": "SENT"})
                a = _save(a)  # a safety write is recorded before it is sent, and never resent
                try:
                    st, body = SEND(req, headers)
                    ok = ad.outcome(req["step"], st, body)[0] == "ok"
                except Transport:
                    ok = False
                a["safety"][-1]["status"] = "CONFIRMED" if ok else "UNKNOWN"
                a = _save(a)
                if not ok:
                    a.update(status="UNCERTAIN", error="Not confirmed paused, and the re-pause got no clear answer")
                    return _end(a)
            a["verification"] = {**ad.read_back(a, c, headers, read), "repaused": True}
            a.update(status="FAILED_VERIFICATION")
            return _end(a)
    a.update(status="SUCCEEDED")
    db.put_record("connection", c["project_id"], {**_payload(c), "last_ok_at": db.now()}, rid=c["id"])
    return _end(a)


def recover() -> int:
    """On boot (P6-R1): an action that was EXECUTING when the process stopped is UNCERTAIN; it is never redispatched."""
    n = 0
    latest = {}
    for r in db.q("SELECT id, MAX(version) v FROM records WHERE kind='execution' GROUP BY id"):
        latest[r["id"]] = db.get_record(r["id"], r["v"])
    for a in latest.values():
        if a["status"] == "EXECUTING":
            for s in a["steps"] + a.get("safety", []):
                if s["status"] == "SENT":
                    s["status"] = "UNKNOWN"
            created = a["steps"] and all(s["status"] == "CONFIRMED" for s in a["steps"])
            a.update(status="UNCERTAIN", error=(
                "Created, but Landfall stopped before confirming it is paused" if created else
                "Landfall stopped while this was running; it may or may not have happened"))
            _save(a)  # ids and responses saved so far are kept; nothing is redispatched
            n += 1
    return n


def resolve(pid: str, action_id: str, happened: bool, provider_id: str | None, note: str | None) -> dict:
    """UNCERTAIN is the founder's to resolve: a ManualRecord ("It was sent / created") or NOT_EXECUTED_BY_FOUNDER."""
    a = db.get_record(action_id)
    if not a or a["project_id"] != pid or a["status"] != "UNCERTAIN":
        raise Refused(409, "Only an uncertain attempt can be resolved here.")
    ad = adapters()[a["destination"]]
    if happened and is_test(a):  # P67-R1: receipt of a test is a test outcome, never "Marked sent by you"
        if (a.get("resolution") or {}).get("kind") == "TEST_RECEIPT_CONFIRMED":
            raise Refused(409, "You already confirmed this test email.")
        a["resolution"] = {"by": "owner", "happened": True, "kind": "TEST_RECEIPT_CONFIRMED", "at": db.now()}
        _save(a)  # Gmail's answer stays UNCERTAIN and the slot stays held: still no resend
        db.x("DELETE FROM recipient_claims WHERE action_id=?", a["id"])  # the outcome is known; other test sends may go
        db.event(pid, "founder", {"stage": "Pursue", "task": "Resolved",
                                  "detail": "Gmail: you confirmed the test email is in the private test inbox"})
        return {"status": "UNCERTAIN", "test": "TEST_RECEIPT_CONFIRMED"}
    if happened:  # a ManualRecord beside the result; the result itself stays UNCERTAIN (P6-R2)
        manual_record(pid, a["package"]["id"], a["destination"], note=f"{provider_id or ''} {note or ''}".strip() or None,
                      action_id=action_id)
        a["resolution"] = {"by": "founder", "happened": True, "provider_id": provider_id, "at": db.now()}
        _save(a)
        return {"status": "UNCERTAIN", "manual": ad.MANUAL}
    a.update(status="NOT_EXECUTED_BY_FOUNDER", resolution={"by": "founder", "happened": False, "at": db.now()})
    _end(a)  # frees the destination; the consumed approval stays consumed, so a new approval is required
    db.event(pid, "founder", {"stage": "Pursue", "task": "Resolved", "detail": f"{ad.LABEL}: you said it didn't happen"})
    return {"status": "NOT_EXECUTED_BY_FOUNDER"}


def manual_record(pid: str, pkg_id: str, dst: str, date: str | None = None, note: str | None = None,
                  action_id: str | None = None) -> dict:
    pkg = db.get_record(pkg_id)
    if not pkg or pkg["project_id"] != pid or dst not in pkg["destinations"]:
        raise Refused(404, "No such package or destination.")
    ad = adapters()[dst]
    rec = db.put_record("manual_record", pid, {"package_id": pkg_id, "package_version": pkg["version"], "destination": dst,
                                               "manual_kind": ad.MANUAL, "date": date or db.now()[:10], "note": note,
                                               "action_id": action_id, "by": "founder"}, rid=db.new_id("man"))
    db.event(pid, "founder", {"stage": "Pursue", "task": "Recorded by you",
                              "detail": f"{ad.LABEL}: {'Marked sent by you' if ad.MANUAL == 'MARKED_SENT_BY_YOU' else 'Marked created by you'}"})
    return {"id": rec["id"]}


def withdraw_manual(pid: str, pkg_id: str, dst: str, record_id: str) -> dict:
    """"Withdraw this mark": a superseding version with `withdrawn_at`; nothing is deleted (A9)."""
    m = db.get_record(record_id)
    if not m or m["project_id"] != pid or m.get("kind") != "manual_record" or m["package_id"] != pkg_id or m["destination"] != dst:
        raise Refused(404, "No such record.")
    if m.get("withdrawn_at"):
        raise Refused(409, "This mark was already withdrawn.")
    rec = db.put_record("manual_record", pid, {**_payload(m), "withdrawn_at": db.now()}, rid=record_id)
    db.event(pid, "founder", {"stage": "Act", "task": "Withdrew a mark", "detail": f"{adapters()[dst].LABEL}"})
    return {"version": rec["version"]}


def manuals_of(pid: str, pkg_id: str, dst: str) -> list[dict]:
    """The latest version of each ManualRecord; withdrawn ones stay listed as history. A record tied to a test send can't
    be made since 6.7; one found in older data is never shown or counted as your own outreach (P67-R1)."""
    seen: dict = {}
    for m in db.records("manual_record", pid):
        if m["package_id"] == pkg_id and m["destination"] == dst:
            seen[m["id"]] = m
    return [m for m in seen.values() if not (m.get("action_id") and is_test(db.get_record(m["action_id"])))]


# ---- slice 6.7: test delivery (G1, G3) ----
from .adapters.gmail import INBOX_LABEL, SENDER_LABEL, WRONG_SENDER, email_mode, email_routing, is_test, partner_name, routed  # noqa: E402


def test_sent(a: dict) -> bool:
    """TEST_SENT: a test action Gmail accepted, or an uncertain one whose receipt the owner confirmed. Never contact."""
    return is_test(a) and a["mode"] == "LIVE" and (a["status"] == "SUCCEEDED" or (
        a["status"] == "UNCERTAIN" and (a.get("resolution") or {}).get("kind") == "TEST_RECEIPT_CONFIRMED"))


def contact_missing(pkg: dict) -> str | None:
    """In test delivery the partner's address doesn't block a test send, but real outreach still needs one."""
    if pkg["flow"] == "outreach" and email_mode() == "TEST_REDIRECT" and not pkg["recipients"]["to"]:
        return "Contact address: not found. A test send doesn't establish one."
    return None


def routing_view(pid: str, pkg: dict) -> dict | None:
    """G3: labels and the intended outreach only; the configured addresses never leave the server."""
    if simulated(pid) or email_mode() != "TEST_REDIRECT":
        return None
    r = pkg["recipients"]
    send = routed(pkg, "")
    return {"mode": "TEST_REDIRECT", "sender_label": SENDER_LABEL, "recipient_label": INBOX_LABEL,
            "intended": {"name": partner_name(pkg), "to": r["to"], "cc": r["cc"], "bcc": r["bcc"],
                         "basis": (pkg.get("recipients_by") or {}).get("to")},
            "receives": {"subject": send["copy"]["subject"]["text"], "first_line": send["copy"]["segments"][0]["text"]}}


def startup_check() -> None:
    """G1: a judging deployment never sends directly to a partner."""
    if config.env("LANDFALL_ENV") == "judging" and email_mode() == "DIRECT":
        raise RuntimeError("LANDFALL_EMAIL_MODE=direct is refused when LANDFALL_ENV=judging")


def provider_validate(pid: str, pkg_id: str, dst: str) -> dict:
    ad = adapters()[dst]
    pkg = db.get_record(pkg_id)
    if not pkg or pkg["project_id"] != pid:
        raise Refused(404, "No such package.")
    fenced(pid, pkg)
    no_judge(pid, dst)
    if not ad.VALIDATES:
        raise Refused(422, f"{ad.LABEL} has no documented validation-only mode.")
    plan = provider_plan(pid, pkg, dst)
    if plan["blockers"] or not plan["identity"]:
        raise Refused(422, "Complete the plan and connect an account first.")
    c = conn_for(pid, dst)
    if simulated(pid):
        res = {"result": "SIMULATED", "message": "SIMULATED: no provider was called"}
    else:
        token = ad.fresh_token(c, get_token(c["id"]), read)
        res = ad.validate_remote(plan, c, ad.auth_headers(token, c), read)
    rec = db.put_record("provider_validation", pid, {"package_id": pkg_id, "package_version": pkg["version"], "destination": dst,
                                                     "plan_hash": plan["plan_hash"], **res, "at": db.now()},
                        rid=f"pv_{pkg_id}_{dst.lower()}")
    return {"result": rec["result"], "message": rec.get("message")}


# ---- the Action Center (DestinationState, C; wording fixed by BUILD-PLAN §6.1) ----
def summary_of(pid: str, pkg: dict, dst: str, cap: dict, conn: dict | None, missing: list, appr: str, plan: dict,
               acts: list[dict], apv: dict | None, shared: dict | None = None) -> tuple[str, str, list[str]]:
    """(summary, sentence, next actions). The latest action's outcome first [P6-R2], then capability, then approval."""
    ad = adapters()[dst]
    last = acts[-1] if acts else None
    newer_approval = bool(last and apv and apv["at"] > last["at"])
    if last:
        st = last["status"]
        if st == "EXECUTING":
            return "EXECUTING", ad.EXECUTING, []
        if test_sent(last):
            return "TEST_SENT", ad.success_text(last), []
        if st == "UNCERTAIN" and is_test(last):
            return ("UNCERTAIN", "We can't tell whether Gmail accepted this test email. Check the private test inbox before "
                                 "doing anything else.", ["resolve_happened", "resolve_not_happened"])
        if st == "UNCERTAIN":
            return ("UNCERTAIN", f"We can't tell whether this was {ad.VERB}. Check {ad.WHERE} before doing anything else.",
                    ["resolve_happened", "resolve_not_happened"])
        if st == "FAILED_VERIFICATION":
            return "FAILED_VERIFICATION", f"Created, but not confirmed paused: Landfall paused it again. Check it in {ad.WHERE}.", []
        if st == "SUCCEEDED":
            sim = " (SIMULATED: no provider was called)" if last["mode"] == "SIMULATED" else ""
            return ad.SUCCESS, ad.success_text(last) + sim, []
        if (part := partly_of(last)) and not newer_approval:  # B13: what exists is shown, and only the rest is offered
            return "PARTLY_CREATED", partly_text(part, ad), recovery(pid, cap, conn, missing, appr, plan, apv)
        if shared and not shared["available"]:  # judges: never Reconnect, never a dead end; exports are the next step
            return "UNAVAILABLE_IN_THIS_DEPLOYMENT", shared["why"], ["review"]
        if st in ("FAILED", "REFUSED") and not newer_approval:  # the result stays; its permitted recovery is offered
            return "FAILED", f"{last['error']}." + ("" if shared else " Fix and approve again."), \
                recovery(pid, cap, conn, missing, appr, plan, apv)
        if st == "NOT_EXECUTED_BY_FOUNDER" and not newer_approval:
            return ("READY_TO_APPROVE", "You said the last attempt didn't happen. Approve this version again to try once more.",
                    recovery(pid, cap, conn, missing, appr, plan, apv))
    if cap["deployment"] != "CONFIGURED" and not simulated(pid):
        return "UNAVAILABLE_IN_THIS_DEPLOYMENT", "Direct delivery unavailable in this deployment.", ["review"]
    if shared and not shared["available"]:
        return "UNAVAILABLE_IN_THIS_DEPLOYMENT", shared["why"], ["review"]
    if not cap["can_connect"] and not simulated(pid):
        if cap["platform_access"] == "PENDING":
            return "PLATFORM_VERIFICATION_PENDING", "Platform verification pending.", ["review"]
        return "PLATFORM_APPROVAL_REQUIRED", "Platform approval required.", ["review"]
    if not conn or conn["state"] != "ACTIVE":
        return ("CONNECT_YOUR_ACCOUNT", "Reconnect your account. Your package and approval are kept." if conn else
                f"Connect your {ad.ACCOUNT_NOUN}.", ["connect"])
    if not conn.get("account_identity"):
        return "CONNECT_YOUR_ACCOUNT", f"Choose the {ad.ACCOUNT_NOUN} to use.", ["choose_account"]
    if missing or plan["blockers"]:
        n = len(plan["blockers"])
        return "NEEDS_INFORMATION", f"Needs information: {n} item{'s' * (n != 1)} before direct delivery.", ["review"]
    if appr == "APPROVED" and apv and apv["use"] == "execute" and apv["plan_hash"] == plan["plan_hash"]:
        if part := partly_of(last):
            return ("APPROVED", f"Approved v{pkg['version']}. Creates only what's missing ({', '.join(part['missing'])}); "
                                f"what was created before is reused.", ["execute"])
        return "APPROVED", f"Approved v{pkg['version']}. Ready to {ad.VERB_NOW.lower()}.", ["execute"]
    return "READY_TO_APPROVE", "Ready for your approval of this exact version.", ["approve"]


OBJECT_ORDER = ("campaign", "adset", "adgroup", "ad", "identity", "image", "creative")
OBJECT_NAME = {"campaign": "Campaign", "adset": "ad set", "adgroup": "ad group"}


def partly_of(a: dict | None) -> dict | None:
    """B13: a live attempt rejected after some objects were created. Those objects stay (never deleted by Landfall)."""
    if not a or a["mode"] != "LIVE" or a["status"] != "FAILED" or not a.get("partial"):
        return None
    steps = [*(a.get("reused") or {}), *(s["step"] for s in a["steps"])]
    objs = (a.get("verification") or {}).get("objects") or {}
    paused = bool(objs) and all(o.get("operation_status") == "DISABLE" if "operation_status" in o else
                                o.get("status") == "PAUSED" and o.get("effective_status") != "ACTIVE" for o in objs.values())
    return {"created": a["partial"], "missing": [x for x in steps if x not in a["partial"]], "paused": paused, "error": a.get("error")}


def partly_text(part: dict, ad) -> str:
    key = lambda x: OBJECT_ORDER.index(x) if x in OBJECT_ORDER else 99  # noqa: E731
    done = " · ".join(f"{OBJECT_NAME.get(x, x)} ✓" for x in sorted(part["created"], key=key))
    left = ", ".join(OBJECT_NAME.get(x, x) for x in sorted(part["missing"], key=key))
    state = "Everything created is paused." if part["paused"] else f"Landfall couldn't confirm it is paused: check it in {ad.WHERE}."
    return f"Partly created · {done}. {state} Not created: {left} ({part['error']})."


def recovery(pid: str, cap: dict, conn: dict | None, missing: list, appr: str, plan: dict, apv: dict | None) -> list[str]:
    """The next permitted step after a failed or closed attempt: reconnect, finish the account, fix, approve, or run."""
    if cap["deployment"] != "CONFIGURED" and not simulated(pid):
        return ["review"]
    if not conn or conn["state"] != "ACTIVE":
        return ["connect"]
    if not conn.get("account_identity"):
        return ["choose_account"]
    if missing or plan["blockers"]:
        return ["review"]
    if appr == "APPROVED" and apv and apv["use"] == "execute" and apv["plan_hash"] == plan["plan_hash"]:
        return ["execute"]
    return ["approve"]


def destination_view(pid: str, pkg: dict, dst: str) -> dict:
    ad = adapters()[dst]
    cap = capability(dst)
    conn = conn_for(pid, dst)  # the same resolver as the plan and execution [P67-R5]
    shared = shared_view(pid, dst, conn)
    missing = unresolved(pkg)[dst]
    plan = provider_plan(pid, pkg, dst)
    apvs = approvals(pid, pkg["id"], dst)
    apv = apvs[-1] if apvs else None
    appr = approval_state(pkg, apv, plan)
    acts = latest_actions(pid, pkg["id"], dst)
    pv = db.get_record(f"pv_{pkg['id']}_{dst.lower()}")
    pv_state = ("NOT_SUPPORTED" if not ad.VALIDATES else "NOT_RUN" if not pv or pv["plan_hash"] != plan["plan_hash"]
                else pv["result"])
    summary, text, nxt = summary_of(pid, pkg, dst, cap, conn, missing, appr, plan, acts, apv, shared)
    manuals = manuals_of(pid, pkg["id"], dst)
    live = [m for m in manuals if not m.get("withdrawn_at")]
    last = acts[-1] if acts else None
    rt = routing_view(pid, pkg) if dst == "EMAIL" else None
    return {
        "destination": dst, "label": ad.LABEL, "configuration": ad.CONFIGURATION if not rt else
        "One test message you approve (subject, body, attachments), sent from the Landfall test sender to the private test inbox only", "summary": summary, "text": text, "next": nxt,
        "manual": live[-1]["manual_kind"] if live else None, "manual_records": manuals,
        "prepared": ad.prepared(pkg), "usable_today": ad.usable_today(pkg),
        "state": {"adapter": cap["adapter"], "deployment": cap["deployment"], "missing_config": cap["missing_config"],
                  "platform_access": cap["platform_access"], "access_note": cap["access_note"],
                  "connection": ("SIMULATED" if conn and conn.get("simulated") else conn["state"] if conn else
                                 "NOT_CONNECTED" if cap["can_connect"] or simulated(pid) else "NOT_AVAILABLE"),
                  "account": conn and conn.get("account_label"),
                  "accounts": [] if is_shared(conn) else (conn or {}).get("accounts", []),
                  "pages": [] if is_shared(conn) else ((conn or {}).get("user") or {}).get("pages", []),
                  "package": "NEEDS_INFORMATION" if missing else "VALID", "missing": missing,
                  "provider_validation": pv_state, "provider_validation_message": pv and pv.get("message"),
                  "approval": appr, "approval_id": apv and apv["id"], "approval_use": apv and apv["use"],
                  "approval_version": apv and apv["package_version"],
                  "execution": last["status"] if last else "NONE", "simulated": simulated(pid)},
        "plan": {**{k: plan[k] for k in ("values", "blockers", "plan_hash", "label")}, "requests": strip_bulk(plan["requests"])},
        "actions": [{k: a.get(k) for k in ("id", "status", "mode", "steps", "ids", "result", "verification", "error", "reference",
                                            "at", "partial", "found", "resolution", "reused")} for a in acts],
        "setup_steps": ad.setup_steps(pkg), "where": ad.WHERE, "verb": ad.VERB, "partly": partly_of(last),
        "routing": rt, "test": bool(last and is_test(last)), "shared": shared}


def package_view(pid: str, pkg_id: str) -> dict | None:
    pkg = db.get_record(pkg_id)
    if not pkg or pkg["project_id"] != pid or pkg["kind"] != "package":
        run = db.q("SELECT status, detail FROM runs WHERE project_id=? AND kind='delivery_prepare' AND input_key LIKE ? "
                   "ORDER BY started_at DESC, rowid DESC LIMIT 1", pid, f"drf_{pkg_id[4:]}:%")
        if not run:
            return None
        return {"id": pkg_id, "preparing": {"status": run[0]["status"], "message": json.loads(run[0]["detail"] or "{}").get("message")}}
    dr = db.get_record(pkg["source"]["id"])
    stale = stale_of(pid, pkg)
    from . import plans
    fence = "This package was set aside." if pkg.get("status") == "set_aside" else plans.fence(pid, pkg["city_id"], pkg["method"])
    arts = []
    for a in pkg["assets"]:
        art = db.get_record(a["artifact"]["id"])
        arts.append({**a, **{k: art[k] for k in ("filename", "media_type", "size", "source", "rights", "usage_constraints")},
                     "readable": artifact_bytes(a["artifact"]) is not None,
                     "rights_confirmed": a["artifact"]["sha256"] in pkg.get("rights_confirmed", [])})
    exports = [e for e in db.records("export", pid) if e["package_id"] == pkg_id]
    run = db.q("SELECT status, detail FROM runs WHERE project_id=? AND kind='delivery_prepare' AND input_key LIKE ? "
               "ORDER BY started_at DESC, rowid DESC LIMIT 1", pid, f"{pkg['source']['id']}:%")
    subject, body = email_text(pkg)
    return {**{k: v for k, v in pkg.items() if k not in ("inputs", "project_id", "kind")}, 
            "assets": arts, "stale": stale, "info": info_items(pkg), "exports": exports[-10:], "budget_view": budget_view(pid, pkg),
            "status": pkg.get("status", "active"), "fence": fence, "plan_ref": pkg.get("plan_ref"),
            "earlier_wording": pkg.get("earlier_wording") or {}, "excluded": pkg.get("excluded", []),
            "email": {"subject": subject, "body": body, "mailto": mailto(pkg)} if pkg["flow"] == "outreach" else None,
            "draft": {"id": dr["id"], "version": dr["version"], "label": dr["label"]},
            "preparing": run and run[0]["status"] in ("queued", "running") and {"status": run[0]["status"]},
            "destination_states": (states := [destination_view(pid, pkg, dst) for dst in pkg["destinations"]]),
            "supporting_copy": "Landfall has prepared the materials below. Direct delivery is not available yet. "
                               "You can review, download or copy them to use yourself."
            if all(d["summary"] in BLOCKED for d in states) else
            "Review exactly what will be sent or created, then approve that version. You can also download or copy it."}


BLOCKED = ("UNAVAILABLE_IN_THIS_DEPLOYMENT", "PLATFORM_APPROVAL_REQUIRED", "PLATFORM_VERIFICATION_PENDING")


def package_for_draft(pid: str, draft_id: str) -> dict | None:
    p = db.get_record(package_id_of(draft_id))
    return p and {"id": p["id"], "version": p["version"]}


def packages_for_draft(pid: str, draft_id: str) -> list[dict]:
    """Every recipient's package of one draft [P65-R2], keyed by recipient key (null: no chosen recipient, or digital)."""
    base = package_id_of(draft_id)
    return [{"id": p["id"], "version": p["version"], "rk": p["id"].split("~")[1] if "~" in p["id"] else None,
             "partner": p.get("partner") and {k: p["partner"].get(k) for k in ("lead_id", "lead_key", "name", "basis")},
             "status": p.get("status", "active")}
            for p in packages_of(pid) if p["id"] == base or p["id"].startswith(base + "~")]


def stale_of(pid: str, pkg: dict) -> list[str]:
    """The staleness chain (6.5 UI.5): the draft and its investigation, the claims its copy cites, and its partner."""
    out = []
    dr = db.get_record(pkg["source"]["id"])
    if dr["version"] != pkg["source"]["version"]:
        out.append(f"The draft has changed since (now v{dr['version']})")
    src = db.get_record(pkg["source"]["id"], pkg["source"]["version"])
    d = db.get_record(src["source"]["dossier_id"], src["source"]["dossier_version"])
    out += [f"Built on research that changed: {r[0].lower()}{r[1:]}" for r in specialist.stale_of(pid, d)]
    if any((db.get_record(c) or {}).get("version") != v for c, v in (pkg.get("claim_versions") or {}).items()):
        out.append("A fact its copy cites changed")
    if (why := signed_money_changed(pid, pkg)):
        out.append(why)
    part = pkg.get("partner") or {}
    latest = db.get_record(src["source"]["dossier_id"])
    if part.get("lead_key") and latest:
        c = next((c for c in latest["candidates"] if c.get("ref") == part["lead_key"]), None)
        gone = (c or {}).get("decision") == "REJECTED" or (part.get("from_candidate") and not c)
        if gone:  # P66-R2: a standalone lead is stale only when research explicitly rejects it
            out.append(f"{part['name']} is no longer kept by the latest research")
    return out


def signed_money_changed(pid: str, pkg: dict) -> str | None:
    """§5.10: a package prepared from an earlier signed version whose method's lines, allocations or scenario differ in
    the latest signed version. Re-preparing writes v+1, which needs a new approval; approved bytes never change."""
    from . import plans, scenarios
    ref = pkg.get("plan_ref")
    signed = ref and plans.latest_signed(pid, pkg["city_id"])
    if not signed or signed["version"] == ref["version"]:
        return None
    old = db.get_record(ref["id"], ref["version"])

    def money_of(p):
        lines = sorted((json.dumps(ln, sort_keys=True) for ln in p.get("cost_lines") or [] if pkg["method"] in ln["used_by"]))
        scn = pkg["method"] == "digital" and scenarios.of_plan(p)
        return lines, scn and (scn["total_daily"], [(x["platform"], x["allocation"]) for x in scn["platforms"]], scn["horizon_days"])
    if money_of(old) != money_of(signed):
        return f"Your signed plan's budget changed (v{ref['version']} → v{signed['version']}) · Re-prepare"
    return None
