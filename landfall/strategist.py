"""Slice 5 · B: the launch strategist (BUILD-PLAN "Slice 5" §5.6, §5.9, §5.10 [R3, R4]; CONTRACTS B7 P5-R3/R4).

`POST /plans/{city}/propose` (never automatic) runs one structured call (`launch_strategist`, Nova 2 Lite) and one
per-citation check (`strategist_check`). The answer is a PlanProposal with one or two options; nothing in it changes
the plan until the founder adopts an option.

- Deterministic first: cited ids must be this plan's evidence; each option has exactly one MAIN; an unlabeled amount
  is refused; every COSTING item of every included method gets a line (NOT_KNOWN from code), so no option is free by
  omission; an option B with option A's roles is dropped; a failed field is dropped, never rewritten.
- Then the check: a FACT stays a FACT only when its own citations support it, otherwise it is shown as Landfall's
  assumption without the citation. A LISTED_RATE needs the cited text to hold that amount and currency (`money.prices`)
  and the judge's verdict that it prices this item in this unit; otherwise ASSUMED (with its basis note) or NOT_KNOWN.
  Anything unjudged counts as unsupported.
- Preview and adoption use one computation (`apply`), and founder amounts win per item.
"""
import json
import re
from decimal import Decimal, InvalidOperation

from pydantic import Field
from strands import Agent

from . import config, db, delivery, estimates, markets, money, plans, ranking, research, runner, specialist
from .cassette import NoRecording
from .ledger import BudgetExhausted
from .nova import make_model
from .research import Refused
from .runner import HANDLERS, Run

STRATEGIST_VERSION = "launch_strategist@7"  # @7: Google Search ads in every option (owner, 28 Oct 2026); @2: ids spelled out, no free label (LIVE L5 @1); @3: a flat rationale list, a worked line; @4: rationale first and required (LIVE L5 @2–@3: rationale null); @5: budget headroom (owner, 27 Oct 2026); @6: the ESTIMATE table and a split of the ceiling (owner, 28 Oct 2026: "if the budget is 10k, then use the 10k")
CHECK_VERSION = "strategist_check@1"
SCHEMA_VERSION = "plan_proposal@5"  # @5: OptionIn.budget_split; a stored option's `split`, its lines sized by code
# Two strategist attempts (the second is the one schema repair) and one check: the case limit of 3 model calls (§5.12).
CAPS = {"model": 8, "search": 0, "page": 0, "presence": 0, "qloo": 0, "urn": 0, "check": 0, "prose": 1, "locate": 0, "profile": 0}
USD_CAP, EXPECTED = 0.40, 0.03  # 8 calls: structured-output retries and the repair (owner, 28 Oct 2026: a 3-call limit stopped a plan)
FIELDS = {"brand": "About your brand", "city": "About {city}", "alternative": "The alternative", "known": "What we know",
          "assumed": "What we're assuming", "what_would_change": "What would change this plan",
          "what_we_learn": "What you'll learn", "success": "What success looks like"}
MODEL_BASES = ("LISTED_RATE", "ESTIMATE", "ASSUMED", "NOT_KNOWN", "NOT_COSTED")

SYSTEM = """You are a launch strategist. You shape one founder's first launch test in one city from research already
done: which ways in to use, in which roles, what each would cost, and why. You do no research, and you never compare
this city with another.

Roles: exactly one MAIN (the main test); SUPPORTS methods help it; OPTIONAL and NOT_NOW are left out of the cost.
Google Search ads (method digital; Google only, never TikTok, Meta or Instagram) are part of every option once
investigated: SUPPORTS unless you make them the MAIN test, with their cost items and budget lever.
Option A is the mix you propose. Give an option B only when a genuinely different mix is a real trade-off (cheaper,
faster, less dependent on a partner); otherwise give only A.

Costs: give one cost line for every listed cost item of every MAIN or SUPPORTS method in that option. An amount is
always labeled:
- ESTIMATE: Landfall's ESTIMATE table (listed with each item) is the anchor. Landfall prices every item it lists from
  that table, so for those items give basis ESTIMATE and the table's amount.
- ASSUMED: only for an item the table doesn't price: your own planning figure, in the city's currency, with a
  basis_note stating quantity × unit × period ("2 staff × 6 hours × 2 days at 30 an hour").
- LISTED_RATE: only when a cited evidence item itself states that price for that item and unit. Product prices are
  the brand's own retail prices, never a fee.
- NOT_KNOWN: when you can't size it honestly. Never guess silently.
Keep the founder's own amounts (YOU SAID) as they are: don't propose a line for those items.

Budget: the founder's ceiling is the budget for this test. Plan to use it, never more. What the costs above leave
under the ceiling goes to the budget levers listed (the daily ad budget, more creator posts, more product for sampling,
more pop-up days): give budget_split, a percent for each lever of the option's MAIN or SUPPORTS methods that you
use (they add up to 100), each with why. Landfall does the arithmetic from your split. Weight the levers the research
supports best. Never add a method whose investigation found nothing; instead say in what_would_change what research
would unlock it. Affinity or audience results (QLOO) are cultural relationships: never money, demand, size or a forecast.

Write the rationale first, then the options. A cost line looks like: {"item": "popup:STAFF", "amount": "360", "currency": "AUD", "basis": "ASSUMED", "basis_note":
"2 staff × 6 hours × 1 day at 30 an hour", "used_by": ["popup"], "flow": "expense"}.

Rationale: a list of short statements, each tagged with its field (brand, city, alternative, known, assumed,
what_would_change, what_we_learn, success), at least one per field. Each statement is FACT (it states something about the world
and cites the evidence ids that state it) or ASSUMPTION (your reasoning, a proposal, or anything the evidence doesn't
state). When in doubt, it is an ASSUMPTION. A reviewer checks every FACT against its citations."""


class StatementIn(research.Lenient):
    field: str = Field("", description="Exactly one of: brand, city, alternative, known, assumed, what_would_change, "
                                       "what_we_learn, success")
    text: str = ""
    kind: str = Field("ASSUMPTION", description="FACT (the cited evidence states it) or ASSUMPTION (your reasoning)")
    cites: list[str] = Field(default_factory=list, description="For a FACT: the evidence ids that state it, e.g. [\"E3\"]")


class LineIn(research.Lenient):
    item: str = Field("", description="One cost item id from the list, exactly as written, e.g. popup:HIRE_FEE")
    amount: str | None = Field(None, description="A plain number in the currency, e.g. \"450\"; null when not known")
    currency: str | None = Field(None, description="ISO code, e.g. AUD")
    basis: str = Field("NOT_KNOWN", description="Exactly one of: ESTIMATE (the table's amount), ASSUMED, LISTED_RATE, NOT_KNOWN")
    basis_note: str = Field("", description="For ASSUMED or ESTIMATE: quantity × unit × period with numbers, e.g. "
                                            "\"2 staff × 6 hours × 2 days at 30 an hour\"")
    used_by: list[str] = Field(default_factory=list, description="Method ids this cost serves; two when shared, e.g. [\"popup\", \"community\"]")
    flow: str | None = Field(None, description="expense, inventory or refundable_deposit")
    cites: list[str] = Field(default_factory=list, description="For LISTED_RATE only: the evidence id stating that price")


class MethodIn(research.Lenient):
    method: str = Field("", description="Exactly one of: retail, creator, popup, community, digital")
    role: str = Field("", description="Exactly one of: MAIN, SUPPORTS, OPTIONAL, NOT_NOW")
    reason: str = ""


class SplitIn(research.Lenient):
    lever: str = Field("", description="One budget lever id from the list, exactly as written, e.g. digital:MEDIA_SPEND")
    percent: int = Field(0, description="Its share of the budget left under the ceiling, a whole percent")
    why: str = ""


class OptionIn(research.Lenient):
    methods: list[MethodIn] = Field(default_factory=list, description="Exactly one MAIN")
    cost_lines: list[LineIn] = Field(default_factory=list, description="One line per cost item of every MAIN or SUPPORTS method")
    budget_split: list[SplitIn] = Field(default_factory=list, description="How the budget left under the ceiling is split "
                                        "across this option's levers; percents add up to 100")
    why: str = ""
    trade_off: str = ""


class ProposalIn(research.Lenient):
    # rationale first and required: Nova 2 Lite wrote the options and left a trailing rationale null (LIVE L5 @1–@3)
    rationale: list[StatementIn] = Field(min_length=1, description="8 to 16 short statements, at least one for each "
                                         "field: brand, city, alternative, known, assumed, what_would_change, what_we_learn, success")
    options: list[OptionIn] = Field(default_factory=list, description="Option A, then option B only if it is a real alternative")
    preference: list[str] = Field(default_factory=list, description="Option ids in your order of preference, e.g. [\"A\", \"B\"]")


def proposal_id(pid: str, cid: str) -> str:
    return f"prp_{pid}_{cid}"


# ---- inputs ----
def evidence(pid: str, cid: str) -> list[dict]:
    """The checked evidence of every investigated method (delivery.evidence_of), one E-number per distinct item."""
    seen, out = set(), []
    for method in research.METHODS:
        d = db.get_record(f"dos_{pid}_{cid}_{method}")
        if not d or d["status"] not in ("complete", "partial"):
            continue
        dr = db.get_record(f"drf_{d['id'][4:]}") or {}
        for e in delivery.evidence_of(d, dr):
            k = (e["role"], e["text"])
            if k not in seen:
                seen.add(k)
                out.append({k2: e[k2] for k2 in ("role", "text", "claim_id")} | {"method": method})
    return [{**e, "id": f"E{n}"} for n, e in enumerate(out, 1)]


def prompt(pid: str, cid: str, plan: dict, ev: list[dict], ceiling: dict | None) -> str:
    city, fi = markets.CITIES[cid], db.latest("founder_input", pid) or {}
    ccy = markets.CURRENCY.get(city["country"]) or "USD"
    roles = plans.roles_of(plan)
    est = plans.estimator({**plan, "project_id": pid, "fx": []}, cid)  # the latest stored rate, the scenario's days

    def priced(ref: str) -> str:
        e = est(ref)
        return f"ESTIMATE {e['text']}: {e['basis_note'].removeprefix('Estimate: ')}" if e else "no ESTIMATE"
    lines = []
    for method in research.METHODS:
        d = db.get_record(f"dos_{pid}_{cid}_{method}")
        dr = d and db.get_record(f"drf_{d['id'][4:]}")
        state = (f"investigated ({d['readiness']['overall'].replace('_', ' ').lower()})" if d and d["status"] in ("complete", "partial")
                 else "not investigated")
        lines.append(f"## method id: {method} ({research.METHOD_LABELS[method]}) · {state} · current role: {roles.get(method, 'not in the plan')}")
        if dr:
            lines.append(f"Draft: {dr['concept']['name']}: {dr['concept']['line']}. Success measure: {dr['success_measure']}")
            lines += [f"- activity: {a}" for a in dr["activities"][:6]]
        lines.append("Cost item ids:\n" + "\n".join(f"- {ref} ({label}, {flow}) · {priced(ref)}" for ref, label, flow in plans.items_of(method)))
        lines.append("Budget levers: " + "; ".join(f"{ref} ({label})" for ref, (_, label) in estimates.LEVERS.items()
                                                   if ref.startswith(method + ":")))
    yours = [ln for ln in plan.get("cost_lines") or [] if ln["by"] == "founder" and ln["amount"]]
    aud = [a["name"] for a in (ranking.audience_view(pid) or {}).get("audiences", [])]
    return (f"City: {city['name']}, {markets.COUNTRY_NAME.get(city['country'], city['country'])}. Currency: {ccy}.\n"
            f"Founder's goal: {fi.get('objective') or 'not stated'} (purpose: {fi.get('purpose') or 'not stated'}).\n"
            f"Launch-test budget (the ceiling): {money.display(ceiling) or 'none set'}{_local(ceiling, ccy, plans.latest_fx(pid))} · plan to use it, never more.\n"
            f"Founder's own amounts (YOU SAID; they are kept whatever you propose, so give NOT_KNOWN for these items): "
            + ("; ".join(f"{ln['item_ref'] or ln['label']} = {money.money_text(ln['amount'])}" for ln in yours) or "none") + "\n"
            f"Audiences (QLOO, cultural relationships only): {', '.join(aud) or 'none'}\n\n"
            + "\n".join(lines) + "\n\nEvidence:\n" + "\n".join(f"- {e['id']} {e['role']}: {e['text']}" for e in ev)
            + "\n\nUse the method ids and cost item ids exactly as written. Fill all eight rationale fields. Return ProposalIn.")


def _local(ceiling: dict | None, ccy: str, fx: dict | None) -> str:
    """" (≈ CA$13,900)": the ceiling in the city's currency, so the strategist compares like with like."""
    r = ceiling and ccy != "USD" and money.usd_rate(ccy, fx)
    return f" (≈ {money.display(estimates._r2(money.of_major(money.major(ceiling) / r, ccy)))})" if r else ""


# ---- the call ----
def call(ctx: research.Ctx, text: str) -> dict:
    """One structured answer, or {"error"}: an answer that can't be read (or a refused retry inside it) is an unusable
    answer, handled by the one repair and then FAILED (LIVE L5-A @1), never a crash. A stop or replay miss propagates."""
    try:
        agent = Agent(model=make_model(ctx.run.cassette, "launch_strategist", research.usage_sink(ctx, "model"), max_tokens=4000,
                                       admit=research.admitter(ctx, "model")),
                      system_prompt=SYSTEM, callback_handler=None)
        out = agent(text, structured_output_model=ProposalIn, cancel_signal=ctx.run.cancel).structured_output
    except (NoRecording, runner.Cancelled):
        raise
    except Exception as e:
        if research._caused_by(e, BudgetExhausted):
            raise
        return {"error": type(e).__name__}
    return out.model_dump() if out else {"error": "no answer"}


def _amount(ln: dict, ccy: str) -> dict | None:
    try:
        d = Decimal(str(ln.get("amount")).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    c = (ln.get("currency") or ccy).strip().upper()
    if not d.is_finite() or d <= 0 or c not in money.CODES:
        return None
    return money.of_major(d, c)


def with_search_ads(sub: dict, pid: str, cid: str) -> dict:
    """Owner, 28 Oct 2026: "each time we will write ads … every plan will probably do it". Every option includes Google
    Search ads (digital) as SUPPORTS unless it is the MAIN test, once digital is investigated; before validation, so
    its cost lines and lever are completed like any included method's."""
    d = db.get_record(f"dos_{pid}_{cid}_digital")
    if not d or d["status"] not in ("complete", "partial"):
        return sub
    for o in sub.get("options") or []:
        ms = [m for m in o.get("methods") or [] if m.get("method") != "digital" or m.get("role") in plans.INCLUDED]
        if not any(m.get("method") == "digital" for m in ms):
            ms.append({"method": "digital", "role": "SUPPORTS", "reason": "Google Search ads run alongside every plan."})
        o["methods"] = ms
    return sub


SAMPLE_SHAPE = ("\n\nThe founder has chosen the plan's shape: a creator collaboration (method creator) as the MAIN test and "
                "Google Search ads (method digital) as SUPPORTS, and no other method. Every option has exactly those two.")


def sample_shape(sub: dict, pid: str) -> dict:
    """Owner, 29 Oct 2026: a real brand's recorded demo is a creator (sample creators) with Google Ads beside it. The
    prompt says so (SAMPLE_SHAPE); this keeps any option that strays to exactly those two, with the model's reasons."""
    from . import influencers
    if not influencers.sample(pid):
        return sub
    for o in sub.get("options") or []:
        why = {m.get("method"): m.get("reason") for m in o.get("methods") or []}
        o["methods"] = [{"method": "creator", "role": "MAIN", "reason": why.get("creator") or "A local creator introduces the brand."},
                        {"method": "digital", "role": "SUPPORTS", "reason": why.get("digital") or "Google Search ads run alongside every plan."}]
    return sub


def validate(sub: dict, cid: str, ev: list[dict], faults: list) -> dict:
    """Deterministic: what survives is shown; what fails is dropped with a fault, never rewritten."""
    ids = {e["id"] for e in ev}
    qloo = {e["id"] for e in ev if e["role"] == "QLOO"}
    ccy = markets.CURRENCY.get(markets.CITIES[cid]["country"]) or "USD"
    others = [c["name"] for k, c in markets.CITIES.items() if k != cid]

    def cites(xs: list[str], where: str, money_line: bool = False) -> list[str]:
        good = [x for x in dict.fromkeys(xs) if x in ids and not (money_line and x in qloo)]
        if len(good) != len(set(xs)):
            faults.append({"field": where, "reason": "a citation that isn't this plan's evidence (or a Qloo result used as money) was removed"})
        return good

    def compares(text: str) -> bool:
        t = markets.fold(text)
        return any(len(n) > 3 and f" {markets.fold(n)} " in f" {t} " for n in others)

    options = []
    for i, o in enumerate(sub.get("options") or []):
        oid = "AB"[i] if i < 2 else None
        if not oid:
            faults.append({"field": "options", "reason": "more than two options; the rest were dropped"})
            break
        ms = [m for m in o.get("methods") or [] if m.get("method") in research.METHODS and m.get("role") in plans.ROLES]
        if len({m["method"] for m in ms}) != len(ms) or sum(m["role"] == "MAIN" for m in ms) != 1:
            faults.append({"field": f"options.{oid}", "reason": "needs exactly one main test and each method once; dropped"})
            continue
        inc = [m["method"] for m in ms if m["role"] in plans.INCLUDED]
        refs = {ref: (label, flow) for m in inc for ref, label, flow in plans.items_of(m)}
        lines = []
        for j, ln in enumerate(o.get("cost_lines") or []):
            where = f"options.{oid}.cost_lines[{j}]"
            ref = ln.get("item") or None
            if ref and ref not in refs:
                faults.append({"field": where, "reason": f"{ref} isn't a cost item of this option's methods; dropped"})
                continue
            if ref and any(x["item_ref"] == ref for x in lines):
                continue  # one line per item: the first
            used = [m for m in dict.fromkeys(ln.get("used_by") or []) if m in inc] or ([ref.split(":")[0]] if ref else [])
            if not used:
                faults.append({"field": where, "reason": "serves no method in this option; dropped"})
                continue
            basis = ln.get("basis") if ln.get("basis") in MODEL_BASES else "NOT_KNOWN"
            amt = _amount(ln, ccy) if ln.get("amount") not in (None, "") else None
            note = " ".join((ln.get("basis_note") or "").split())[:200] or None
            if amt is None or basis in ("NOT_KNOWN", "NOT_COSTED"):  # no amount, so no note: it would read as a figure
                amt, basis, note = None, basis if basis in ("NOT_KNOWN", "NOT_COSTED") else "NOT_KNOWN", None
            elif basis in money.ASSUMED and not (note and re.search(r"\d", note)):
                if not ("budget_split" in o and ref in estimates.BASES["bases"]):  # @6: the table prices it, no fault
                    faults.append({"field": where, "reason": "an amount without its quantity, unit and period; not used"})
                amt, basis, note = None, "NOT_KNOWN", None
            if not ref:
                faults.append({"field": where, "reason": "not one of the listed cost items; dropped"})
                continue
            label = refs[ref][0]
            flow = ln.get("flow") if ln.get("flow") in money.FLOWS else (refs[ref][1] if ref else "expense")
            lines.append({"id": plans.line_id(ref) if ref else f"cl_s{oid.lower()}_{j}", "label": label, "item_ref": ref,
                          "amount": amt, "basis": basis, "flow": flow, "used_by": used, "basis_note": note,
                          "source_claim_ids": cites(ln.get("cites") or [], where, True), "by": "strategist"})
        lines = plans.complete(lines, inc)
        prose = {k: " ".join((o.get(k) or "").split())[:400] for k in ("why", "trade_off")}
        options.append({"id": oid, "methods": [{"method": m["method"], "role": m["role"],
                                                "reason": " ".join((m.get("reason") or "").split())[:300]} for m in ms],
                        "cost_lines": lines, **prose, **({"split": _split(o["budget_split"], inc, oid, faults)}
                                                         if "budget_split" in o else {})})
    if len(options) == 2 and {(m["method"], m["role"]) for m in options[0]["methods"]} == \
            {(m["method"], m["role"]) for m in options[1]["methods"]}:
        faults.append({"field": "options.B", "reason": "the same roles as option A; dropped (no fabricated alternative)"})
        options = options[:1]
    if options and options[0]["id"] != "A":  # option A failed: the surviving option is the proposal
        options[0]["id"] = "A"
    rationale = {}
    raw = sub.get("rationale") or []
    if isinstance(raw, dict):  # the @1/@2 shape, kept readable for their recordings
        raw = [{**st, "field": k} for k, sts in raw.items() for st in (sts or [])]
    for k in FIELDS:
        got = []
        for n, st in enumerate(st for st in raw if st.get("field") == k):
            text = " ".join((st.get("text") or "").split())[:400]
            if not text or specialist.money_or_reach(text) or compares(text):
                if text:
                    faults.append({"field": f"rationale.{k}[{n}]", "reason": f"\"{text[:60]}\": a figure or another city; dropped"})
                continue
            kind = "FACT" if st.get("kind") == "FACT" else "ASSUMPTION"
            got.append({"text": text, "kind": kind, "cites": cites(st.get("cites") or [], f"rationale.{k}[{n}]") if kind == "FACT" else []})
        if got:
            rationale[k] = got  # a field the strategist didn't return is absent (AC5-5)
    order = [o for o in dict.fromkeys(sub.get("preference") or []) if o in {x["id"] for x in options}]
    order += [o["id"] for o in options if o["id"] not in order]
    return {"options": options, "preference_order": order, "rationale": rationale}


def _split(raw: list | None, inc: list[str], oid: str, faults: list) -> list[dict]:
    """The strategist's split of the budget left under the ceiling: levers of this option's included methods, each
    once, with a whole positive percent. None usable: an equal split across the option's levers, and it says so."""
    out = []
    for n, x in enumerate(raw or []):
        lv = x.get("lever")
        try:
            pct = int(float(str(x.get("percent"))))
        except (TypeError, ValueError):
            pct = 0
        if lv not in estimates.LEVERS or lv.split(":")[0] not in inc or pct <= 0 or any(y["lever"] == lv for y in out):
            faults.append({"field": f"options.{oid}.budget_split[{n}]", "reason": f"{lv or 'a lever'} isn't a budget lever of this option; dropped"})
            continue
        out.append({"lever": lv, "label": estimates.LEVERS[lv][1], "percent": pct, "why": " ".join((x.get("why") or "").split())[:300]})
    if not out:
        out = [{"lever": lv, "label": label, "percent": 1, "why": ""} for lv, (_, label) in estimates.LEVERS.items() if lv.split(":")[0] in inc]
        if out:
            faults.append({"field": f"options.{oid}.budget_split", "reason": "no usable split of the budget; split equally across the levers"})
    return out


def size(opt: dict, plan: dict, pid: str, cid: str, fx: dict | None, ceiling: dict | None, faults: list) -> None:
    """Deterministic, after the check (owner, 28 Oct 2026: "if the budget is 10k, then use the 10k"). Every line the
    founder or the scenario doesn't price and no listed rate covers takes the ESTIMATE table's figure (labeled, with its
    basis note); then what is left under 98% of the ceiling goes to the option's levers by the strategist's split, in
    whole units and whole days, so the planned total never passes the ceiling. An option without a split (an @5 answer
    and its recordings) is left as it was."""
    if "split" not in opt:
        return
    from . import scenarios
    days = (scenarios.of_plan(plan) or scenarios.base_of(pid, cid))["horizon_days"]
    est = lambda ref, q=None: estimates.estimate(ref, cid, fx, days, q)  # noqa: E731
    roles = {m["method"]: m["role"] for m in opt["methods"]}
    fixed = [ln for ln in plan.get("cost_lines") or [] if ln["by"] == "founder" or (ln["by"] == "scenario" and ln["amount"])]
    mine = {ln["item_ref"]: ln for ln in opt["cost_lines"] if ln["item_ref"] not in {x.get("item_ref") for x in fixed}}
    for ref, ln in mine.items():
        e = ln["basis"] != "LISTED_RATE" and est(ref)
        if e:
            ln.update(amount=e["amount"], basis="ESTIMATE", basis_note=e["basis_note"], source_claim_ids=[], by="strategist")
    split = [x for x in opt["split"] if all(mine.get(r, {}).get("basis") == "ESTIMATE" for r in estimates.TOGETHER.get(x["lever"], (x["lever"],)))]
    if len(split) < len(opt["split"]):
        faults.append({"field": f"options.{opt['id']}.split", "reason": "a lever you, your scenario or a listed rate already prices stays as it is"})
    opt["split"] = split
    usd = [money.to_usd(ln["amount"], fx) for ln in fixed + list(mine.values()) if ln["amount"] and money.counts(ln, roles)]
    if not ceiling or None in usd or not split:
        return
    left = ceiling["minor"] * 98 // 100 - sum(u["minor"] for u in usd)
    if left <= 0:
        faults.append({"field": f"options.{opt['id']}.split", "reason": "the estimates alone reach your ceiling, so nothing was added"})
        return
    whole, carry = sum(x["percent"] for x in split), 0
    for x in sorted(split, key=lambda x: x["lever"] == estimates.MEDIA):  # media last: it takes what whole units leave
        refs = estimates.TOGETHER.get(x["lever"], (x["lever"],))
        share = left * x["percent"] // whole + carry
        before = sum(mine[r]["amount"]["minor"] for r in refs)
        ccy = mine[refs[0]]["amount"]["ccy"]
        budget = before + int(money.of_major(money.major(money.usd(share)) / money.usd_rate(ccy, fx), ccy)["minor"])
        why = f"; sized to use {round(100 * x['percent'] / whole)}% of the budget left under your {money.display(ceiling)} ceiling"
        if x["lever"] == estimates.MEDIA:
            unit = 10 ** money.exp(ccy)
            daily = {"minor": budget // days // unit * unit, "ccy": ccy}
            mine[refs[0]].update(amount={"minor": daily["minor"] * days, "ccy": ccy}, daily=daily, days=days,
                                 basis_note=f"Estimate: {money.display(daily)} a day × {days} days of ads" + why)
        else:
            unit = estimates.LEVERS[x["lever"]][0]
            q0 = next(q for q, u in estimates.BASES["bases"][refs[0]]["qty"] if u == unit)
            cost = lambda q: [est(r, {unit: q}) for r in refs]  # noqa: E731
            one = sum(e["amount"]["minor"] for e in cost(1))
            q = max(q0, int(budget / one * 1.1) + 1)
            while q > q0 and sum(e["amount"]["minor"] for e in cost(q)) > budget:
                q -= 1
            for r, e in zip(refs, cost(q)):
                mine[r].update(amount=e["amount"], basis_note=e["basis_note"] + why)
        after = sum(mine[r]["amount"]["minor"] for r in refs)
        carry = max(0, share - money.to_usd({"minor": after - before, "ccy": ccy}, fx)["minor"])
        x.update(amount={"minor": after, "ccy": ccy}, text=f"{x['percent'] * 100 // whole}% · {x['label']} · {money.display({'minor': after, 'ccy': ccy})}"
                 + (f" ({money.display(mine[refs[0]]['daily'])} a day)" if x["lever"] == estimates.MEDIA else ""))


# ---- the check [R4] ----
def _judge(ctx: research.Ctx, items: list[dict]) -> dict:
    def go():
        try:
            agent = Agent(model=make_model(ctx.run.cassette, "strategist_check", research.usage_sink(ctx, "prose"), max_tokens=2500,
                                           admit=research.admitter(ctx, "prose")),
                          system_prompt=specialist.PROSE, callback_handler=None)
            out = agent("Sort each sentence:\n" + specialist.prose_listing(items), structured_output_model=research.ProseVerdicts,
                        cancel_signal=ctx.run.cancel).structured_output
        except (NoRecording, runner.Cancelled):
            raise
        except Exception as e:
            return {"error": "not allowed" if research._caused_by(e, BudgetExhausted) else type(e).__name__, "verdicts": []}
        return {"verdicts": [v.model_dump() for v in out.verdicts]} if out else {"error": "no answer", "verdicts": []}
    res, refused = research.memo(ctx, "prose", "strategist", go)
    return res or {"error": refused, "verdicts": []}


def check(ctx: research.Ctx, prop: dict, ev: list[dict], faults: list) -> dict:
    """One call judges every FACT statement and every LISTED_RATE against only its own citations."""
    by = {e["id"]: e for e in ev}
    items, where = [], []
    for k, sts in prop["rationale"].items():
        for i, st in enumerate(sts):
            if st["kind"] == "FACT":
                items.append({"field": f"rationale.{k}", "text": st["text"], "evidence": [by[c] for c in st["cites"]]})
                where.append(("fact", st))
    claim_ev = {e["claim_id"]: e for e in ev if e["claim_id"]}
    for o in prop["options"]:
        for ln in o["cost_lines"]:
            if ln["basis"] != "LISTED_RATE":
                continue
            cited = [claim_ev.get(c) or by.get(c) for c in ln["source_claim_ids"]]
            cited = [e for e in cited if e]
            ln["source_claim_ids"] = [e["claim_id"] or e["id"] for e in cited]
            listed = any(ln["amount"] in money.prices(e["text"]) for e in cited)  # deterministic: that amount and currency
            if not listed:
                _downgrade(ln, faults, f"options.{o['id']}.{ln['id']}", "the cited evidence doesn't state this amount")
                continue
            items.append({"field": f"cost line {ln['label']}", "evidence": cited,
                          "text": f"{ln['label']} costs {money.money_text(ln['amount'])}" + (f" ({ln['basis_note']})" if ln["basis_note"] else "")})
            where.append(("rate", ln, o["id"]))
    if not items:
        return {"judged": 0, "error": None}
    for n, it in enumerate(items, 1):
        it["id"] = f"s{n}"
    res = _judge(ctx, items)
    keep = specialist.prose_keep(items, res["verdicts"])
    kinds = {v["id"]: v["kind"] for v in res["verdicts"]}
    for it, w in zip(items, where):
        ok = keep[it["id"]] and kinds.get(it["id"]) == "SUPPORTED_FACT" and bool(it["evidence"])
        if w[0] == "fact" and not ok:
            w[1].update(kind="ASSUMPTION", cites=[], downgraded="its citations don't state it")
            faults.append({"field": it["field"], "reason": f"\"{it['text'][:60]}\": not supported by its citations; shown as an assumption"})
        elif w[0] == "rate" and not ok:
            _downgrade(w[1], faults, f"options.{w[2]}.{w[1]['id']}", "the cited evidence doesn't price this item in this unit")
    return {"judged": len(items), "error": res.get("error")}


def _downgrade(ln: dict, faults: list, where: str, why: str) -> None:
    if ln["basis_note"] and re.search(r"\d", ln["basis_note"]):  # the same rule as an assumed line: numbers, not adjectives
        ln.update(basis="ASSUMED", source_claim_ids=[])
    else:
        ln.update(basis="NOT_KNOWN", amount=None, basis_note=None, source_claim_ids=[])
    faults.append({"field": where, "reason": f"{why}: not a listed rate ({ln['basis'].lower().replace('_', ' ')})"})


# ---- the run ----
def start(pid: str, cid: str, dry_run: bool = False) -> dict:
    plan = db.get_record(plans.plan_id(pid, cid))
    if not plan:
        raise Refused(422, "Start your plan first: choose a Main test.")
    if not evidence(pid, cid):
        raise Refused(422, "Investigate at least one way in first: the strategist works only from research already done.")
    est = {"calls": "1 proposal + 1 check (at most one repair)", "expected_usd": EXPECTED, "max_usd": USD_CAP}
    if dry_run:
        return {"run_id": None, "est": est}
    rid = runner.start(pid, "plan_propose", f"{cid}:{plan['version']}", {"city_id": cid, "plan_version": plan["version"]})
    return {"run_id": rid, "est": est}


def propose_run(run: Run):
    pid, cid, pv = run.project_id, run.params["city_id"], run.params["plan_version"]
    plan = db.get_record(plans.plan_id(pid, cid), pv)
    ctx = research.Ctx(run, pid, cid, pv, {}, caps=CAPS, usd_cap=USD_CAP, expected_usd=EXPECTED, forced_usd=0.0,
                       stage="launch_strategist", cr_key=f"{pid}:{cid}:{pv}:propose:{run.id}")
    ev = run.step("evidence", lambda: evidence(pid, cid))
    ceiling = plans.ceiling_of(db.latest("founder_input", pid))
    from . import influencers
    text = prompt(pid, cid, plan, ev, ceiling) + (SAMPLE_SHAPE if influencers.sample(pid) else "")
    research._activity(run, f"Propose a plan for {markets.CITIES[cid]['name']}", "launch strategist", "one structured call",
                       status="RUNNING", role="Strategist")
    faults: list = []
    sub = sample_shape(with_search_ads(run.step("propose", lambda: call(ctx, text)), pid, cid), pid)
    if sub.get("error"):
        faults.append({"field": "options", "reason": f"the answer couldn't be read ({sub['error']})"})
    got = validate(sub, cid, ev, faults)
    if not got["options"]:  # the one repair: what failed, then FAILED with Retry and no plan change
        sub = run.step("repair", lambda: call(ctx, text + "\n\nYour previous answer couldn't be used: " +
                                              "; ".join(f["reason"] for f in faults[-6:]) + ". Return ProposalIn again."))
        got = validate(sample_shape(with_search_ads(sub, pid, cid), pid), cid, ev, faults)
    if not got["options"]:
        return "failed", "The strategist's answer couldn't be used, so nothing in your plan changed. Retry, or keep your roles."
    ccy = markets.CURRENCY.get(markets.CITIES[cid]["country"]) or "USD"  # the estimates' currency too
    run.step("rates", lambda: bool(plans.ensure_fx(pid, [ln for o in got["options"] for ln in o["cost_lines"]]
                                                   + [{"amount": {"minor": 1, "ccy": ccy}}])))  # §5.5, before any preview

    def checked_():
        c = check(ctx, got, ev, faults)  # downgrades in place
        for o in got["options"]:
            size(o, plan, pid, cid, plans.latest_fx(pid), ceiling, faults)  # after the check: a downgraded rate is an estimate
        return {**got, "check": c, "faults": faults}
    checked = run.step("check", checked_)
    rec = run.step("publish", lambda: db.put_record("proposal", pid, {
        "city_id": cid, "plan": {"id": plan["id"], "version": pv}, "options": checked["options"],
        "preference_order": checked["preference_order"], "rationale": checked["rationale"], "faults": checked["faults"],
        "check": checked["check"], "evidence": ev, "prompt_version": STRATEGIST_VERSION, "check_version": CHECK_VERSION,
        "schema_version": SCHEMA_VERSION, "model": config.NOVA_MODEL, "usage": research.counts(ctx.cr)},
        inputs=[{"id": plan["id"], "version": pv, "keys": []}], rid=proposal_id(pid, cid)))
    research._activity(run, f"Propose a plan for {markets.CITIES[cid]['name']}", "launch strategist",
                       f"proposal v{rec['version']} · {len(checked['options'])} option{'s' * (len(checked['options']) > 1)}",
                       role="Strategist")


HANDLERS["plan_propose"] = propose_run


# ---- preview and adoption: one computation [R3] ----
def apply(cur: dict, prop: dict, opt: dict) -> dict:
    """The plan as adopting `opt` would write it: the option's roles (role_by strategist; any other included method
    becomes OPTIONAL), and for each item the founder's amount if there is one, else the option's line. The scenario's
    media line stays the scenario's."""
    p = plans._fields(cur)
    roles = {m["method"]: m["role"] for m in opt["methods"]}
    old = {m["method"]: m for m in p["methods"]}
    out = []
    for method in research.METHODS:
        if method in roles:
            out.append({**(old.get(method) or {"dossier": None, "draft": None, "note": None}), "method": method,
                        "role": roles[method], "role_by": "strategist"})
        elif method in old:
            m = old[method]
            out.append({**m, "role": "OPTIONAL", "role_by": "strategist"} if m["role"] in plans.INCLUDED else m)
    kept = [ln for ln in p.get("cost_lines") or [] if ln["by"] == "founder" or (ln["by"] == "scenario" and ln["amount"])]
    have = {ln["item_ref"] for ln in kept if ln.get("item_ref")}
    lines = kept + [ln for ln in opt["cost_lines"] if not ln.get("item_ref") or ln["item_ref"] not in have]
    others = [o for o in prop["options"] if o["id"] != opt["id"]]
    return {**p, "methods": out, "cost_lines": lines, "source": "chosen", "rationale": prop["rationale"],
            "proposal": {"id": prop["id"], "version": prop["version"], "option": opt["id"]},
            "alternative": others and {"id": prop["id"], "version": prop["version"], "option": others[0]["id"]} or None}


def _option(pid: str, cid: str, version: int | None, oid: str) -> tuple[dict, dict]:
    prop = db.get_record(proposal_id(pid, cid), version)
    opt = prop and next((o for o in prop["options"] if o["id"] == oid), None)
    if not opt:
        raise Refused(404, "No such proposal option.")
    return prop, opt


def preview(pid: str, cid: str, version: int | None, oid: str, cur: dict | None = None, ceiling: dict | None = None) -> dict:
    """GET …/proposals/{id}/options/{opt}/preview: exactly what adoption would write against the current plan."""
    cur = cur or db.get_record(plans.plan_id(pid, cid))
    prop, opt = _option(pid, cid, version, oid)
    p = plans.finish(pid, cid, apply(cur, prop, opt))
    ceiling = ceiling if ceiling is not None else plans.ceiling_of(db.latest("founder_input", pid))
    m = plans.money_of({**p, "id": plans.plan_id(pid, cid), "project_id": pid}, ceiling)  # the city and FX its estimates use
    return {"base_version": cur["version"], "roles": [{k: x[k] for k in ("method", "role", "role_by")} for x in p["methods"]],
            "cost_lines": m["lines"], "money": {k: m[k] for k in ("cash_out_known", "expense_known", "inventory_known", "deposits_known",
                                                                  "assumed_known", "unknown", "unconverted", "text", "remaining", "planned")},
            "fit": m["fit"]}


def adopt(pid: str, cid: str, version: int, oid: str, base_version: int) -> dict:
    """POST …/adopt: Plan v+1 (`source: chosen`), refused (409) when the plan moved since the preview's base."""
    prop, opt = _option(pid, cid, version, oid)
    return plans.write(pid, cid, base_version, lambda cur: apply(cur, prop, opt),
                       "Adopted Landfall's proposal", f"option {oid} of proposal v{version}")


def view(pid: str, cid: str, cur: dict | None, ceiling: dict | None) -> dict | None:
    """The latest proposal with each option previewed against the current plan and fitted against the current ceiling
    (0 model calls), the recommendation, and the latest propose run."""
    run = db.q("SELECT id, status, detail FROM runs WHERE project_id=? AND kind='plan_propose' AND input_key LIKE ? "
               "ORDER BY started_at DESC, rowid DESC LIMIT 1", pid, f"{cid}:%")
    run = run and {"id": run[0]["id"], "status": run[0]["status"], "message": json.loads(run[0]["detail"] or "{}").get("message")}
    prop = db.get_record(proposal_id(pid, cid))
    if not prop or not cur:
        return run and {"run": run, "id": None}
    options = []
    for o in prop["options"]:
        pv = preview(pid, cid, prop["version"], o["id"], cur, ceiling)
        options.append({**o, "preview": pv})
    fits = {o["id"]: o["preview"]["fit"] for o in options}
    rec = next((o for o in prop["preference_order"] if fits.get(o) != "OVER_CEILING"), None)
    adopted = (cur.get("proposal") or {})
    return {"id": prop["id"], "version": prop["version"], "plan": prop["plan"], "options": options,
            "preference_order": prop["preference_order"], "recommendation": rec, "rationale": prop["rationale"],
            "faults": prop["faults"], "check": prop["check"], "evidence": prop["evidence"], "run": run,
            "adopted": adopted.get("id") == prop["id"] and adopted.get("version") == prop["version"] and adopted.get("option"),
            "labels": {k: v.format(city=markets.CITIES[cid]["name"]) for k, v in FIELDS.items()},
            "made_from_version": prop["plan"]["version"], "prompt_version": prop["prompt_version"]}
