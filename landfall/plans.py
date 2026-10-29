"""Slice 6.5 · the Plan for the beachhead city, its sign-off, the earlier-plan fence and the Act checklist
(BUILD-PLAN "Slice 6.5" P1–P9, A1–A6; CONTRACTS B10).

- Plan vN (`plan_{project}_{city}`): the methods the founder chose and their roles ("Selected by you" before slice 5).
  Any edit writes an unsigned version; sign-off pins the exact dossier and draft versions the founder saw [P65-R4].
- The fence [P65-R1]: a package's city must be the beachhead, and its method MAIN or SUPPORTS in both the latest
  plan (so a removal applies at once) and the latest signed plan (so an unsigned addition enables nothing).
- The checklist is a view over records; nothing about tasks is stored.

Slice 5 (BUILD-PLAN "Slice 5" §§5.4–5.10; CONTRACTS B7/B10 P5-R): the Plan is the only owner of cost lines. Every
COSTING item of every method in the plan has at least one line (NOT_KNOWN from code until someone prices it), a shared
line exists once with `used_by[]`, and every money write is Plan v+1, unsigned, taken against `base_version` inside one
`db.tx()`. A version pins the FX publication, the scenario and the adopted proposal, so a signed version's totals, fit
and allocations recompute the same whenever they are read.
"""
from . import db, delivery, markets, money, research, specialist
from .research import Refused

ROLES = ("MAIN", "SUPPORTS", "OPTIONAL", "NOT_NOW")
INCLUDED = ("MAIN", "SUPPORTS")
EARLIER = "This package belongs to an earlier plan."
PRE_PLAN_ROUTE = False  # tests of the slice-6 route set this; replay and demo projects need no switch [P65-I2]


def plan_id(pid: str, cid: str) -> str:
    return f"plan_{pid}_{cid}"


def beachhead(pid: str) -> str | None:
    return ((db.latest("shortlist", pid) or {}).get("beachhead") or {}).get("city_id")


def latest_signed(pid: str, cid: str) -> dict | None:
    cur = db.get_record(plan_id(pid, cid))
    for v in range(cur["version"] if cur else 0, 0, -1):
        rec = db.get_record(plan_id(pid, cid), v)
        if rec.get("signed_off"):
            return rec
    return None


def role_of(plan: dict | None, method: str) -> str | None:
    return next((m["role"] for m in (plan or {}).get("methods", []) if m["method"] == method), None)


def fence(pid: str, cid: str, method: str) -> str | None:
    """None when (city, method) may be prepared, approved, validated or executed; otherwise why not. Only replay or
    demo evidence (and tests of the slice-6 route) may use the pre-6.5 route without a beachhead and plan [P65-I2]."""
    bh = beachhead(pid)
    if not bh and not db.latest("plan", pid):
        if PRE_PLAN_ROUTE or delivery.simulated(pid):
            return None
        return "Choose a beachhead city in Markets and sign off a plan first."
    if cid != bh:
        return f"{EARLIER} {markets.CITIES[bh]['name'] if bh else 'No city'} is your beachhead now."
    if role_of(db.get_record(plan_id(pid, cid)), method) not in INCLUDED:
        return f"{EARLIER} {research.METHOD_LABELS[method]} isn't in your current plan."
    if role_of(latest_signed(pid, cid), method) not in INCLUDED:
        return f"{EARLIER} Sign off the plan with {research.METHOD_LABELS[method]} in it first."
    return None


def pin_of(pid: str, cid: str, method: str) -> tuple[dict | None, dict | None]:
    """The signed plan's pins for a method, and the plan reference a package records (`plan_ref`)."""
    signed = latest_signed(pid, cid)
    m = next((m for m in (signed or {}).get("methods", []) if m["method"] == method and m.get("draft")), None)
    return m, (signed and m and {"id": signed["id"], "version": signed["version"]})


# ---- slice 5: cost lines, launch-test sections, pins [P5-R2] ----
DEFAULT_FLOW = {"STOCK": "inventory", "DEPOSIT": "refundable_deposit"}
BASES = ("LISTED_RATE", "ESTIMATE", "ASSUMED", "YOU_SAID", "NOT_KNOWN", "NOT_COSTED")
CHANGED = "Your plan changed since you loaded it (now v{v})."
ROLE_LABEL = {"founder": "Selected by you", "strategist": "Proposed by Landfall · you adopted it"}


def items_of(method: str) -> list[tuple[str, str, str]]:
    """The method's fixed checklist of items to cost (specialist.COSTING, S5-1): (item_ref, label, default flow)."""
    return [(f"{method}:{kind}", label, DEFAULT_FLOW.get(kind, "expense")) for label, kind in specialist.COSTING[method]]


def line_id(item_ref: str) -> str:
    return "cl_" + item_ref.replace(":", "_").lower()


def code_line(item_ref: str, label: str, flow: str) -> dict:
    return {"id": line_id(item_ref), "label": label, "item_ref": item_ref, "amount": None, "basis": "NOT_KNOWN", "flow": flow,
            "used_by": [item_ref.split(":")[0]], "basis_note": None, "source_claim_ids": [], "by": "code"}


def complete(lines: list[dict], methods: list[str]) -> list[dict]:
    """No method is ever free by omission: a COSTING item no line covers gets a NOT_KNOWN line from code."""
    have = {ln.get("item_ref") for ln in lines}
    return list(lines) + [code_line(ref, label, flow) for m in methods for ref, label, flow in items_of(m) if ref not in have]


def latest_fx(pid: str) -> dict | None:
    """The newest ECB publication stored, by its publication date (never by write time, which can tie)."""
    return max(db.records("fx_rate", pid), key=lambda r: r["rate_date"], default=None)


def fx_pin(pid: str, lines: list[dict]) -> list[dict]:
    """The latest stored ECB publication, pinned when any line needs converting; USD-only plans pin none."""
    if not any(ln["amount"] and ln["amount"]["ccy"] != "USD" for ln in lines):
        return []
    fx = latest_fx(pid)
    return [{"id": fx["id"], "version": fx["version"]}] if fx else []


def fx_of(plan: dict) -> dict | None:
    pins = plan.get("fx") or []
    return db.get_record(pins[0]["id"], pins[0]["version"]) if pins else None


def ensure_fx(pid: str, lines: list[dict], retry: bool = False) -> dict | None:
    """Before a write (never inside its transaction): fetch the ECB publication once when a line needs converting and
    none is stored, or on Retry. A failed fetch stores nothing: the lines stay "not converted", never zero."""
    need = any(ln["amount"] and ln["amount"]["ccy"] != "USD" for ln in lines)
    have = latest_fx(pid)
    if not need or (have and not retry):
        return have
    from . import fetch, runner
    try:
        page = fetch.get(money.ECB_URL, runner.cassette_for(pid), "fx_ecb")
    except Exception:  # a replay miss, a refusal or a transport failure: unconverted, with Retry
        return have
    got = page.get("status") == 200 and money.parse_ecb(page.get("body") or page.get("text") or "")
    if not got:
        return have
    day, rates = got
    rid = f"fx_{pid}_{day}"
    return db.get_record(rid) or db.put_record("fx_rate", pid, {"base": "EUR", "rate_date": day, "rates": rates,
                                                                "fetched_at": db.now(), "source": "ECB"}, rid=rid)


def launch_test(pid: str, cid: str, m: dict, lines: list[dict], rationale: dict | None) -> dict | None:
    """The launch test is a section of each included method (P5-R2), drawn from its draft (the pinned one once signed)."""
    if m["role"] not in INCLUDED:
        return None
    pin = m.get("draft")
    dr = db.get_record(pin["id"], pin["version"]) if pin else db.get_record(f"drf_{pid}_{cid}_{m['method']}")
    change = [s["text"] for s in ((rationale or {}).get("what_would_change") or [])]
    return {"activities": (dr or {}).get("activities", []), "assumptions": [t["text"] for t in (dr or {}).get("validation_tasks", [])],
            "dependencies": (dr or {}).get("dependencies", []), "success_measure": (dr or {}).get("success_measure"),
            "what_would_change": change, "line_ids": [ln["id"] for ln in lines if m["method"] in ln["used_by"]]}


def source_price(pid: str, cid: str, methods: list[dict]) -> dict | None:
    """S5-13: the first product price with an unambiguous currency, from the drafts of the included methods."""
    for m in methods:
        if m["role"] not in INCLUDED:
            continue
        dr = db.get_record(f"drf_{pid}_{cid}_{m['method']}")
        for p in (dr or {}).get("products", []):
            pr = db.get_record(p["id"], p.get("version"))
            if (got := money.parse_price((pr or {}).get("price_text") or "")):
                return {"amount": got, "title": pr["title"], "product": {"id": pr["id"], "version": pr["version"]}}
    return None


def finish(pid: str, cid: str, p: dict, repin: bool = True) -> dict:
    """Every Plan write passes here: complete lines, launch-test sections, the FX pin and the return estimate's SOURCE
    default. Always unsigned; sign-off sets `signed_off` after. Sign-off passes `repin=False`: it signs the money the
    founder reviewed, so a publication or price stored since never changes it (REVIEW-SLICE-05 R2)."""
    names = [m["method"] for m in p["methods"]]
    p["cost_lines"] = complete(p.get("cost_lines") or [], names)
    p["methods"] = [{**m, "launch_test": launch_test(pid, cid, m, p["cost_lines"], p.get("rationale"))} for m in p["methods"]]
    p["signed_off"] = None
    if not repin:
        return p
    p["fx"] = fx_pin(pid, p["cost_lines"])
    est = dict(p.get("return_estimate") or {})
    if not est.get("retail_price") and (src := source_price(pid, cid, p["methods"])):
        est.update(retail_price=src["amount"], retail_price_source={"title": src["title"], "product": src["product"]})
        est.setdefault("by", {})["retail_price"] = "SOURCE"
    p["return_estimate"] = est or None
    p["signed_off"] = None
    return p


def write(pid: str, cid: str, base_version: int | None, change, task: str, detail: str, also=None) -> dict:
    """One money or role write: the base check, the change and the new version in one transaction; a stale base is 409
    and nothing is written. `also(tx_plan)` writes a record that must land with it (the scenario)."""
    with db.tx():
        cur = db.get_record(plan_id(pid, cid))
        if (cur["version"] if cur else None) != base_version:
            raise Refused(409, CHANGED.format(v=cur["version"] if cur else 0))
        p = change(_fields(cur) if cur else None)
        extra = also(p) if also else None
        rec = db.put_record("plan", pid, finish(pid, cid, p), inputs=_inputs(pid), rid=plan_id(pid, cid))
    db.event(pid, "founder", {"stage": "Plan", "task": task, "detail": detail})
    return {"version": rec["version"], **({"also": extra} if extra else {})}


def _plan_methods(p: dict) -> list[str]:
    return [m["method"] for m in p["methods"]]


def edit_line(pid: str, cid: str, base_version: int, lid: str, fields: dict) -> dict:
    """PATCH …/cost-lines/{id}: the founder's amount (YOU SAID), or null for "not known"; a flow; who uses it (sharing a
    line with another method of the plan). The line exists once, so every total that uses it changes once."""
    if "amount" in fields:
        a = fields["amount"]
        amt = money.money(a["amount"], a["ccy"]) if a else None
    if "flow" in fields and fields["flow"] not in money.FLOWS:
        raise Refused(422, "A cost is an expense, inventory or a refundable deposit.")
    pre = db.get_record(plan_id(pid, cid))
    est = None
    if fields.pop("use_estimate", None):  # the founder accepts Landfall's estimate: ESTIMATE, their choice, labeled
        ln0 = next((x for x in (pre or {}).get("cost_lines", []) if x["id"] == lid), None)
        est = ln0 and (ln0["amount"] is None) and (f := estimator(pre, cid)) and f(ln0.get("item_ref"))
        if not est:
            raise Refused(422, "There is no estimate for this cost.")
        fields.pop("amount", None)

    def change(p):
        if p is None:
            raise Refused(404, "No plan yet.")
        ln = next((x for x in p.get("cost_lines", []) if x["id"] == lid), None)
        if not ln:
            raise Refused(404, "No such cost line in this plan.")
        ln = dict(ln)
        if "amount" in fields or est:
            ln.pop("daily", None), ln.pop("days", None)  # a sized media line's daily budget goes with its amount
        if "amount" in fields:
            ln.update(amount=amt, basis="YOU_SAID" if amt else "NOT_KNOWN", by="founder", basis_note=None, source_claim_ids=[])
        if est:
            ln.update(amount=est["amount"], basis="ESTIMATE", by="founder", basis_note=est["basis_note"], source_claim_ids=[])
        if "flow" in fields:
            ln["flow"] = fields["flow"]
        if "used_by" in fields:
            ub = list(dict.fromkeys(fields["used_by"] or []))
            if not ub or set(ub) - set(_plan_methods(p)):
                raise Refused(422, "Share a cost only with methods in this plan.")
            ln["used_by"] = ub
        p["cost_lines"] = [ln if x["id"] == lid else x for x in p["cost_lines"]]
        return p
    detail = f"{lid}: " + (f"used the estimate {est['text']}" if est else "not known" if "amount" in fields and not fields["amount"] else
                           money.money_text(money.money(fields["amount"]["amount"], fields["amount"]["ccy"])) if fields.get("amount")
                           else ", ".join(sorted(fields)))
    if fields.get("amount") or est:
        ensure_fx(pid, [{"amount": est["amount"] if est else amt}] + ((pre or {}).get("cost_lines") or []))
    return write(pid, cid, base_version, change, "Changed a cost", detail)


RETURN_FIELDS = ("units", "retail_price", "wholesale_price", "sell_through")


def edit_return(pid: str, cid: str, base_version: int, fields: dict) -> dict:
    """PATCH …/return [T3]: units are individual sellable units; prices are Money per unit; sell-through is a {low,
    high} fraction. Each edit is YOU SAID and writes Plan v+1."""
    if set(fields) - set(RETURN_FIELDS):
        raise Refused(422, f"Give any of: {', '.join(RETURN_FIELDS)}.")
    val: dict = {}
    for k, v in fields.items():
        if v is None:
            val[k] = None
        elif k == "units":
            if not isinstance(v, int) or not 0 < v <= 10_000_000:
                raise Refused(422, "Give the units as a whole number of individual items.")
            val[k] = v
        elif k == "sell_through":
            try:
                lo, hi = float(v["low"]), float(v["high"])
            except (KeyError, TypeError, ValueError):
                raise Refused(422, "Give sell-through as low and high fractions, e.g. 0.3 and 0.6.")
            if not 0 <= lo <= hi <= 1:
                raise Refused(422, "Sell-through is between 0 and 1, low first.")
            val[k] = {"low": lo, "high": hi}
        else:
            val[k] = money.money(v["amount"], v["ccy"])

    def change(p):
        if p is None:
            raise Refused(404, "No plan yet.")
        est = dict(p.get("return_estimate") or {})
        est.update(val)
        est["by"] = {**est.get("by", {}), **{k: "YOU_SAID" for k in val}}
        if "retail_price" in val:
            est.pop("retail_price_source", None)
        p["return_estimate"] = est
        return p
    return write(pid, cid, base_version, change, "Changed the return estimate", ", ".join(sorted(fields)))


def ceiling_of(fi: dict | None) -> dict | None:
    return money.usd(fi["ceiling_usd"] * 100) if fi and fi.get("ceiling_usd") else None


def pinned_ceiling(plan: dict) -> dict | None:
    """A signed snapshot keeps the ceiling it was signed under (its pinned FounderInput)."""
    pin = next((x for x in plan.get("inputs", []) if "ceiling_usd" in x.get("keys", [])), None)
    return ceiling_of(pin and db.get_record(pin["id"], pin["version"]))


def roles_of(plan: dict | None) -> dict[str, str]:
    return {m["method"]: m["role"] for m in (plan or {}).get("methods", [])}


def line_view(ln: dict, roles: dict, fx: dict | None, est=None) -> dict:
    u = ln["amount"] and money.to_usd(ln["amount"], fx)
    out = {**ln, "text": money.display(ln["amount"]) if ln["amount"] else None,
           "usd": u or None, "usd_text": money.display(u) if u and ln["amount"]["ccy"] != "USD" else None,
           "unconverted": bool(ln["amount"]) and not u, "counts": money.counts(ln, roles), "shared": len(ln["used_by"]) > 1}
    if est and ln["amount"] is None and ln["basis"] == "NOT_KNOWN" and out["counts"]:
        out["estimate"] = est(ln.get("item_ref"))  # beside the unknown, never as its amount
    return out


def city_of(plan: dict) -> str | None:
    pre = f"plan_{plan.get('project_id')}_"
    return plan["id"][len(pre):] if plan.get("project_id") and str(plan.get("id", "")).startswith(pre) else None


def estimator(plan: dict, cid: str | None):
    """item_ref → estimate for this plan's city: the plan's FX (else the latest stored), the scenario's days for media."""
    from . import estimates, scenarios
    pid = plan.get("project_id")
    if not cid:
        return None
    fx = fx_of(plan) or (pid and latest_fx(pid))
    scn = scenarios.of_plan(plan) or (pid and scenarios.base_of(pid, cid)) or {}
    return lambda ref: estimates.estimate(ref, cid, fx, scn.get("horizon_days"))


def money_of(plan: dict | None, ceiling: dict | None, cid: str | None = None) -> dict | None:
    """PlanMoney (S5-6): lines, known totals by flow, unknown and unconverted counts, fit, FX pin, return estimate.
    `estimated_total` is known + the estimates of unknown lines (low/high); `cash_out_known` never includes them."""
    if not plan:
        return None
    from . import estimates
    roles, fx = roles_of(plan), fx_of(plan)
    lines = plan.get("cost_lines") or []
    t = money.totals(lines, roles, fx, ceiling)
    est = plan.get("return_estimate")
    cid = cid or city_of(plan)
    est_of = estimator(plan, cid)
    views = [line_view(ln, roles, fx, est_of) for ln in lines]
    open_ = [v for v in views if v["id"] in t["unknown"] or v["id"] in t["unconverted"]]
    est_fx = fx or (plan.get("project_id") and latest_fx(plan["project_id"]))
    et = estimates.total(t["cash_out_known"], open_, est_fx) if any(v.get("estimate") for v in open_) else None
    by_method = {}  # each channel's planned amount, known + its lines' estimates (owner, 28 Oct 2026: money next to the channel)
    for m in [m for m, r in roles.items() if r in INCLUDED]:
        mine = {ln["id"] for ln in lines if m in ln["used_by"]}  # ponytail: a shared line counts in full on each channel
        tm = money.totals([{**ln, "used_by": [m]} for ln in lines if ln["id"] in mine], {m: "MAIN"}, fx, None)
        open_m = [v for v in views if v["id"] in mine and (v["id"] in tm["unknown"] or v["id"] in tm["unconverted"])]
        em = estimates.total(tm["cash_out_known"], open_m, est_fx) if any(v.get("estimate") for v in open_m) else None
        if mine:
            by_method[m] = {"amount": (em or {}).get("amount") or tm["cash_out_known"], "estimated": bool(em),
                            "not_known": (em or {}).get("not_estimated", 0) if em else len(tm["unknown"]) + len(tm["unconverted"])}
    planned, gross = planned_of(t, et, ceiling), money.gross_receipts(est, roles, fx)
    assumed = None if gross else assumed_return(plan, planned["amount"], lines, roles, est_fx)
    works = (money.if_it_works(planned["amount"], gross["usd"]["low"], gross["usd"]["high"], "your return estimate") if gross
             else assumed and money.if_it_works(planned["amount"], assumed["amount"], assumed["amount"], "the assumed return"))
    return {**t, "lines": views, "estimated_total": et, "planned": planned, "by_method": by_method,
            "fx": fx and {"id": fx["id"], "version": fx["version"], "rate_date": fx["rate_date"], "source": fx["source"],
                          "text": f"converted at ECB rates of {fx['rate_date']}"},
            "needs_fx": bool(t["unconverted"]),
            "return_estimate": est, "gross_receipts": gross, "assumed_return": assumed, "if_it_works": works,
            "scenario": plan.get("scenario"), "proposal": plan.get("proposal"), "alternative": plan.get("alternative"),
            "rationale": plan.get("rationale"), "rationale_evidence": adopted_evidence(plan)}


def assumed_return(plan: dict, planned: dict, lines: list[dict], roles: dict, fx: dict | None) -> dict | None:
    """The founder's own units and price (theirs or the plan's SOURCE price) without a sell-through: their return at a
    labeled 50% sold. Nothing else: no share of the planned cost, no shop-stock units, no store median (owner,
    28 Oct 2026: "why would I put 10k in for 2,500 out? maybe we remove that"); the strip hides Cash back then."""
    est = plan.get("return_estimate") or {}
    if not est.get("units"):
        return None
    wholesale = not any(m in money.DIRECT for m, r in roles.items() if r in INCLUDED)
    key = next((k for k in ("wholesale_price" if wholesale else "retail_price", "retail_price") if est.get(k)), None)
    price = key and money.to_usd(est[key], fx)
    if not price:
        return None
    why = "you said" if (est.get("by") or {}).get(key) == "YOU_SAID" else \
        f"from {(est.get('retail_price_source') or {}).get('title', 'your store')}"
    st = est.get("sell_through")
    return money.assumed_return(planned, est["units"], price, why, "you said", st and (st["low"] + st["high"]) / 2)


def planned_of(t: dict, et: dict | None, ceiling: dict | None) -> dict:
    """The plan's total against the founder's budget (owner, 28 Oct 2026): every counted amount plus the estimates
    beside unknown lines; `known` is what the founder said or a source lists (YOU SAID, LISTED RATE), the rest is
    labeled ESTIMATE or ASSUMED. "US$9,800 planned of US$10,000 · US$1,200 known, the rest labeled estimates"."""
    amount = et["amount"] if et else t["cash_out_known"]
    known = money.usd(t["cash_out_known"]["minor"] - t["assumed_known"]["minor"])
    open_ = et["not_estimated"] if et else len(t["unknown"]) + len(t["unconverted"])
    whole = lambda m: money.display(money.usd(round(m["minor"] / 100) * 100))  # noqa: E731
    rest = amount["minor"] - known["minor"]
    text = ((f"{whole(amount)} planned" + (f" of {whole(ceiling)}" if ceiling else "") + " · "
             + (f"{whole(known)} known, the rest labeled estimates" if known["minor"] and rest > 0 else
                "all labeled estimates" if rest > 0 else "all known")) if amount["minor"] else "Nothing costed yet") \
        + (f" · {open_} not known" if open_ else "")
    return {"amount": amount, "known": known, "estimated": money.usd(rest), "not_known": open_, "ceiling": ceiling,
            "over": bool(ceiling) and amount["minor"] > ceiling["minor"], "text": text}


def adopted_evidence(plan: dict) -> list[dict] | None:
    """E-numbers are per proposal version, so an adopted rationale's citations resolve only against the version it came
    from, never the latest proposal (REVIEW-SLICE-05 R4)."""
    pin = plan.get("proposal")
    prop = pin and db.get_record(pin["id"], pin["version"])
    return prop and [{k: e[k] for k in ("id", "role", "text")} for e in prop.get("evidence", [])]


def method_money(plan: dict | None, method: str) -> dict | None:
    """S5-7 / S5-18: "N cost lines · M shared · K not known · subtotal" for one method's own lines."""
    if not plan:
        return None
    roles, fx = roles_of(plan), fx_of(plan)
    mine = [ln for ln in plan.get("cost_lines") or [] if method in ln["used_by"]]
    if not mine:
        return None
    t = money.totals([{**ln, "used_by": [method]} for ln in mine], {method: "MAIN"}, fx, None)
    return {"lines": len(mine), "shared": sum(len(ln["used_by"]) > 1 for ln in mine),
            "not_known": len(t["unknown"]), "unconverted": len(t["unconverted"]), "subtotal": t["cash_out_known"],
            "text": t["text"], "line_ids": [ln["id"] for ln in mine], "counts": roles.get(method) in INCLUDED}


def creator_fee(pid: str, cid: str) -> dict | None:
    """S5-12: the signed plan's creator-fee line as a pin; None while there is none or it is NOT KNOWN."""
    s = latest_signed(pid, cid)
    ln = s and next((x for x in s.get("cost_lines") or [] if x.get("item_ref") == "creator:CREATOR_FEE"), None)
    if not ln or not ln["amount"]:
        return None
    return {"plan": {"id": s["id"], "version": s["version"]}, "line_id": ln["id"], "amount": ln["amount"], "basis": ln["basis"],
            "text": f"From your signed plan v{s['version']}: {money.display(ln['amount'])} "
                    f"({ {'YOU_SAID': 'you said', 'LISTED_RATE': 'listed rate'}.get(ln['basis'], 'assumed')})"}


def _fields(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}


def _inputs(pid: str) -> list[dict]:
    sl, fi = db.latest("shortlist", pid), db.latest("founder_input", pid)
    return [{"id": sl["id"], "version": sl["version"], "keys": ["beachhead"]},
            {"id": fi["id"], "version": fi["version"], "keys": ["ceiling_usd"]}]


def save(pid: str, cid: str, base_version: int | None, methods: list[dict]) -> dict:
    """PUT /plans/{city}: the founder's roles (YOU SAID, "Selected by you"). Always an unsigned version; a method kept
    from the last version keeps its pins for reference, but only a signed version's pins are ever used."""
    if cid not in markets.CITIES:
        raise Refused(404, "No such city.")
    if cid != beachhead(pid):
        raise Refused(409, "Choose this city as your beachhead in Markets first.")
    names = [m.get("method") for m in methods]
    if set(names) - set(research.METHODS) or len(set(names)) != len(names) or \
            any(m.get("role") not in ROLES for m in methods):
        raise Refused(422, "Give each method once, with a role: MAIN, SUPPORTS, OPTIONAL or NOT_NOW.")
    if sum(m["role"] == "MAIN" for m in methods) > 1:
        raise Refused(422, "Only one method can be the main test.")

    def change(cur):
        old = {m["method"]: m for m in (cur or {}).get("methods", [])}
        out = [{"method": m["method"], "role": m["role"],
                # a role the founder leaves as it was keeps who chose it (S5-4); any change is the founder's
                "role_by": (old.get(m["method"]) or {}).get("role_by", "founder") if (old.get(m["method"]) or {}).get("role") == m["role"] else "founder",
                "dossier": (old.get(m["method"]) or {}).get("dossier"), "draft": (old.get(m["method"]) or {}).get("draft"),
                "note": (m.get("note") or "").strip() or None} for m in methods]
        keep = {k: v for k, v in (cur or {}).items() if k not in ("methods", "signed_off", "source")}
        source = "chosen" if any(m["role_by"] == "strategist" for m in out) else "founder_selection"
        return {"city_id": cid, "status": "active", **keep, "source": source, "methods": out}
    detail = " · ".join(f"{research.METHOD_LABELS[m['method']]}: {m['role'].replace('_', ' ').lower()}" for m in methods)
    return {"version": write(pid, cid, base_version, change, "Changed the plan", detail)["version"]}


def prepare(pid: str, cid: str) -> dict:
    """Owner, 28 Oct 2026: the plan for a recommended city, started before the founder chooses one (autoplan.prewarm).
    An empty plan marked `prepared_in_advance`; no beachhead guard and no founder event, because nobody chose it. It
    is just `plan_{project}_{city}`, so it is the current plan the moment the founder picks the city; sign-off and the
    fence still require the beachhead. An existing plan is kept as it is."""
    if cid not in markets.CITIES:
        raise Refused(404, "No such city.")
    with db.tx():
        cur = db.get_record(plan_id(pid, cid))
        if cur:
            return {"version": cur["version"]}
        p = finish(pid, cid, {"city_id": cid, "status": "active", "source": "founder_selection", "methods": [],
                              "prepared_in_advance": True})
        return {"version": db.put_record("plan", pid, p, inputs=_inputs(pid), rid=plan_id(pid, cid))["version"]}


def _current_dossier(pid: str, cid: str, method: str, seen: dict) -> dict:
    """The method's dossier and draft as they stand, checked against what the founder saw: a moved version is 409, a
    missing or unusable one 422 [P65-R4]."""
    row = specialist.spec_row(pid, cid, method)
    d = db.get_record(f"dos_{pid}_{cid}_{method}")
    label = research.METHOD_LABELS[method]
    if row and row["status"] in ("queued", "running"):
        raise Refused(422, f"{label} is still being investigated.")
    if not d or d["status"] not in ("complete", "partial"):
        raise Refused(422, f"Investigate {label} first.")
    if seen.get("dossier_version") != d["version"]:
        raise Refused(409, "The research or draft changed while you were signing; review it and sign again.")
    dr = db.get_record(f"drf_{d['id'][4:]}")
    if not dr or dr["source"]["dossier_version"] != d["version"]:
        raise Refused(422, f"Develop {label} first.")
    if seen.get("draft_version") != dr["version"]:
        raise Refused(409, "The research or draft changed while you were signing; review it and sign again.")
    if specialist.stale_of(pid, d):
        raise Refused(422, f"{label}'s investigation is stale: investigate it again.")
    return {"dossier": {"id": d["id"], "version": d["version"]}, "draft": {"id": dr["id"], "version": dr["version"]}}


def sign_off(pid: str, cid: str, base_version: int, expect: dict) -> dict:
    """POST /plans/{city}/sign-off [P65-R4, P5-R2]: pins exactly the versions the founder saw, including this plan
    version and so its money (`base_version` is `expect.plan_version`). A moved pin → 409; a missing Main test,
    draft or a stale investigation → 422. Authorizes nothing external."""
    if "plan_version" in expect and expect["plan_version"] != base_version:
        raise Refused(409, CHANGED.format(v=base_version))
    expect = {k: v for k, v in expect.items() if k != "plan_version"}
    with db.tx():
        cur = db.get_record(plan_id(pid, cid))
        if not cur or cur["version"] != base_version:
            raise Refused(409, CHANGED.format(v=cur["version"] if cur else 0))
        if cid != beachhead(pid):
            raise Refused(409, "Choose this city as your beachhead in Markets first.")
        inc = [m for m in cur["methods"] if m["role"] in INCLUDED]
        if sum(m["role"] == "MAIN" for m in inc) != 1:
            raise Refused(422, "Choose one main test first.")
        out = []
        for m in cur["methods"]:
            if m["role"] not in INCLUDED:
                out.append({**m, "dossier": None, "draft": None})
                continue
            if m["method"] not in expect:
                raise Refused(422, f"Sign off with the {research.METHOD_LABELS[m['method']]} versions you reviewed.")
            out.append({**m, **_current_dossier(pid, cid, m["method"], expect[m["method"]])})
        p = finish(pid, cid, {**_fields(cur), "methods": out}, repin=False)
        p["signed_off"] = {"at": db.now(), "by": "founder"}
        rec = db.put_record("plan", pid, p, inputs=_inputs(pid) + [{**x[k], "keys": []} for x in out if x["draft"] for k in ("dossier", "draft")],
                            rid=plan_id(pid, cid))
    db.event(pid, "founder", {"stage": "Plan", "task": "Signed off the plan", "detail": f"plan v{rec['version']} (nothing is "
                              "sent, created or spent by signing)"})
    return {"version": rec["version"], "pins": {m["method"]: {"dossier": m["dossier"], "draft": m["draft"]} for m in out if m["draft"]}}


def plan_stale(pid: str, plan: dict | None) -> list[str]:
    """The union of the pinned drafts' reasons, plus newer work the signed plan doesn't use yet."""
    out = []
    for m in (plan or {}).get("methods", []):
        if not m.get("draft"):
            continue
        label = research.METHOD_LABELS[m["method"]]
        d = db.get_record(m["dossier"]["id"], m["dossier"]["version"])
        out += [f"{label}: {r}" for r in specialist.stale_of(pid, d)]
        if db.get_record(m["draft"]["id"])["version"] != m["draft"]["version"] or \
                db.get_record(m["dossier"]["id"])["version"] != m["dossier"]["version"]:
            out.append(f"A newer draft of {label} exists; sign again to use it")
    return out


def view(pid: str, cid: str) -> dict:
    """GET /plans/{city} (B10 Plan view)."""
    from . import autoplan  # it builds plans, so it imports this module
    fi = db.latest("founder_input", pid)
    ov = next((c for c in research.opportunities_view(pid)["cities"] if c["city_id"] == cid), None)
    cur, signed = db.get_record(plan_id(pid, cid)), latest_signed(pid, cid)
    roles = {m["method"]: m for m in (cur or {}).get("methods", [])}
    methods = []
    for method in research.METHODS:
        row = specialist.spec_row(pid, cid, method)
        d = db.get_record(f"dos_{pid}_{cid}_{method}")
        dr = d and db.get_record(f"drf_{d['id'][4:]}")
        r = roles.get(method)
        methods.append({
            "method": method, "label": research.METHOD_LABELS[method],
            "matrix": (ov or {}).get("matrix", {}).get(method),
            "investigation": {"status": row["status"] if row else "not_investigated",
                              "dossier": d and {"id": d["id"], "version": d["version"]},
                              "readiness": d and d["readiness"]["overall"], "stale": d and specialist.stale_of(pid, d),
                              # what the investigation found, so the card stops repeating the city research's first read
                              "found": d and {"kept": [c.get("name") for c in d.get("candidates") or [] if c.get("decision") == "KEPT"],
                                              "name": specialist.concept_of(d).get("name"),
                                              "concept": specialist.concept_of(d).get("line")}},
            "role": r and {"role": r["role"], "by": r["role_by"], "label": ROLE_LABEL[r["role_by"]], "note": r.get("note"),
                           "dossier": r.get("dossier"), "draft": r.get("draft")},
            "money": method_money(cur, method), "launch_test": r and r.get("launch_test"),
            "draft": dr and {"id": dr["id"], "version": dr["version"], "success_measure": dr["success_measure"],
                             "label": dr["label"], "built_on": dr["source"]["dossier_version"],
                             # v4 08/B03/B04: the card's plan line, the preview's objective and activities
                             "concept": specialist.concept_of({**d, "concept": dr["concept"]}),
                             "activities": dr.get("activities", [])},
            "cost_items": [label for _, label, _ in items_of(method)],  # what Add would cost, before any line exists (B04)
            "why_it_fits": (d or {}).get("why_it_fits", []), "costing_inputs": (dr or {}).get("costing_inputs", []),
            "dependencies": (dr or {}).get("dependencies", []), "validation_tasks": (dr or {}).get("validation_tasks", [])})
    return {"city": markets.CITIES[cid] | {"city_id": cid}, "beachhead": cid == beachhead(pid),
            "research": {"status": (ov or {}).get("status", "not_started"), "can": (ov or {}).get("can", [])},
            "auto": autoplan.status(pid, cid),
            "methods": methods,
            "plan": cur and {"id": cur["id"], "version": cur["version"], "signed_off": cur["signed_off"],
                             "signed_version": signed and signed["version"], "changed_since_sign_off": bool(signed) and signed["version"] != cur["version"],
                             "stale": plan_stale(pid, signed)},
            "ceiling": {"usd": fi.get("ceiling_usd"), "by": fi.get("ceiling_by") or ("YOU_SAID" if fi.get("ceiling_usd") else None)},
            "money": money_of(cur, ceiling_of(fi)),
            "signed": signed and {"version": signed["version"], "money": money_of(signed, pinned_ceiling(signed))},
            **extras(pid, cid, cur, ceiling_of(fi))}


def extras(pid: str, cid: str, cur: dict | None, ceiling: dict | None) -> dict:
    """The proposal (with each option previewed against the current plan) and the digital scenario."""
    from . import scenarios, strategist
    return {"proposal": strategist.view(pid, cid, cur, ceiling), "scenario": scenarios.view(pid, cid, cur)}


# ---- the Act checklist (A1): derived from records, never stored ----
def _dst_status(pid: str, pkg: dict, dst: str, missing: list) -> str:
    acts = delivery.latest_actions(pid, pkg["id"], dst)
    last = acts[-1] if acts else None
    if last and last["status"] == "FAILED_VERIFICATION" and last["mode"] == "LIVE":
        return "FAILED_VERIFICATION"  # created, but not confirmed paused: never counted as paused [P65-I6]
    live = [m for m in delivery.manuals_of(pid, pkg["id"], dst) if not m.get("withdrawn_at")]
    if last and delivery.test_sent(last):  # 6.7 G3: a test never counts as contact; your own outreach outranks it
        return live[-1]["manual_kind"] if live else "TEST_SENT"
    if last and last["status"] == "SUCCEEDED" and last["mode"] == "LIVE":
        return "SENT" if dst == "EMAIL" else "CREATED_PAUSED"
    if last and last["status"] in ("UNCERTAIN", "EXECUTING"):
        return "UNCERTAIN"
    if live:
        return live[-1]["manual_kind"]
    if last and last["status"] in ("FAILED", "REFUSED"):
        return "FAILED"
    if any(e["package_id"] == pkg["id"] and e["package_version"] == pkg["version"] for e in db.records("export", pid)):
        return "EXPORTED"
    apvs = delivery.approvals(pid, pkg["id"], dst)
    if apvs and apvs[-1]["package_version"] == pkg["version"]:
        return "APPROVED"
    return "NEEDS_INFORMATION" if missing else "PREPARED"


def act(pid: str, cid: str) -> dict:
    """GET /plans/{city}/act (B10): the checklist for the latest signed-off plan."""
    signed, cur = latest_signed(pid, cid), db.get_record(plan_id(pid, cid))
    pkgs = delivery.packages_of(pid, cid)
    if not signed:
        return {"plan": None, "reason": "Sign off a plan first.", "plan_changed": False, "items": [],
                "earlier_plan": [{"package": {"id": p["id"], "version": p["version"]}, "reason": EARLIER} for p in pkgs]}
    items, earlier = [], []
    included = [m for m in signed["methods"] if m["role"] in INCLUDED and role_of(cur, m["method"]) in INCLUDED]  # a removal applies at once
    active = [p for p in pkgs if p.get("status", "active") == "active" and not fence(pid, cid, p["method"])]
    for p in pkgs:
        if p not in active:
            earlier.append({"package": {"id": p["id"], "version": p["version"]}, "method": p["method"],
                            "plan_version": (p.get("plan_ref") or {}).get("version"),
                            "reason": "Set aside" if p.get("status") == "set_aside" else fence(pid, cid, p["method"]) or EARLIER})
    if any((m.get("draft") and db.get_record(m["draft"]["id"], m["draft"]["version"])["products"]) for m in included):
        unconfirmed = sum(1 for p in active for a in p["assets"] if a["artifact"]["sha256"] not in p.get("rights_confirmed", []))
        items.append({"key": "brand_assets", "kind": "BRAND_ASSETS", "status": "NEEDS_INFORMATION" if unconfirmed else "PREPARED",
                      "missing": [f"Confirm you may use {unconfirmed} image{'s' * (unconfirmed > 1)}"] if unconfirmed else [],
                      "depends_on": [], "stale": []})
    for m in included:
        method = m["method"]
        mine = [p for p in active if p["method"] == method]
        outreach = method in delivery.OUTREACH
        if outreach:
            items.append({"key": f"choose_{method}", "kind": "CHOOSE_RECIPIENTS", "method": method,
                          "status": "PREPARED" if mine else "NEEDS_INFORMATION",
                          "missing": [] if mine else ["Choose who to approach"], "depends_on": [], "stale": []})
        elif not mine:
            items.append({"key": f"settings_{method}", "kind": "DIGITAL_SETTINGS", "method": method, "status": "NEEDS_INFORMATION",
                          "missing": ["Prepare the campaign"], "depends_on": [], "stale": []})
        for p in mine:
            missing = delivery.unresolved(p)
            stale = delivery.stale_of(pid, p)
            flat = [x["text"] for dst in p["destinations"] for x in missing[dst]] + [x for x in [delivery.contact_missing(p)] if x]
            key = f"pkg_{p['id']}"
            items.append({"key": key, "kind": "OUTREACH" if outreach else "CAMPAIGN_REVIEW", "method": method,
                          "package": {"id": p["id"], "version": p["version"]}, "partner": p.get("partner") and p["partner"]["name"],
                          "lead_id": (p.get("partner") or {}).get("lead_id") if "~" in p["id"] else None,
                          "status": "NEEDS_INFORMATION" if flat else "PREPARED", "missing": list(dict.fromkeys(flat)),
                          "depends_on": [f"choose_{method}"] * outreach + ["brand_assets"] * bool(p["assets"]), "stale": stale})
            for dst in p["destinations"]:
                items.append({"key": f"{key}_{dst.lower()}", "kind": "DELIVER", "method": method,
                              "partner": p.get("partner") and p["partner"]["name"],
                              "package": {"id": p["id"], "version": p["version"]}, "destination": dst,
                              "status": _dst_status(pid, p, dst, missing[dst]), "missing": [x["text"] for x in missing[dst]],
                              "depends_on": [key], "stale": stale})
    counts: dict = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {"plan": {"id": signed["id"], "version": signed["version"], "signed_off": signed["signed_off"]},
            "plan_changed": cur["version"] != signed["version"], "items": items, "earlier_plan": earlier, "counts": counts}


def confirm_rights(pid: str, cid: str) -> dict:
    """A2: confirm once that you may use the images, for every active package of the signed plan that holds any you
    haven't confirmed; each such package writes v+1 (and so outdates its approval, as any edit does)."""
    done = []
    for p in delivery.packages_of(pid, cid):
        if p.get("status", "active") != "active" or fence(pid, cid, p["method"]):
            continue
        if {a["artifact"]["sha256"] for a in p["assets"]} - set(p.get("rights_confirmed", [])):
            delivery.edit_package(pid, p["id"], p["version"], {"rights_confirmed": True})
            done.append(p["id"])
    return {"packages": done}


def stage(pid: str) -> dict:
    """For the stage bar (SH1): the beachhead city's plan, and the checklist counts once it is signed."""
    cid = beachhead(pid)
    if not cid:
        return {"city_id": None, "version": None, "signed_off": None, "counts": None}
    cur, signed = db.get_record(plan_id(pid, cid)), latest_signed(pid, cid)
    return {"city_id": cid, "version": cur and cur["version"], "signed_off": bool(signed),
            "signed_version": signed and signed["version"], "changed_since_sign_off": bool(signed) and signed["version"] != cur["version"],
            "methods": cur and sum(m["role"] in INCLUDED for m in cur["methods"]),
            "counts": _deliver_counts(act(pid, cid)) if signed else None}


def _deliver_counts(a: dict) -> dict:
    """Launch: one count per thing that goes out (a package's destination), by status."""
    out: dict = {}
    for it in a["items"]:
        if it["kind"] == "DELIVER":
            out[it["status"]] = out.get(it["status"], 0) + 1
    return out
