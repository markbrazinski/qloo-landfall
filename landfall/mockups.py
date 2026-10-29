"""Slice 6.5 increment D: launch-kit mockups (BUILD-PLAN UI.13; CONTRACTS B10 ConceptPreview).

A mockup is two layers. The scene is generated once per prompt and saved; the page lays the brand's real product images,
brand, city and concept over it, with an "Illustrative concept" label. The model never draws the product or any text.
Nothing regenerates by itself: reading, reloading and restarting never dispatch, and an unchanged prompt reuses its scene.
The image model is DOCS-only until probe P65-MK1 (UI.13); its request and response shapes below are UNVERIFIED.

Since v4 (D3a, 27 Oct 2026) the page composes the Campaign Mockup kinds from `kit_of`; since @2 (owner, 27 Oct 2026)
the scene is the photographic background of the story ads and the social post: a lived-in setting with people in soft
focus, the brand's real product photos laid over it.
"""
import base64
import hashlib
import json
import time
from functools import lru_cache

from . import config, db, delivery, ledger, markets, runner
from .runner import HANDLERS, Run

PROMPT_ID = "mockup_scene@4"  # @2: people in the setting, used as the ad background; @3: the ad scene styled from Qloo (owner, 28 Oct 2026)
# @4: the ad scene in the brand's own look and the Qloo mood, bold, never an everyday street; nothing brand-specific is
# hard-coded (owner, 28 Oct 2026: "a judge tries My Little Pony and gets Death Wish stuff")
# Gemini when its key is set (owner, 27 Oct 2026), else Stability on Bedrock (blocked by IAM in P65-MK1 attempt 1).
# Gemini by default, key or not, so a demo replays its recorded images on any machine (Stability on Bedrock is blocked
# by IAM, P65-MK1, and stays selectable with LANDFALL_IMAGE_MODEL); without a key a live image fails as FAILED, never silently.
MODEL = config.env("LANDFALL_IMAGE_MODEL", "gemini-nano-banana-2.1")
REGION = config.env("LANDFALL_IMAGE_REGION", "global" if MODEL.startswith("gemini") else "us-west-2")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
# ai.google.dev/gemini-api/docs/pricing (updated 2026-10-26): gemini-nano-banana-2.1, standard tier, $0.0336 per 1K image.
# The Bedrock figure is an admission estimate only (third-party pages: ≈ $0.04).
EXPOSURE_USD = 0.0336 if MODEL.startswith("gemini") else 0.10
NEGATIVE = ("text, letters, words, numbers, logo, brand name, signage, watermark, label, product packaging, close-up face, "
            "portrait, person looking at the camera")
INCLUDED = ("MAIN", "SUPPORTS")
DEADLINE_S = 150  # one scene, end to end
# method → (treatment, frame, scene); the frame is the page's layout, rebuilt from the design's LF-CampaignMockup kinds
TREATMENTS = {
    "retail": ("RETAIL_DISPLAY", "shelf", "a busy independent shop in {city}, shoppers browsing in the background"),
    "popup": ("POPUP_ACTIVATION", "stand", "a lively weekend pop-up market in {city}, people browsing the stalls"),
    "creator": ("CREATOR_BRIEF", "social", "a candid everyday moment in {city} for people who like {category}"),
    "community": ("COMMUNITY_COLLAB", "flyer", "a friendly community get-together in {city}, people chatting"),
    "digital": ("DIGITAL_CREATIVE", "ad", "a vibrant street in {city} where people who like {category} spend time, passers-by"),
}


class Refused(Exception):
    def __init__(self, code: int, detail: str):
        self.code, self.detail = code, detail


def _plan(pid: str, city: str, signed: bool) -> dict | None:
    """The latest (signed) Plan version for a city (CONTRACTS B10)."""
    rows = db.q("SELECT version FROM records WHERE id=? ORDER BY version DESC", f"plan_{pid}_{city}")
    for r in rows:
        p = db.get_record(f"plan_{pid}_{city}", r["version"])
        if not signed or p.get("signed_off"):
            return p
    return None


def _category(refs: list[dict]) -> str:
    terms = [t for ref in refs for t in (db.get_record(ref["id"], ref["version"]) or {}).get("category_terms", [])]
    return terms[0] if terms else "the brand's products"


def _offered(pid: str, city: str, method: str, dr: dict) -> list[dict]:
    """The products the method offers now [P65-I4]: those its active packages keep (the founder's selection), else the
    draft's; each at its current version, so an Update composes what is current."""
    pkgs = [p for p in delivery.packages_of(pid, city, method) if p.get("status", "active") == "active"]
    keep = {x["id"] for p in pkgs for x in p["products"]} if pkgs else None
    return [{"id": r["id"], "version": (db.get_record(r["id"]) or r)["version"]} for r in dr.get("products", [])
            if keep is None or r["id"] in keep]


def scene_prompt(method: str, city: str, category: str, style: dict | None = None, brand: str | None = None) -> str:
    """The ad background (digital) takes its look from the brand's own and its setting and people from the brand's Qloo
    taste in the city and its audiences (delivery.qloo_style), e.g. racing fans or gamers: a look, never a claim about
    who buys. Owner, 28 Oct 2026: bold and extreme (a black background for Death Wish), never a generic street."""
    _, _, scene = TREATMENTS[method]
    name = markets.CITIES[city]["name"]
    if method == "digital" and style and (style.get("themes") or style.get("audiences")):
        who = style.get("people") or style.get("audiences")
        return (f"A bold, striking advertising photograph in {name}"
                + (f" in the visual style and colors of the brand {brand}" if brand else "")
                + (f", with the mood of {', '.join(style['themes'])}" if style.get("themes") else "")
                + (f"; people into {', '.join(who).lower()} enjoying {category}" if who else "")
                + ". Lighting, colors and energy that fit that brand and mood, pushed to an extreme, never a plain "
                "everyday street; people in soft focus and nobody looking at the camera; the lower third calm so products "
                "can sit over it. No text, no logos, no product packaging.")
    return (scene.format(city=name, category=category)
            + ". Photorealiztic, natural light, shallow depth of field, people in soft focus and nobody looking at the "
            "camera, the lower third calm so products can sit over it. No text, no logos, no product packaging.")


def _inputs(pid: str, city: str, method: str, plan: dict) -> dict:
    pin = next(m for m in plan["methods"] if m["method"] == method)
    dr = db.get_record(pin["draft"]["id"], pin["draft"]["version"])
    brief = db.latest("brief", pid) or {}
    refs = _offered(pid, city, method, dr)
    prompt = scene_prompt(method, city, _category(refs), delivery.qloo_style(pid, city) if method == "digital" else None,
                          brief.get("brand_name"))
    return {"plan_ref": {"id": plan["id"], "version": plan["version"]}, "draft": dict(pin["draft"]), "method": method,
            "treatment": TREATMENTS[method][0], "frame": TREATMENTS[method][1],
            "overlay": {"brand": brief.get("brand_name"), "city": markets.CITIES[city]["name"],
                        "concept": {"name": dr["concept"]["name"], "line": dr["concept"].get("line")}},
            "product_refs": refs,
            "scene": {"prompt": prompt, "prompt_id": PROMPT_ID, "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(),
                      "model_id": MODEL, "region": REGION, "seed": int(hashlib.sha256(f"{pid}{city}{method}".encode()).hexdigest()[:8], 16) % 2**31}}


def _record(pid: str, city: str, method: str) -> dict | None:
    return db.get_record(f"cpv_plan_{pid}_{city}_{method}")


def _saved_scene(pid: str, digest: str) -> dict | None:
    """A scene already generated for this exact prompt, in any of the project's mockups: reused, never paid again."""
    for r in db.records("concept_preview", pid):
        if r["scene"]["prompt_digest"] == digest and r.get("image"):
            return r
    return None


def render_scene(cassette, prompt: str, seed: int) -> dict:
    """One text-to-image call through the cassette and the ledger. Returns {"b64", "media_type"}."""
    if MODEL.startswith("gemini"):
        return _gemini_scene(cassette, prompt)
    request = {"model": MODEL, "region": REGION, "prompt": prompt, "negative_prompt": NEGATIVE, "seed": seed,
               "aspect_ratio": "3:2", "output_format": "jpeg"}

    def live():
        import boto3
        rid = ledger.reserve("bedrock", "InvokeModel", exposure_usd=EXPOSURE_USD, model=MODEL, stage="mockup")
        try:
            client = boto3.Session(profile_name=config.AWS_PROFILE).client("bedrock-runtime", region_name=REGION)
            body = {k: v for k, v in request.items() if k not in ("model", "region")}
            out = json.loads(client.invoke_model(modelId=MODEL, body=json.dumps(body))["body"].read())
        except Exception as e:
            # AWS refusing the request itself (access, a bad body) is definitive and never billed; anything else may
            # have run, so the full exposure stays counted
            code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
            refused = code in ("AccessDeniedException", "ValidationException", "ResourceNotFoundException")
            ledger.finalize(rid, "bedrock", "InvokeModel", est_usd=0.0 if refused else EXPOSURE_USD, outcome=code or "error",
                            cost_known=refused, error=type(e).__name__, model=MODEL, stage="mockup")
            raise
        ledger.finalize(rid, "bedrock", "InvokeModel", est_usd=EXPOSURE_USD, outcome="200", cost_known=False, model=MODEL,
                        stage="mockup")  # ponytail: the price is the estimate until P65-MK1 records the real one
        return {"b64": out["images"][0], "media_type": "image/jpeg", "finish_reasons": out.get("finish_reasons")}
    return cassette.call("bedrock", "InvokeModel", request, live, stage="mockup")


def _post(url: str, json=None, headers=None, timeout=None):
    """httpx.post with a total deadline: `timeout` limits each read, and a trickling response once held a run for 36 min
    (27 Oct 2026)."""
    import httpx
    t0 = time.monotonic()
    with httpx.stream("POST", url, json=json, headers=headers, timeout=timeout) as r:
        body = b""
        for c in r.iter_raw():  # raw: the Response below decodes once, with the original headers
            if time.monotonic() - t0 > DEADLINE_S:
                raise httpx.ReadTimeout(f"no complete image within {DEADLINE_S} s")
            body += c
    return httpx.Response(r.status_code, headers=r.headers, content=body)


def _gemini_scene(cassette, prompt: str) -> dict:
    """Gemini's Interactions API (ai.google.dev, read 27 Oct 2026). Not stored on Google's side (`store: false`).
    The raw response shape is documented only through the SDK (steps → model_output → image block `data`): UNVERIFIED
    until the probe. 16:9 is the documented ratio; the frame crops it."""
    import httpx
    body = {"model": MODEL, "input": f"{prompt} Avoid: {NEGATIVE}.", "store": False,
            "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "16:9", "image_size": "1K"}}

    def live():
        rid = ledger.reserve("gemini", "interactions.create", exposure_usd=EXPOSURE_USD, model=MODEL, stage="mockup")
        try:
            r = _post(GEMINI_URL, json=body, timeout=120,
                      headers={"x-goog-api-key": config.env("gemini_api_key") or "", "Content-Type": "application/json"})
        except httpx.TransportError as e:  # it may have run: the full price stays counted
            ledger.finalize(rid, "gemini", "interactions.create", est_usd=EXPOSURE_USD, outcome="transport_error",
                            cost_known=False, error=type(e).__name__, model=MODEL, stage="mockup")
            raise
        data = None
        if r.status_code == 200:
            out = r.json()
            blocks = [b for st in out.get("steps", []) if st.get("type") == "model_output" for b in st.get("content", [])]
            data = next((b.get("data") for b in blocks if b.get("type") == "image" and b.get("data")), None) \
                or (out.get("output_image") or {}).get("data")
        # a refusal (4xx) is never billed; a 200 without an image is billed as generated output may have been
        ledger.finalize(rid, "gemini", "interactions.create", est_usd=EXPOSURE_USD if r.status_code == 200 else 0.0,
                        outcome=str(r.status_code), cost_known=True, model=MODEL, stage="mockup")
        if not data:
            raise RuntimeError(f"Gemini returned no image (HTTP {r.status_code}): {r.text[:200]}")
        return {"b64": data, "media_type": "image/jpeg"}
    return cassette.call("gemini", "interactions.create", body, live, stage="mockup")


def _product_images(run: Run, pid: str, dr: dict) -> list[dict]:
    """The real product images: saved artifacts for the offered products' pages, fetched once if none exist yet."""
    pages = {db.get_record(p["id"], p["version"])["product_url"]: p for p in dr["products"]}
    arts = {a["source"].get("page"): a for a in db.records("artifact", pid) if a["source"].get("kind") == "FETCHED"}
    if pages and not any(u in arts for u in pages):
        delivery.fetch_images(run, pid, dr)
        arts = {a["source"].get("page"): a for a in db.records("artifact", pid) if a["source"].get("kind") == "FETCHED"}
    out = []
    for url, p in pages.items():
        prod = db.get_record(p["id"], p["version"])
        if delivery.GIFT.search(prod["title"]):
            continue
        a = arts.get(url)
        out.append({"id": p["id"], "version": p["version"], "title": prod["title"],
                    "artifact": {"sha256": a["sha256"], "media_type": a["media_type"]} if a else None})
    return out


def _store(pid: str, city: str, method: str, inp: dict, payload: dict) -> dict:
    alt = (f"Illustrative concept: {inp['overlay']['brand']} {inp['overlay']['concept']['name']} in {inp['overlay']['city']} "
           f"({TREATMENTS[method][1]}). Not a real installation, post or partnership.")
    return db.put_record("concept_preview", pid, {**inp, "labels": ["ILLUSTRATIVE_CONCEPT"], "alt_text": alt, **payload},
                         rid=f"cpv_plan_{pid}_{city}_{method}")


def mockup_run(run: Run):
    pid, city = run.project_id, run.params["city"]
    plan = db.get_record(run.params["plan_id"], run.params["plan_version"])
    if run.params.get("prewarm"):  # an unsigned plan pins no drafts yet: the scenes use the current ones
        plan = {**plan, "methods": [{**m, "draft": m.get("draft") or {"id": d["id"], "version": d["version"]}}
                                    for m in plan["methods"] for d in [db.get_record(f"drf_{pid}_{city}_{m['method']}")]
                                    if d or m.get("draft")]}
    failed = []
    for method in run.params["methods"]:
        inp = _inputs(pid, city, method, plan)
        dr = {**db.get_record(inp["draft"]["id"], inp["draft"]["version"]), "products": inp["product_refs"]}
        try:
            products = run.step(f"{method}:products", lambda: _product_images(run, pid, dr))
            reuse = _saved_scene(pid, inp["scene"]["prompt_digest"])
            if reuse:
                payload = {"image": reuse["image"], "reused_from": {"id": reuse["id"], "version": reuse["version"]}, "cost": None}
            else:
                _store(pid, city, method, inp, {"state": "GENERATING", "products": products, "image": None})
                run.emit("mockup", {"method": method, "state": "GENERATING"})
                out = run.step(f"{method}:scene", lambda: render_scene(run.cassette, inp["scene"]["prompt"], inp["scene"]["seed"]))
                data = base64.b64decode(out["b64"])
                art = delivery.save_artifact(pid, data, f"mockup-{city}-{method}.jpg", out["media_type"],
                                             {"kind": "GENERATED", "provider": "bedrock", "model_id": MODEL,
                                              "prompt_digest": inp["scene"]["prompt_digest"]},
                                             "generated_illustrative", ["Illustrative concept; never a real installation, post or partnership"])
                payload = {"image": {"artifact": art, "media_type": out["media_type"]}, "cost": {"est_usd": EXPOSURE_USD}}
            _store(pid, city, method, inp, {"state": "READY", "products": products, "error": None, **payload})
        except ledger.BudgetExhausted as e:
            failed.append(method)
            _store(pid, city, method, inp, {"state": "OUT_OF_BUDGET", "image": None, "products": [],
                                            "error": {"reason": str(e), "retryable": True}})
        except Exception as e:  # one mockup's failure never touches the plan, the checklist or the other mockups
            failed.append(method)
            _store(pid, city, method, inp, {"state": "FAILED", "image": None, "products": [],
                                            "error": {"reason": f"{type(e).__name__}: {str(e)[:160]}", "retryable": True}})
        run.emit("mockup", {"method": method})
    return ("partial", f"{len(failed)} mockup(s) didn't finish: {', '.join(failed)}") if failed else None


def stale_of(pid: str, rec: dict) -> list[str]:
    """Why a saved mockup no longer matches its plan; computed now, never acted on by itself."""
    out = []
    city = rec["plan_ref"]["id"].split("_")[-1]
    plan = _plan(pid, city, signed=True)
    pin = next((m for m in (plan or {}).get("methods", []) if m["method"] == rec["method"]), None)
    if pin and pin.get("draft") and pin["draft"] != rec["draft"]:
        out.append("The plan was signed again with a newer draft")
    if (db.latest("brief", pid) or {}).get("brand_name") != rec["overlay"]["brand"]:
        out.append("Your brand name changed")
    now = _offered(pid, city, rec["method"], db.get_record(rec["draft"]["id"], rec["draft"]["version"]))
    if [p["id"] for p in now] != [p["id"] for p in rec.get("product_refs", [])]:
        out.append("The products you're offering changed")
    elif now != rec.get("product_refs", []):
        out.append("A product changed")
    if (rec.get("scene") or {}).get("prompt_id") != PROMPT_ID:
        out.append("A newer scene style is available")
    return out


def start(pid: str, city: str, methods: list[str] | None = None, dry_run: bool = False) -> dict:
    """POST …/plans/{city}/mockups: generate what's missing, failed or out of date; cost shown first (dry_run)."""
    plan = _plan(pid, city, signed=True)
    if not plan:
        raise Refused(409, "Sign off the plan first.")
    included = [m["method"] for m in plan["methods"] if m["role"] in INCLUDED and m.get("draft")]
    want = [m for m in (methods or included) if m in included]
    need, new_scenes = [], 0
    for m in want:
        cur = _record(pid, city, m)
        if cur and cur["state"] in ("READY", "GENERATING") and not stale_of(pid, cur) and cur["plan_ref"]["version"] == plan["version"]:
            continue
        need.append(m)
        new_scenes += _saved_scene(pid, _inputs(pid, city, m, plan)["scene"]["prompt_digest"]) is None
    est = {"mockups": len(need), "new_scenes": new_scenes, "est_usd": round(new_scenes * EXPOSURE_USD, 2)}
    if dry_run or not need:
        return {"run_id": None, "est": est}
    n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='mockup'", pid)[0]["n"]
    rid = runner.start(pid, "mockup", f"{plan['id']}:v{plan['version']}:{','.join(need)}:{n}",
                       {"city": city, "plan_id": plan["id"], "plan_version": plan["version"], "methods": need})
    return {"run_id": rid, "est": est}


def prewarm(pid: str, city: str) -> str | None:
    """Owner, 28 Oct 2026: the launch kit's scenes start in the background once the top recommended city's plan is
    drafted (autoplan), not at sign-off. Same prompts (@2), deadline and ledger as `start`; sign-off's mockups then
    reuse each scene by its prompt digest (`_saved_scene`) and only re-compose. Nothing here is shown as signed."""
    # ponytail: a scene still generating when the founder signs is generated again by sign-off's run (one extra image)
    plan = _plan(pid, city, signed=False)
    methods = [m["method"] for m in (plan or {}).get("methods", [])
               if m["role"] in INCLUDED and db.get_record(f"drf_{pid}_{city}_{m['method']}")]
    if not methods:
        return None
    return runner.start(pid, "mockup", f"{plan['id']}:v{plan['version']}:prewarm",
                        {"city": city, "plan_id": plan["id"], "plan_version": plan["version"], "methods": methods,
                         "prewarm": True})


def kit_of(pid: str, city: str, rec: dict) -> dict:
    """What the page composes the v4 launch-kit tiles from (D3a, 27 Oct 2026), read now and never generated: the offered
    products with any saved photo (no fetch), the method's own copy from its active package, and its partner's name."""
    arts = {a["source"].get("page"): a for a in db.records("artifact", pid) if a["source"].get("kind") == "FETCHED"}
    products = []
    for ref in _offered(pid, city, rec["method"], db.get_record(rec["draft"]["id"], rec["draft"]["version"])):
        prod = db.get_record(ref["id"], ref["version"]) or {}
        if prod.get("title") and not delivery.GIFT.search(prod["title"]):
            a = arts.get(prod.get("product_url"))
            products.append({"title": prod["title"], "artifact": a and {"sha256": a["sha256"], "media_type": a["media_type"]}})
    pkgs = [p for p in delivery.packages_of(pid, city, rec["method"]) if p.get("status", "active") == "active"]
    c = next((p["copy"] for p in pkgs if p.get("flow") == "digital"), None)
    copy = c and {"headlines": [h["text"] for h in c.get("headlines") or []], "primary_text": (c.get("primary_text") or {}).get("text"),
                  "cta": c.get("cta")}
    limited = any(not x["artifact"] for x in products) and bool(
        db.q("SELECT 1 FROM sources WHERE project_id=? AND status IN (429, 503) LIMIT 1", pid))
    return {"products": products, "copy": copy, "partner": next((p["partner"]["name"] for p in pkgs if p.get("partner")), None),
            "image_why": "your site limited our reader, so no product photo was saved yet" if limited else None}


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(c[:3])


def _on(c) -> str:
    """Text that reads on c: near-black on light colors, off-white on dark."""
    r, g, b = c[:3]
    return "#111111" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 150 else "#f7f5f0"


@lru_cache(maxsize=64)
def _looks(sha: str) -> tuple | None:
    """The three Launch ads' looks (background, text, button, button text) taken from the scene's own colors, so they
    follow the brand and its Qloo taste: its two main colors, then its most vivid one for the colour ad and the buttons."""
    import io
    import colorsys
    from PIL import Image
    data = delivery.artifact_bytes({"sha256": sha})
    if not data:
        return None
    try:
        q = Image.open(io.BytesIO(data)).convert("RGB").resize((64, 64)).quantize(colors=6)
    except (OSError, ValueError):  # not a picture Pillow reads: the neutral looks stand
        return None
    pal = q.getpalette()
    cols = [(n, tuple(pal[i * 3:i * 3 + 3])) for n, i in sorted(q.getcolors(), reverse=True)]
    main = [c for _, c in cols]
    vivid = max(main, key=lambda c: (lambda h, s, v: s * v)(*colorsys.rgb_to_hsv(*(x / 255 for x in c))))
    a, b = main[0], (main[1] if len(main) > 1 else main[0])
    return ((_hex(a), _on(a), _hex(vivid), _on(vivid)), (_hex(b), _on(b), _hex(vivid), _on(vivid)),
            (_hex(vivid), _on(vivid), _hex(a), _on(a)))


def looks_of(rec: dict | None) -> list | None:
    img = (rec or {}).get("image")
    return [list(x) for x in _looks(img["artifact"]["sha256"])] if img and _looks(img["artifact"]["sha256"]) else None


def view(pid: str, city: str) -> list[dict]:
    """GET …/plans/{city}/mockups: the saved mockups, each with why it needs an update, and whether it belongs to an
    earlier plan (its method is no longer MAIN or SUPPORTS in the latest version)."""
    latest = _plan(pid, city, signed=False)
    included = {m["method"] for m in (latest or {}).get("methods", []) if m["role"] in INCLUDED}
    out = []
    for method in TREATMENTS:
        rec = _record(pid, city, method)
        if rec:
            out.append({**{k: rec[k] for k in rec if k not in ("inputs", "kind", "project_id")},
                        "stale": stale_of(pid, rec), "earlier_plan": method not in included, "kit": kit_of(pid, city, rec),
                        "looks": looks_of(rec)})
    return out


HANDLERS["mockup"] = mockup_run
