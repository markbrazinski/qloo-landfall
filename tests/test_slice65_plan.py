"""Slice 6.5 increments B and C, server side: Plan, sign-off pins, recipient-keyed packages, carry-forward, products,
the earlier-plan fence, the Gmail guard, manual withdraw and templates (BUILD-PLAN "Slice 6.5" AC4, AC5–AC5d, AC14;
CONTRACTS B10). SYNTHETIC pages, model answers and providers: they prove handling, never a live integration."""
import pytest
from test_markets import idle, markets_env  # noqa: F401
from test_slice4 import env, research_ready  # noqa: F401
from test_slice45_runner import KC_BRANDS, spec_env  # noqa: F401
from test_slice6 import (approve_and_run, configure, connect, deliver_env, digital_packaged, edit, provider,  # noqa: F401
                         view, with_product)

from landfall import config, db, delivery, plans, runner, specialist

KC_MAIL = "hello@kettlecrane.example"


@pytest.fixture
def plan_env(deliver_env, monkeypatch):
    """The slice-6 Toronto retail case plus Bramble Grocer, a PENDING candidate named on a page read; every copy prompt
    is kept so a test can see which recipient the writer was asked to address."""
    code, page = deliver_env.web.pages[KC_BRANDS]
    deliver_env.web.pages[KC_BRANDS] = (code, page.replace(
        "Wholesale enquiries", "Our neighbours at Bramble Grocer also stock local makers. Wholesale enquiries"))
    deliver_env.prompts = []
    retail = deliver_env.spec_script

    def with_bramble(T):
        out = retail(T)
        out["shops"].append({"name": "Bramble Grocer", "own_site": "http://bramble.example/", "claims": []})
        return out
    deliver_env.spec_script = with_bramble

    def copy_call(ctx, prompt):
        deliver_env.prompts.append(prompt)
        return deliver_env.copy
    monkeypatch.setattr(delivery, "copy_call", copy_call)
    return deliver_env


def investigate(client, pid, method="retail"):
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/{method}/investigate", json={})
    assert r.status_code == 200, r.text
    idle(pid)


def dossiered(client, env):
    from test_slice4 import toronto_pages, toronto_script
    pid = with_product(client, env)
    toronto_pages(env.web)
    env.scripts.setdefault("toronto", toronto_script)
    assert client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]}).status_code == 200
    idle(pid)
    investigate(client, pid)
    return pid


def develop(client, pid, method="retail"):
    r = client.post(f"/api/projects/{pid}/dossiers/dos_{pid}_toronto_{method}/develop", json={})
    assert r.status_code == 200, r.text
    idle(pid)


def beachhead(client, pid, city="toronto"):
    v = db.latest("shortlist", pid)["version"]
    assert client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": v, "beachhead": city}).status_code == 200


def put_plan(client, pid, roles, base="current"):
    cur = db.get_record(f"plan_{pid}_toronto")
    base = (cur and cur["version"]) if base == "current" else base
    return client.put(f"/api/projects/{pid}/plans/toronto", json={
        "base_version": base, "methods": [{"method": m, "role": r} for m, r in roles.items()]})


def expect_of(client, pid):
    v = client.get(f"/api/projects/{pid}/plans/toronto").json()
    return {m["method"]: {"dossier_version": m["investigation"]["dossier"]["version"], "draft_version": m["draft"]["version"]}
            for m in v["methods"] if m["role"] and m["role"]["role"] in ("MAIN", "SUPPORTS") and m["draft"]}


def sign(client, pid, expect=None):
    v = client.get(f"/api/projects/{pid}/plans/toronto").json()
    return client.post(f"/api/projects/{pid}/plans/toronto/sign-off",
                       json={"base_version": v["plan"]["version"], "expect": expect if expect is not None else expect_of(client, pid)})


def signed(client, env, method="retail"):
    pid = dossiered(client, env)
    develop(client, pid)
    beachhead(client, pid)
    assert put_plan(client, pid, {method: "MAIN"}).status_code == 200
    assert sign(client, pid).status_code == 200
    return pid


def lead_of(pid, name, method="retail"):
    d = db.get_record(f"dos_{pid}_toronto_{method}")
    return next(c["lead_id"] for c in d["candidates"] if c["name"] == name)


def prepare(client, pid, lead_id=None, method="retail"):
    r = client.post(f"/api/projects/{pid}/drafts/drf_{pid}_toronto_{method}/package", json={"partner_lead_id": lead_id})
    assert r.status_code == 200, r.text
    return r.json()


def ready(client, pid, pkg_id):
    """A sender name and the unsupported sentence removed, so the package can be approved."""
    v = view(client, pid, pkg_id)
    return edit(client, pid, v, {"from_name": "Maya at Tidewell",
                                 "segments": [s["text"] for s in v["copy"]["segments"] if "award" not in s["text"]]})


# ---- AC5c [P65-R4]: sign-off pins exactly what the founder saw ----
def test_sign_off_pins_the_versions_seen_and_refuses_moved_or_missing_ones(client, plan_env, monkeypatch):
    pid = dossiered(client, plan_env)
    assert put_plan(client, pid, {"retail": "MAIN"}).status_code == 409  # no beachhead yet
    beachhead(client, pid)
    assert put_plan(client, pid, {"retail": "MAIN", "popup": "MAIN"}).status_code == 422
    assert put_plan(client, pid, {"atlantis": "MAIN"}).status_code == 422
    assert put_plan(client, pid, {"retail": "MAIN", "digital": "NOT_NOW"}).json() == {"version": 1}
    assert put_plan(client, pid, {"retail": "MAIN"}, base=None).status_code == 409  # a stale base
    exp = {"retail": {"dossier_version": 1, "draft_version": 1}}
    r = sign(client, pid, exp)
    assert r.status_code == 422 and "Develop" in r.json()["detail"]

    real = specialist.draft_call
    monkeypatch.setattr(specialist, "draft_call", lambda ctx, prompt: (_ for _ in ()).throw(RuntimeError("SYNTHETIC failure")))
    develop(client, pid)
    assert not db.get_record(f"drf_{pid}_toronto_retail") and sign(client, pid, exp).status_code == 422
    monkeypatch.setattr(specialist, "draft_call", real)
    develop(client, pid)  # the retry; finishing late signs nothing
    v = client.get(f"/api/projects/{pid}/plans/toronto").json()
    assert v["plan"]["signed_off"] is None and v["plan"]["version"] == 1
    assert plans.stage(pid)["signed_off"] is False  # (the project view; this fixture's product record is minimal)
    seen = expect_of(client, pid)

    investigate(client, pid)  # a concurrent re-investigation: the screen's versions moved
    r = sign(client, pid, seen)
    assert r.status_code == 409 and "changed while you were signing" in r.json()["detail"]
    develop(client, pid)
    seen = expect_of(client, pid)
    r = sign(client, pid, seen)
    assert r.status_code == 200, r.text
    rec = db.get_record(f"plan_{pid}_toronto")
    pin = next(m for m in rec["methods"] if m["method"] == "retail")
    assert (pin["dossier"]["version"], pin["draft"]["version"]) == (seen["retail"]["dossier_version"], seen["retail"]["draft_version"])
    assert rec["signed_off"]["by"] == "founder" and next(m for m in rec["methods"] if m["method"] == "digital")["draft"] is None

    # a newer draft after sign-off: pins and packages stay, and it is flagged
    pkg = prepare(client, pid, lead_of(pid, "Kettle & Crane"))
    idle(pid)
    assert view(client, pid, pkg["package_id"])["plan_ref"] == {"id": rec["id"], "version": rec["version"]}
    dr = db.get_record(f"drf_{pid}_toronto_retail")
    assert client.patch(f"/api/projects/{pid}/drafts/{dr['id']}", json={"base_version": dr["version"], "fields": {
        "success_measure": "Repeat orders in six weeks"}}).status_code == 200
    v = client.get(f"/api/projects/{pid}/plans/toronto").json()
    assert "A newer draft of Retail exists; sign again to use it" in v["plan"]["stale"]
    assert db.get_record(f"plan_{pid}_toronto")["version"] == rec["version"]
    assert view(client, pid, pkg["package_id"])["source"]["version"] == pin["draft"]["version"]


# ---- AC5 / AC5b [P65-R2]: a package per recipient ----
def test_each_recipient_gets_a_package_and_replacing_sets_one_aside(client, plan_env):
    pid = signed(client, plan_env)
    kc, br = lead_of(pid, "Kettle & Crane"), lead_of(pid, "Bramble Grocer")
    assert client.post(f"/api/projects/{pid}/leads/{br}/contact", json={"base_version": 0, "contact": "hi@bramble.example"}).status_code == 200
    a, b = prepare(client, pid, kc), prepare(client, pid, br)  # concurrently
    idle(pid)
    assert a["package_id"] != b["package_id"] and "~" in a["package_id"] and "~" in b["package_id"]
    va, vb = view(client, pid, a["package_id"]), view(client, pid, b["package_id"])
    assert (va["partner"]["name"], va["partner"]["basis"], va["recipients"]["to"]) == ("Kettle & Crane", "KEPT", [KC_MAIL])
    assert (vb["partner"]["name"], vb["partner"]["basis"], vb["recipients"]["to"]) == ("Bramble Grocer", "YOUR_CHOICE", ["hi@bramble.example"])
    assert vb["recipients_by"]["to"] == "YOU_SAID" and va["recipients_by"]["to"] == "SOURCE"
    asked = {p.split("Write the first inquiry email to ")[1].split(" (")[0] for p in plan_env.prompts}
    assert asked == {"Kettle & Crane", "Bramble Grocer"}  # neither writer saw the other recipient
    lst = {p["partner"]["name"]: p for p in client.get(f"/api/projects/{pid}/drafts/drf_{pid}_toronto_retail").json()["packages"]}
    assert set(lst) == {"Kettle & Crane", "Bramble Grocer"}
    rej = db.get_record(f"dos_{pid}_toronto_retail")
    assert client.post(f"/api/projects/{pid}/drafts/drf_{pid}_toronto_retail/package",
                       json={"partner_lead_id": "lead_nope"}).status_code == 422
    assert not any(c["decision"] == "REJECTED" and c.get("lead_id") for c in rej["candidates"])  # Loblaws has no Lead

    # replace Kettle & Crane: set aside, kept as history, refused by the fence; the checklist moves on
    r = client.post(f"/api/projects/{pid}/packages/{a['package_id']}/set-aside", json={"base_version": va["version"], "reason": "replaced"})
    assert r.status_code == 200
    va = view(client, pid, a["package_id"])
    assert va["status"] == "set_aside" and va["fence"] and va["partner"]["name"] == "Kettle & Crane"
    assert client.post(f"/api/projects/{pid}/packages/{a['package_id']}/destinations/EMAIL/approve",
                       json={"base_version": va["version"]}).status_code == 409
    act = client.get(f"/api/projects/{pid}/plans/toronto/act").json()
    assert [e["package"]["id"] for e in act["earlier_plan"]] == [a["package_id"]]
    outreach = [i for i in act["items"] if i["kind"] == "OUTREACH"]
    assert [i["partner"] for i in outreach] == ["Bramble Grocer"]
    assert {i["kind"] for i in act["items"]} >= {"BRAND_ASSETS", "CHOOSE_RECIPIENTS", "OUTREACH", "DELIVER"}
    # chosen again: the same package, reactivated, no new run
    assert prepare(client, pid, kc) == {"run_id": None, "package_id": a["package_id"]}
    assert view(client, pid, a["package_id"])["status"] == "active"


def test_a_restart_during_preparation_resumes_one_package(client, plan_env, monkeypatch):
    pid = signed(client, plan_env)
    kc = lead_of(pid, "Kettle & Crane")
    submit = runner._submit
    monkeypatch.setattr(runner, "_submit", lambda rid: None)  # the process stops before the run executes
    first = prepare(client, pid, kc)
    assert prepare(client, pid, kc) == first  # a repeat click while queued is the same run
    monkeypatch.setattr(runner, "_submit", submit)
    runner.resume_all()
    idle(pid)
    v = view(client, pid, first["package_id"])
    assert v["version"] == 1 and len(db.q("SELECT 1 FROM runs WHERE project_id=? AND kind='delivery_prepare'", pid)) == 1


def test_a_legacy_package_is_adopted_by_its_recipient(client, plan_env):
    pid = dossiered(client, plan_env)
    develop(client, pid)
    legacy = prepare(client, pid)  # the pre-6.5 route: the draft's first kept partner, unsuffixed
    idle(pid)
    assert "~" not in legacy["package_id"]
    old = db.get_record(legacy["package_id"])
    db.put_record("package", pid, {**delivery._payload(old), "partner": {k: v for k, v in old["partner"].items() if k != "lead_key"}},
                  inputs=old["inputs"], rid=old["id"])  # as an L6-era record: no lead_key
    beachhead(client, pid)
    put_plan(client, pid, {"retail": "MAIN"})
    assert sign(client, pid).status_code == 200
    assert prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"] == legacy["package_id"]


@pytest.mark.parametrize("outcome", ["timeout", None])
def test_the_same_recipient_rediscovered_never_gets_a_second_send(client, plan_env, monkeypatch, provider, outcome):
    """AC5b: after UNCERTAIN or SUCCEEDED, a re-investigation's new Lead id resolves to the same package and slot."""
    pid = signed(client, plan_env)
    kc = lead_of(pid, "Kettle & Crane")
    pkg = prepare(client, pid, kc)["package_id"]
    idle(pid)
    v = ready(client, pid, pkg)
    cases = config.REPLAY_CASES
    configure(monkeypatch)
    connect(pid, "EMAIL")
    if outcome:
        provider.after_commit["message"] = outcome
    status = approve_and_run(client, pid, v, "EMAIL").json()["status"]
    assert status == ("UNCERTAIN" if outcome else "SUCCEEDED")
    provider.after_commit.clear()
    monkeypatch.setattr(config, "REPLAY_CASES", cases)  # the SYNTHETIC research pages again, for the re-investigation

    investigate(client, pid)
    develop(client, pid)
    assert sign(client, pid).status_code == 200
    kc2 = lead_of(pid, "Kettle & Crane")  # a new dossier version's Lead record for the same recipient key
    assert db.get_record(kc2)["key"] == db.get_record(kc)["key"]
    assert prepare(client, pid, kc2)["package_id"] == pkg
    idle(pid)
    v = view(client, pid, pkg)
    assert v["version"] > 2 and v["from_name"] == "Maya at Tidewell"  # carried with the recipient
    assert "segments" in v["earlier_wording"]  # the founder's edited wording waits for an explicit choice
    assert client.post(f"/api/projects/{pid}/packages/{pkg}/wording",
                       json={"base_version": v["version"], "field": "segments", "choice": "mine"}).status_code == 200
    v = view(client, pid, pkg)
    apv = client.post(f"/api/projects/{pid}/packages/{pkg}/destinations/EMAIL/approve", json={"base_version": v["version"]})
    assert apv.status_code == 200, apv.text
    r = client.post(f"/api/projects/{pid}/packages/{pkg}/destinations/EMAIL/execute", json={"approval_id": apv.json()["approval_id"]})
    assert r.status_code == 409 and provider.writes.count("message") == 1
    assert ("hasn't been resolved" if outcome else "never does it twice") in r.json()["detail"]


def test_the_cross_package_guard_refuses_an_overlapping_open_send(client, plan_env, monkeypatch, provider):
    pid = signed(client, plan_env)
    a = prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"]
    b = prepare(client, pid, lead_of(pid, "Bramble Grocer"))["package_id"]
    idle(pid)
    configure(monkeypatch)
    connect(pid, "EMAIL")
    provider.after_commit["message"] = "timeout"
    assert approve_and_run(client, pid, ready(client, pid, a), "EMAIL").json()["status"] == "UNCERTAIN"
    provider.after_commit.clear()
    vb = edit(client, pid, ready(client, pid, b), {"to": [KC_MAIL.upper()]})  # one person reached through two leads
    apv = client.post(f"/api/projects/{pid}/packages/{b}/destinations/EMAIL/approve", json={"base_version": vb["version"]}).json()
    r = client.post(f"/api/projects/{pid}/packages/{b}/destinations/EMAIL/execute", json={"approval_id": apv["approval_id"]})
    assert r.status_code == 409 and KC_MAIL in r.json()["detail"] and provider.writes.count("message") == 1


def test_racing_overlapping_sends_write_once_and_hold_until_resolved(client, plan_env, monkeypatch, provider):
    """[P65-I1] Two packages to one address, both past the read-only check at once: the atomic recipient claim lets
    exactly one write through; the claim survives UNCERTAIN and is freed only by a resolution."""
    import threading
    pid = signed(client, plan_env)
    a = prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"]
    b = prepare(client, pid, lead_of(pid, "Bramble Grocer"))["package_id"]
    idle(pid)
    configure(monkeypatch)
    connect(pid, "EMAIL")
    va = ready(client, pid, a)
    vb = edit(client, pid, ready(client, pid, b), {"to": [KC_MAIL]})
    apvs = [delivery.approve(pid, v["id"], "EMAIL", v["version"])["approval_id"] for v in (va, vb)]
    check, both = delivery.open_overlap, threading.Barrier(2)

    def race(*args):
        out = check(*args)
        both.wait(timeout=15)
        return out
    monkeypatch.setattr(delivery, "open_overlap", race)
    provider.after_commit["message"] = "timeout"
    results, refused = [], []

    def go(pkg, apv):
        try:
            results.append(delivery.execute(pid, pkg, "EMAIL", apv)["status"])
        except delivery.Refused as e:
            refused.append(e.code)
    ts = [threading.Thread(target=go, args=x) for x in zip((a, b), apvs)]
    [t.start() for t in ts]
    [t.join(timeout=20) for t in ts]
    assert results == ["UNCERTAIN"] and refused == [409] and provider.writes.count("message") == 1
    held = db.q("SELECT action_id FROM recipient_claims WHERE project_id=?", pid)
    assert {r["action_id"] for r in held} and len({r["action_id"] for r in held}) == 1
    delivery.recover()  # a restart keeps the claim
    assert db.q("SELECT 1 FROM recipient_claims WHERE project_id=? AND address=?", pid, KC_MAIL)
    delivery.resolve(pid, held[0]["action_id"], False, None, None)  # "It didn't happen" frees the address
    assert not db.q("SELECT 1 FROM recipient_claims WHERE project_id=?", pid)


# ---- AC4 [P65-R1]: the fence ----
def test_removing_a_method_fences_its_packages_before_signing_again(client, plan_env, monkeypatch, provider):
    pid = signed(client, plan_env)
    pkg = prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"]
    idle(pid)
    v = ready(client, pid, pkg)
    configure(monkeypatch)
    connect(pid, "EMAIL")
    apv = client.post(f"/api/projects/{pid}/packages/{pkg}/destinations/EMAIL/approve", json={"base_version": v["version"]}).json()
    url = f"/api/projects/{pid}/packages/{pkg}/destinations/EMAIL"

    assert put_plan(client, pid, {"retail": "NOT_NOW"}).status_code == 200  # removed: plan v+1, unsigned
    assert client.get(f"/api/projects/{pid}/plans/toronto").json()["plan"]["signed_off"] is None
    for r in (client.post(f"/api/projects/{pid}/drafts/drf_{pid}_toronto_retail/package", json={}),
              client.post(f"{url}/approve", json={"base_version": v["version"]}),
              client.post(f"{url}/validate"),
              client.post(f"{url}/execute", json={"approval_id": apv["approval_id"]})):  # the approval it already holds
        assert r.status_code == 409 and "earlier plan" in r.json()["detail"], r.text
    assert provider.writes == []
    act = client.get(f"/api/projects/{pid}/plans/toronto/act").json()
    assert act["plan_changed"] and [e["package"]["id"] for e in act["earlier_plan"]] == [pkg]
    assert view(client, pid, pkg)["fence"]

    assert put_plan(client, pid, {"retail": "MAIN"}).status_code == 200  # restored before re-signing: allowed
    provider.after_commit["message"] = "timeout"
    a = client.post(f"{url}/execute", json={"approval_id": apv["approval_id"]}).json()
    assert a["status"] == "UNCERTAIN"
    put_plan(client, pid, {"retail": "NOT_NOW"})
    assert client.post(f"/api/projects/{pid}/executions/{a['id']}/resolve", json={"happened": True}).status_code == 200  # exempt

    put_plan(client, pid, {"retail": "MAIN"})
    assert plans.fence(pid, "toronto", "retail") is None
    beachhead(client, pid, "montreal")  # another beachhead
    assert "beachhead" in plans.fence(pid, "toronto", "retail")
    beachhead(client, pid, "toronto")
    put_plan(client, pid, {"retail": "MAIN", "popup": "SUPPORTS"})  # added but unsigned: enables nothing
    assert "Sign off" in plans.fence(pid, "toronto", "popup") and plans.fence(pid, "toronto", "retail") is None


def test_an_ordinary_project_needs_a_signed_plan_before_any_delivery(client, plan_env, monkeypatch):
    """[P65-I2] No beachhead and no plan: only tests of the slice-6 route (and replay or demo projects) may prepare,
    approve, validate or execute; an ordinary project is refused on all four, and an UNCERTAIN send stays resolvable."""
    pid = dossiered(client, plan_env)
    develop(client, pid)
    assert plans.fence(pid, "toronto", "retail") is None  # PRE_PLAN_ROUTE, as the slice-6 tests run
    pkg = prepare(client, pid)["package_id"]
    idle(pid)
    configure(monkeypatch)
    connect(pid, "EMAIL")
    v = ready(client, pid, pkg)
    apv = delivery.approve(pid, pkg, "EMAIL", v["version"])["approval_id"]
    monkeypatch.setattr(plans, "PRE_PLAN_ROUTE", False)
    assert "beachhead" in plans.fence(pid, "toronto", "retail")
    dr = db.get_record(v["source"]["id"])
    assert client.post(f"/api/projects/{pid}/drafts/{dr['id']}/package", json={}).status_code == 409
    for call in (lambda: delivery.approve(pid, pkg, "EMAIL", v["version"]),
                 lambda: delivery.provider_validate(pid, pkg, "META_ADS"),
                 lambda: delivery.execute(pid, pkg, "EMAIL", apv)):
        with pytest.raises(delivery.Refused) as e:
            call()
        assert e.value.code == 409, e.value.detail
    db.x("UPDATE projects SET mode='replay' WHERE id=?", pid)  # replay evidence keeps the slice-6 route
    assert plans.fence(pid, "toronto", "retail") is None


# ---- AC5d [P65-R5, R6]: carry-forward and products ----
def test_re_prepare_carries_independent_decisions_and_reviews_edited_wording(client, plan_env, monkeypatch):
    pid, v = digital_packaged(client, plan_env, monkeypatch)
    img = v["assets"][0]["artifact"]
    v = edit(client, pid, v, {"radius_km": 12, "max_cpc": {"amount": "1.50", "ccy": "CAD"}, "remove_assets": [img["id"]],
                              "headlines": ["Tidewell in Toronto now", *[h["text"] for h in v["copy"]["headlines"][1:]]]})
    dr = db.get_record(v["source"]["id"])
    client.patch(f"/api/projects/{pid}/drafts/{dr['id']}", json={"base_version": dr["version"], "fields": {
        "success_measure": "Sign-ups per week"}})
    client.post(f"/api/projects/{pid}/drafts/{dr['id']}/package", json={})
    idle(pid)
    n = view(client, pid, v["id"])
    assert n["version"] == v["version"] + 1 and n["source"]["version"] == dr["version"] + 1
    geo = n["targeting"]["geo"]
    assert (geo["radius_km"], geo["radius_by"]) == (12, "YOU_SAID") and n["budget"]["max_cpc"] == {"minor": 150, "ccy": "CAD"}
    assert img["sha256"] in n["excluded"] and all(a["artifact"]["sha256"] != img["sha256"] for a in n["assets"])
    assert n["earlier_wording"]["headlines"][0]["text"] == "Tidewell in Toronto now"
    assert n["copy"]["headlines"][0]["text"] == "Sparkling tea in Toronto"  # regenerated, shown beside yours
    assert any("earlier wording" in m["text"] for d in n["destination_states"] for m in d["state"]["missing"])
    assert n["creative"]["landing_by"] == "DERIVED"  # provenance kept, never relabelled YOU SAID
    r = client.post(f"/api/projects/{pid}/packages/{v['id']}/wording", json={"base_version": n["version"], "field": "headlines", "choice": "mine"})
    assert r.status_code == 200
    assert view(client, pid, v["id"])["copy"]["headlines"][0]["text"] == "Tidewell in Toronto now"


def test_deselecting_a_product_excludes_its_image_flags_its_sentences_and_outdates_approval(client, plan_env):
    pid = signed(client, plan_env)
    pkg = prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"]
    idle(pid)
    v = view(client, pid, pkg)
    prod_e = next(e["id"] for e in v["evidence"] if e["role"] == "PRODUCT")
    plan_env.copy = {**plan_env.copy, "paragraphs": [*plan_env.copy["paragraphs"][:2],
                                                     {"text": "Hibiscus Berry Tea is a sparkling tea.", "cites": [prod_e]}]}
    dr = db.get_record(f"drf_{pid}_toronto_retail")
    client.patch(f"/api/projects/{pid}/drafts/{dr['id']}", json={"base_version": dr["version"], "fields": {"success_measure": "Units in a month"}})
    put_plan(client, pid, {"retail": "MAIN"})
    assert sign(client, pid).status_code == 200
    prepare(client, pid, lead_of(pid, "Kettle & Crane"))
    idle(pid)
    v = ready(client, pid, pkg)
    v = edit(client, pid, v, {"confirm": [s["field"] for s in v["copy"]["segments"] if s["state"] == "TO_CHECK"]})
    apv = client.post(f"/api/projects/{pid}/packages/{pkg}/destinations/EMAIL/approve", json={"base_version": v["version"]})
    assert apv.status_code == 200, apv.text
    prod = v["products"][0]
    assert client.patch(f"/api/projects/{pid}/packages/{pkg}", json={"base_version": v["version"], "fields": {
        "products": ["prd_not_in_draft"]}}).status_code == 422
    sha = v["assets"][0]["artifact"]["sha256"]
    n = edit(client, pid, v, {"products": []})
    assert n["products"] == [] and n["products_by"] == "YOU_SAID" and sha in n["excluded"] and n["assets"] == []
    flagged = [s for s in n["copy"]["segments"] if prod["id"] in s["claim_ids"]]
    assert flagged and all(s["state"] in ("TO_CHECK", "YOU_SAID") for s in flagged)
    assert next(d for d in n["destination_states"] if d["destination"] == "EMAIL")["state"]["approval"] == "OUTDATED"
    assert view(client, pid, pkg)["products"] == []  # reload
    assert b"Hibiscus" not in client.get(f"/api/projects/{pid}/packages/{pkg}/exports/outreach_zip").content.split(b"attachments/")[-1][:40]


# ---- A9: withdrawing a manual mark keeps both versions ----
def test_withdrawing_a_manual_mark(client, plan_env):
    pid = signed(client, plan_env)
    pkg = prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"]
    idle(pid)
    m = client.post(f"/api/projects/{pid}/packages/{pkg}/destinations/EMAIL/manual", json={}).json()
    item = lambda: next(i for i in client.get(f"/api/projects/{pid}/plans/toronto/act").json()["items"]  # noqa: E731
                        if i["kind"] == "DELIVER")
    assert item()["status"] == "MARKED_SENT_BY_YOU"
    assert client.post(f"/api/projects/{pid}/packages/{pkg}/destinations/EMAIL/manual/withdraw", json={"record_id": m["id"]}).json() == {"version": 2}
    assert item()["status"] != "MARKED_SENT_BY_YOU" and db.get_record(m["id"], 1) and db.get_record(m["id"])["withdrawn_at"]
    d = next(d for d in view(client, pid, pkg)["destination_states"] if d["destination"] == "EMAIL")
    assert d["manual"] is None and len(d["manual_records"]) == 1


# ---- AC14 (UI.13): templates ----
def test_templates_fill_only_from_records_and_name_each_recipient(client, plan_env):
    pid = signed(client, plan_env)
    br = lead_of(pid, "Bramble Grocer")
    client.post(f"/api/projects/{pid}/leads/{br}/contact", json={"base_version": 0, "contact": "hi@bramble.example"})
    a, b = prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"], prepare(client, pid, br)["package_id"]
    idle(pid)
    ts = client.get(f"/api/projects/{pid}/templates").json()
    assert {t["method"] for t in ts} == {"retail", "popup", "creator", "community"} and all(t["origin"] == "LANDFALL_STARTER" for t in ts)
    t = next(t for t in ts if t["method"] == "retail")
    out = {}
    for pkg in (a, b):
        v = view(client, pid, pkg)
        r = client.post(f"/api/projects/{pid}/packages/{pkg}/apply-template",
                        json={"base_version": v["version"], "template": {"id": t["id"], "version": t["version"]}})
        assert r.status_code == 200 and r.json()["missing"] == ["sender_name"]
        out[pkg] = view(client, pid, pkg)
    assert "Kettle & Crane" in out[a]["email"]["body"] and "Bramble Grocer" in out[b]["email"]["body"]
    assert "Bramble" not in out[a]["email"]["body"] and "Toronto" in out[a]["email"]["body"]
    assert "Hibiscus Berry Tea" in out[a]["email"]["body"] and out[a]["template_ref"] == {"id": t["id"], "version": t["version"]}
    assert any("Needs sender name" in m["text"] for m in out[a]["destination_states"][0]["state"]["missing"])
    assert client.post(f"/api/projects/{pid}/packages/{a}/destinations/EMAIL/approve",
                       json={"base_version": out[a]["version"]}).status_code == 422
    v = edit(client, pid, out[a], {"from_name": "Maya"})
    v = view(client, pid, a)
    assert client.post(f"/api/projects/{pid}/packages/{a}/apply-template", json={
        "base_version": v["version"], "template": {"id": t["id"], "version": t["version"]}}).json()["missing"] == []
    v = view(client, pid, a)
    assert v["email"]["body"].rstrip().endswith("Maya")
    assert client.get(f"/api/projects/{pid}/packages/{a}/exports/eml").content.count(b"Kettle & Crane") >= 1

    before = (db.get_record(a)["version"], db.get_record(b)["version"])
    r = client.put(f"/api/projects/{pid}/templates/{t['id']}", json={"base_version": t["version"], "subject": "Hello {recipient_name}"})
    assert r.json() == {"id": t["id"], "version": 2}
    assert (db.get_record(a)["version"], db.get_record(b)["version"]) == before  # packages change only when applied again
    assert db.get_record(t["id"])["origin"] == "YOU_SAID"
    mine = client.post(f"/api/projects/{pid}/templates", json={"method": "retail", "name": "Short", "subject": "Hi", "body": "Hello {recipient_name}."})
    assert mine.status_code == 201
    assert client.post(f"/api/projects/{pid}/templates", json={"method": "digital", "subject": "x", "body": "y"}).status_code == 422
    put_plan(client, pid, {"retail": "NOT_NOW"})
    v = view(client, pid, b)
    assert client.post(f"/api/projects/{pid}/packages/{b}/apply-template",
                       json={"base_version": v["version"], "template": mine.json()}).status_code == 409  # the fence


# ---- P66-R2: a standalone lead (a creator the founder added) ----
def test_a_founder_added_creator_is_a_recipient_and_absence_later_is_not_staleness(client, plan_env):
    pid = dossiered(client, plan_env)
    plan_env.spec_script = lambda T: {"concept_name": "Creator tasting", "concept_line": "A creator films a first taste.",
                                      "creators": [], "missing": ["Whether a local creator would take part"]}
    investigate(client, pid, "creator")
    develop(client, pid, "creator")
    lid = client.post(f"/api/projects/{pid}/leads", json={"city_id": "toronto", "url": "https://www.tiktok.com/@tea.toronto"}).json()["lead_id"]
    beachhead(client, pid)
    put_plan(client, pid, {"creator": "MAIN"})
    assert sign(client, pid).status_code == 200
    pkg = prepare(client, pid, lid, method="creator")["package_id"]
    idle(pid)
    v = view(client, pid, pkg)
    assert "~" in pkg and v["partner"]["basis"] == "YOU_SAID" and v["partner"]["from_candidate"] is False
    investigate(client, pid, "creator")  # a later investigation that doesn't list them
    after = view(client, pid, pkg)
    assert after["version"] == v["version"] and after["stale"] == []


# ---- A2: one rights confirmation across the plan's packages ----
def test_confirming_rights_once_covers_every_active_package_in_the_plan(client, plan_env):
    pid = signed(client, plan_env)
    a = prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"]
    b = prepare(client, pid, lead_of(pid, "Bramble Grocer"))["package_id"]
    idle(pid)
    before = {k: db.get_record(k) for k in (a, b)}
    assert all(p["assets"] and not p.get("rights_confirmed") for p in before.values())
    r = client.post(f"/api/projects/{pid}/plans/toronto/rights")
    assert r.status_code == 200 and sorted(r.json()["packages"]) == sorted([a, b])
    for k, p in before.items():
        now = db.get_record(k)
        assert now["version"] == p["version"] + 1
        assert set(now["rights_confirmed"]) == {x["artifact"]["sha256"] for x in now["assets"]}
    assert client.post(f"/api/projects/{pid}/plans/toronto/rights").json() == {"packages": []}  # nothing left: no new versions


def test_rewrite_for_the_chosen_products_publishes_a_new_version(client, plan_env):
    """[P65-I3] Deselect, then Rewrite (the same draft version): the writer's new copy is a reviewable v+1 that
    outdates the approval; a second Rewrite for the same products writes nothing."""
    pid = signed(client, plan_env)
    lead = lead_of(pid, "Kettle & Crane")
    pkg = prepare(client, pid, lead)["package_id"]
    idle(pid)
    old = edit(client, pid, view(client, pid, pkg), {"products": []})
    plan_env.copy = {**plan_env.copy, "paragraphs": [{"text": "Would you discuss another idea?", "cites": []}]}
    prepare(client, pid, lead)
    idle(pid)
    new = view(client, pid, pkg)
    assert new["version"] == old["version"] + 1 and new["copy"] != old["copy"] and new["copy_for"] == []
    assert new["products"] == [] and new["products_by"] == "YOU_SAID" and new["source"] == old["source"]
    prepare(client, pid, lead)
    idle(pid)
    assert view(client, pid, pkg)["version"] == new["version"]


def test_adopting_a_legacy_package_records_the_signed_plan(client, plan_env, monkeypatch):
    """[P65-I3] A package from the slice-6 route, adopted by a signed plan: same id (slot lineage), v+1 with plan_ref."""
    pid = dossiered(client, plan_env)
    develop(client, pid)
    pkg = prepare(client, pid)["package_id"]
    idle(pid)
    before = db.get_record(pkg)
    assert before.get("plan_ref") is None
    beachhead(client, pid)
    put_plan(client, pid, {"retail": "MAIN"})
    assert sign(client, pid).status_code == 200
    assert prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"] == pkg
    idle(pid)
    after = db.get_record(pkg)
    plan = db.get_record(f"plan_{pid}_toronto")
    assert after["version"] == before["version"] + 1 and after["plan_ref"] == {"id": plan["id"], "version": plan["version"]}
    assert db.get_record(pkg, before["version"]).get("plan_ref") is None  # history unchanged


def test_unresolved_earlier_wording_survives_further_prepares(client, plan_env, monkeypatch):
    """[P65-I5] Founder wording stays beside the new text, and blocks approval, until Keep mine or Use the new text,
    however many draft revisions come first; template wording is the founder's too."""
    pid, old = digital_packaged(client, plan_env, monkeypatch)
    old = edit(client, pid, old, {"headlines": ["My earlier headline"]})
    for i in range(3):
        dr = db.get_record(old["source"]["id"])
        client.patch(f"/api/projects/{pid}/drafts/{dr['id']}", json={"base_version": dr["version"], "fields": {
            "success_measure": f"Measure edition {i}"}})
        client.post(f"/api/projects/{pid}/drafts/{dr['id']}/package", json={})
        idle(pid)
        new = view(client, pid, old["id"])
        assert new["earlier_wording"]["headlines"][0]["text"] == "My earlier headline", i
        assert any("earlier wording" in m["text"] for d in new["destination_states"] for m in d["state"]["missing"])
    r = client.post(f"/api/projects/{pid}/packages/{old['id']}/wording", json={"base_version": new["version"], "field": "headlines", "choice": "new"})
    assert r.status_code == 200 and "headlines" not in view(client, pid, old["id"])["earlier_wording"]


def test_template_wording_is_carried_for_review(client, plan_env):
    pid = signed(client, plan_env)
    pkg = prepare(client, pid, lead_of(pid, "Kettle & Crane"))["package_id"]
    idle(pid)
    v = view(client, pid, pkg)
    t = next(t for t in client.get(f"/api/projects/{pid}/templates").json() if t["method"] == "retail")
    r = client.post(f"/api/projects/{pid}/packages/{pkg}/apply-template", json={"base_version": v["version"], "template": {"id": t["id"], "version": t["version"]}})
    assert r.status_code == 200, r.text
    subject = view(client, pid, pkg)["copy"]["subject"]
    for i in range(2):
        dr = db.get_record(f"drf_{pid}_toronto_retail")
        client.patch(f"/api/projects/{pid}/drafts/{dr['id']}", json={"base_version": dr["version"], "fields": {"success_measure": f"Units {i}"}})
        put_plan(client, pid, {"retail": "MAIN"})
        assert sign(client, pid).status_code == 200
        prepare(client, pid, lead_of(pid, "Kettle & Crane"))
        idle(pid)
        assert view(client, pid, pkg)["earlier_wording"]["subject"] == subject, i
