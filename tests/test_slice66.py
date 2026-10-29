"""Slice 6.6: Influencers.club creator discovery (BUILD-PLAN "Slice 6.6"; CONTRACTS B11). Offline only: a SYNTHETIC
transport stands in for the provider, and the key is a SYNTHETIC test value."""
import json
import threading
from datetime import date

import httpx
import pytest

from landfall import config, db, influencers as ic, ledger, specialist

KEY = "SYNTHETIC-test-key-not-real"


@pytest.fixture
def env(monkeypatch):
    for k in ("influencers_club_api_key", "influencer_api_key", "LANDFALL_ENV", "LANDFALL_IC_ADMISSION", "LANDFALL_IC_TERMS_V24_CONFIRMED"):
        monkeypatch.delitem(config._ENV, k, raising=False)
    return lambda k, v: monkeypatch.setitem(config._ENV, k, v)


@pytest.fixture
def live(env, monkeypatch):
    """A connected deployment after the rights date, with a SYNTHETIC provider."""
    env("influencers_club_api_key", KEY)
    monkeypatch.setattr(ic, "TERMS_FROM", date(2020, 1, 1))
    monkeypatch.setattr(config, "PROVIDER_MODE", "live")
    fake = Fake()
    monkeypatch.setattr(ic, "transport", fake)
    return fake


class Fake:
    def __init__(self):
        self.calls, self.fail, self.n = [], None, 10

    def __call__(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if self.fail:
            if isinstance(self.fail, Exception):
                raise self.fail
            return self.fail, {"detail": "SYNTHETIC refusal"}
        if "classifier" in path:
            return 200, [{"name": {"Toronto": "Toronto, Ontario, Canada"}.get((params or {}).get("search"), "Brisbane, Queensland, Australia")}]
        if path.startswith("/creators/enrich"):
            return 200, {"result": {"email": "creator@synthetic.example", "email_status": "valid", "location": "Brisbane"}}
        page = body["paging"]["page"]
        accounts = json.loads(ic.SYNTHETIC.read_text())["accounts"][:min(self.n, body["paging"]["limit"])]
        return 200, {"total": 120, "credits_left": "9.70", "accounts": accounts if page == 0 else accounts[:4]}


def project(mode="live", beachhead="brisbane"):
    pid = db.new_id("prj")
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s1", "https://gotbag.example/", mode,
         str(config.ROOT / "fixtures" / "synthetic" / "tidewell-brand-v6") if mode == "replay" else None, None, db.now())
    fact = db.put_record("claim", pid, {"label": "What you sell", "value": "Bags and backpacks made from recycled materials"})
    db.put_record("brief", pid, {"brand_name": "GOT BAG", "facts": [fact["id"]]}, rid=f"brief_{pid}")
    db.put_record("shortlist", pid, {"cities": [{"city_id": beachhead, "included": True}], "beachhead": {"city_id": beachhead}},
                  rid=f"sl_{pid}")
    return pid


def test_no_key_is_unavailable_and_never_synthetic_in_a_live_project(env, monkeypatch):
    """[P66-R5] A live project with no key reads "not connected", even though the SYNTHETIC fixture ships in the build."""
    monkeypatch.setattr(config, "PROVIDER_MODE", "live")
    pid = project()
    assert ic.unavailable(pid) == "Creator discovery isn't connected in this deployment."
    with pytest.raises(ic.Refused) as e:
        ic.search(pid, "brisbane", "s1")
    assert e.value.code == 503
    assert db.records("creator_search", pid) == [] and ledger.ic_used("personal")["requests"] == 0
    env("influencers_club_api_key", KEY)
    monkeypatch.setattr(ic, "TERMS_FROM", date(2999, 1, 1))  # the rights gate still works (the owner opened it on 27 Oct)
    assert ic.unavailable(pid) == "Creator discovery opens on 1 Nov 2026."
    env("LANDFALL_IC_TERMS_V24_CONFIRMED", "1")
    assert ic.unavailable(pid) is None
    env("LANDFALL_IC_ADMISSION", "closed")
    assert "paused" in ic.unavailable(pid)


def test_demo_project_shows_synthetic_creators_and_dispatches_nothing(env):
    pid = project(mode="replay")
    r = ic.search(pid, "brisbane", "s1")
    s = db.get_record(r["search_id"])
    assert (s["origin"], s["status"], len(s["accounts"])) == ("SYNTHETIC", "COMPLETED", 8)
    assert all(a["full_name"].startswith("SYNTHETIC") for a in s["accounts"])
    assert ledger.ic_used("personal")["requests"] == 0


def test_a_search_is_two_requests_then_one_saved_reused_and_counted(live):
    pid = project()
    assert ic.search(pid, "brisbane", "s1", dry_run=True)["est"] == {"requests": 2, "credits": 0.24}
    r = ic.search(pid, "brisbane", "s1")
    s = db.get_record(r["search_id"])
    assert (s["origin"], s["status"], len(s["accounts"]), s["pool"]) == ("LIVE", "COMPLETED", 8, "personal")
    assert [c[1] for c in live.calls] == ["/discovery/classifier/audience-locations/", "/discovery/"]
    body = live.calls[1][3]
    assert body["filters"]["audience"]["location"][0]["name"] == "Brisbane, Queensland, Australia"
    assert body["paging"] == {"limit": 8, "page": 0} and "nlp_search" not in body and live.calls[1][2] == {"return_filter_values": "true"}
    assert body["filters"]["ai_search"] == "Bags and backpacks made from recycled materials"
    assert ledger.ic_used("personal") == {"requests": 2, "credits": 0.24}  # 8 creators × 0.03; the lookup is 0
    a = s["accounts"][0]
    assert a["profile_url"] == {"value": "https://www.instagram.com/synthetic_oceanloop/", "built": True} and "picture" not in a  # most followers first
    # reading never dispatches; the location is cached, so the next search is one request
    v = ic.view(pid, "brisbane", "s1")
    assert v["searches"][0]["accounts"][0]["audience_location"] == "SYNTHETIC city>31%" and v["attribution"] == "Creator data: Influencers.club"
    assert len(live.calls) == 2 and v["left"] == {"requests": 8, "credits": 3.76}
    assert ic.search(pid, "brisbane", "s1", dry_run=True)["est"]["requests"] == 1
    more = ic.search(pid, "brisbane", "s1", more_of=r["search_id"])
    assert db.get_record(more["search_id"])["request"]["page"] == 1 and live.calls[-1][3]["paging"]["page"] == 1
    assert ledger.ic_used("personal") == {"requests": 3, "credits": 0.36}
    assert KEY not in json.dumps([dict(r) for r in db.q("SELECT * FROM records")])  # the key never reaches a record


def test_beachhead_platform_and_page_are_server_decided(live):
    pid = project()
    with pytest.raises(ic.Refused) as e:
        ic.search(pid, "lisbon", "s1")
    assert e.value.code == 409
    r = ic.search(pid, "brisbane", "s1")
    for _ in range(ic.MAX_PAGE):
        r = ic.search(pid, "brisbane", "s1", more_of=r["search_id"])
    with pytest.raises(ic.Refused) as e:
        ic.search(pid, "brisbane", "s1", more_of=r["search_id"])
    assert e.value.code == 422
    yt = ic.search(pid, "brisbane", "s1", platform="youtube")
    body = live.calls[-1][3]
    assert "audience" not in body["filters"] and body["filters"]["location"] and "number_of_subscribers" in body["filters"]
    assert db.get_record(yt["search_id"])["platform"] == "youtube"


def test_failures_count_unknown_outcomes_are_never_retried_and_the_allowance_ends(live):
    pid = project()
    live.fail = 401
    r = ic.search(pid, "brisbane", "s1")  # the lookup fails: FAILED, never "no location"; nothing more sent [P66-I3]
    s = db.get_record(r["search_id"])
    assert s["status"] == "FAILED" and s["error"]["status"] == 401 and "lookup failed" in s["note"]
    assert ledger.ic_used("personal") == {"requests": 1, "credits": 0.0}
    live.fail = None
    ic.search(pid, "brisbane", "s1")
    live.fail = httpx.ReadTimeout("SYNTHETIC timeout")
    n = len(live.calls)
    r = ic.search(pid, "brisbane", "s1", follower_min=20000)  # a new request: the same one would come from the 30-day cache
    s = db.get_record(r["search_id"])
    assert s["status"] == "UNKNOWN" and "may have been charged" in s["note"] and len(live.calls) == n + 1  # once, not retried
    assert ledger.ic_used("personal") == {"requests": 4, "credits": 0.48}  # the unknown one counts at its full reservation
    live.fail = 500
    for k in range(6):  # distinct requests, so none is answered from the cache
        assert db.get_record(ic.search(pid, "brisbane", "s1", follower_min=30000 + k)["search_id"])["status"] == "FAILED"
    with pytest.raises(ic.Refused) as e:  # 10 used: the next new request is refused before dispatch (a cached one is free)
        ic.search(pid, "brisbane", "s1", follower_min=40000)
    assert e.value.code == 423 and "allowance" in e.value.detail and len(live.calls) == n + 7
    assert len(db.records("creator_search", pid)) == 9  # saved results stay usable
    assert ic.view(pid, "brisbane", "s1")["left"]["requests"] == 0


def test_pools_never_borrow_are_atomic_and_carry_in(env, monkeypatch):
    def take(pool, credits=0.0, pid=None, lookup=False):
        return ledger.reserve(ledger.IC, "discovery", pool=pool, credits_reserved=credits, project_id=pid, lookup=lookup)
    ok, refused = [], []

    def one():
        try:
            ok.append(take("personal"))
        except ledger.BudgetExhausted:
            refused.append(1)
    threads = [threading.Thread(target=one) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert (len(ok), len(refused)) == (10, 20) and ledger.ic_used("personal")["requests"] == 10  # never overshoots
    with pytest.raises(ledger.BudgetExhausted):
        take("personal")
    assert ledger.ic_left("judges") == {"requests": 20, "credits": 6.0}  # untouched: personal can't borrow it
    # judges: 2 requests and 0.6 credits per project, the location lookup exempt [P66-R3]
    take("judges", 0.0, "prjA", lookup=True)
    take("judges", 0.3, "prjA")
    take("judges", 0.2, "prjA")
    with pytest.raises(ledger.BudgetExhausted):
        take("judges", 0.0, "prjA")
    take("judges", 0.3, "prjB")
    assert ledger.ic_used("judges")["requests"] == 4
    # the hosted handover opens with the dev totals [P66-R1]; dev then refuses
    monkeypatch.setattr(config, "LEDGER_PATH", config.LEDGER_PATH.with_name("hosted.jsonl"))
    ledger.ic_carry_in("personal", 7, 1.2, "dev ledger at handover")
    assert ledger.ic_left("personal") == {"requests": 3, "credits": 2.8}
    env("LANDFALL_IC_ADMISSION", "closed")
    with pytest.raises(ledger.BudgetExhausted):
        take("personal")
    # nothing on the Influencers.club allocation touches §8.1's dollars
    assert ledger.totals()["usd"] == 0


def test_the_pool_is_never_chosen_by_the_client(env):
    env("LANDFALL_ENV", "judging")
    assert ic.pool_of("any-session") == "personal"  # no judge grant exists until DEPLOYMENT-PLAN A1
    assert set(ic.search.__code__.co_varnames[:ic.search.__code__.co_argcount]) == {
        "pid", "city", "session", "platform", "follower_min", "more_of", "dry_run"}


def test_choose_dedupes_with_a_founder_added_profile_and_enrich_finds_a_contact(live):
    pid = project()
    s = ic.search(pid, "brisbane", "s1")["search_id"]
    lid = ic.choose(pid, s, 0)["lead_id"]
    lead = db.get_record(lid)
    assert lead["key"] == "creator|https://www.instagram.com/synthetic_oceanloop/" and lead["basis"] == "FOUND_BY_PROVIDER"
    assert lead["audience_location"] == {"text": "SYNTHETIC city>31%", "state": "PROVIDER_ESTIMATE", "by": ic.ATTRIBUTION}
    assert lead["creator"]["location"]["state"] == "UNKNOWN" and lead["contact"] is None
    assert ic.choose(pid, s, 0)["lead_id"] == lid
    # a profile the founder added by URL is the same recipient
    mine = specialist.add_creator(pid, "brisbane", "https://www.instagram.com/synthetic_gearnotes/", None)["lead_id"]
    assert ic.choose(pid, s, 1)["lead_id"] == mine and db.get_record(mine)["origin"] == "YOU_SAID"
    n = len(live.calls)
    out = ic.enrich(pid, lid, "s1")
    assert out["contact"]["value"] == "creator@synthetic.example" and len(live.calls) == n + 1
    assert ic.enrich(pid, lid, "s1")["contact"] == out["contact"] and len(live.calls) == n + 1  # never twice
    assert db.get_record(lid)["creator"]["location"] == {"state": "PROVIDER", "text": "Brisbane"}
    assert ledger.ic_used("personal")["credits"] == pytest.approx(0.24 + 0.2)
    assert [c["lead_id"] for c in ic.view(pid, "brisbane", "s1")["chosen"]] == [lid, mine]


def test_a_crash_mid_request_becomes_unknown_at_startup_and_is_never_resent(live):
    """[P66-R4] The process stopped after admission: a restart finalizes it as unknown and resends nothing."""
    pid = project()
    ledger.reserve(ledger.IC, "discovery", pool="personal", project_id=pid, credits_reserved=0.3, city_id="brisbane")
    ic.recover()
    assert live.calls == []
    s = ic.view(pid, "brisbane", "s1")["searches"]
    assert s[0]["status"] == "UNKNOWN" and "may have been charged" in s[0]["note"]
    assert ledger.ic_used("personal") == {"requests": 1, "credits": 0.3}
    ic.recover()  # idempotent
    assert ledger.ic_used("personal")["requests"] == 1 and len(ic.view(pid, "brisbane", "s1")["searches"]) == 1
    ic.search(pid, "brisbane", "s1")  # search stays available


@pytest.mark.parametrize("status", [401, 429, 500])
def test_a_failed_classifier_is_failed_and_only_a_real_no_match_is_empty(live, monkeypatch, status):
    """[P66-I3]"""
    pid = project()
    live.fail = status
    s = db.get_record(ic.search(pid, "brisbane", "s1")["search_id"])
    assert s["status"] == "FAILED" and s["error"]["status"] == status and len(live.calls) == 1
    live.fail = None
    monkeypatch.setattr(live, "__call__", None, raising=False)
    orig = ic.transport
    monkeypatch.setattr(ic, "transport", lambda m, path, **kw: (200, []) if "classifier" in path else orig(m, path, **kw))
    s = db.get_record(ic.search(pid, "brisbane", "s1")["search_id"])
    assert s["status"] == "EMPTY" and "no location matching" in s["note"] and s["error"] is None


def test_the_rights_gate_holds_for_new_requests_beside_replayed_cases(env, monkeypatch):
    """[P66-I1] Unrelated upstream recordings never open the gate: before the date, a miss refuses before any
    reservation or transport."""
    pid = project()
    env("influencers_club_api_key", KEY)
    monkeypatch.setattr(ic, "TERMS_FROM", date(2099, 1, 1))
    monkeypatch.setattr(config, "REPLAY_CASES", [config.ROOT / "fixtures" / "synthetic" / "tidewell-brand-v6"])
    monkeypatch.setattr(config, "LIVE_STAGES", ("creator_discovery",))
    fake = Fake()
    monkeypatch.setattr(ic, "transport", fake)
    assert ic.unavailable(pid) == "Creator discovery opens on 1 Nov 2026."
    with pytest.raises(ic.Refused) as e:
        ic.search(pid, "brisbane", "s1")
    assert e.value.code == 503 and fake.calls == [] and ledger.ic_used("personal")["requests"] == 0


def test_a_found_contact_becomes_the_packages_to_with_its_source(live, monkeypatch):
    """[P66-I2] choose → package → Find contact: the package becomes v+1 with the sourced To and its provenance."""
    from landfall import delivery
    pid = project()
    sid = ic.search(pid, "brisbane", "s1")["search_id"]
    lid = ic.choose(pid, sid, 0)["lead_id"]
    lead = db.get_record(lid)
    db.put_record("experiment_draft", pid, {"source": {"dossier_id": f"dos_{pid}_brisbane_creator", "dossier_version": 1}},
                  rid=f"drf_{pid}_brisbane_creator")
    db.put_record("dossier", pid, {"candidates": []}, rid=f"dos_{pid}_brisbane_creator")
    pkg = db.put_record("package", pid, {"city_id": "brisbane", "method": "creator", "status": "active",
                                         "source": {"id": f"drf_{pid}_brisbane_creator", "version": 1},
                                         "partner": {"lead_id": lid, "lead_key": lead["key"], "name": lead["name"], "contact": None},
                                         "recipients": {"to": [], "cc": [], "bcc": []}, "recipients_by": {"to": None},
                                         "you_said": []}, rid="pkg_creator_synthetic")
    out = ic.enrich(pid, lid, "s1")
    assert out["packages"] == [pkg["id"]]
    now = db.get_record(pkg["id"])
    assert now["version"] == 2 and now["recipients"]["to"] == ["creator@synthetic.example"]
    assert now["recipients_by"]["to"] == "SOURCE"
    assert now["partner"]["contact"]["source"] == "influencers_club_enrichment" and now["partner"]["contact"]["email_status"] == "valid"
    assert delivery.partner_of(pid, db.get_record(lid), {"candidates": []})["contact"]["value"] == "creator@synthetic.example"


@pytest.mark.parametrize("op,meta", [("enrich", {"lead_id": "lead_x"}), ("location", {"city_id": "brisbane", "platform": "tiktok", "page": 0}),
                                     ("discovery", {"city_id": "brisbane", "platform": "youtube", "page": 1})])
def test_every_interrupted_request_becomes_a_visible_unknown_once(live, op, meta):
    """[P66-I4] Recovery records the operation it was (platform, page or lead), before finalizing, idempotently."""
    pid = project()
    ledger.reserve(ledger.IC, op, pool="personal", project_id=pid, credits_reserved=0.2 if op == "enrich" else 0.0 if op == "location" else 0.24,
                   lookup=op == "location", **meta)
    real = ledger.finalize
    ledger.finalize = lambda *a, **k: (_ for _ in ()).throw(SystemExit("SYNTHETIC crash during recovery"))
    try:
        with pytest.raises(SystemExit):
            ic.recover()
    finally:
        ledger.finalize = real
    ic.recover()
    ic.recover()
    assert live.calls == [] and ledger.ic_used("personal")["requests"] == 1
    if op == "enrich":
        recs = db.records("creator_enrichment", pid)
        assert len(recs) == 1 and recs[0]["status"] == "UNKNOWN" and recs[0]["lead_id"] == "lead_x"
    else:
        recs = ic.view(pid, "brisbane", "s1")["searches"]
        assert len(recs) == 1 and recs[0]["status"] == "UNKNOWN" and recs[0]["platform"] == meta["platform"]
        assert recs[0]["request"]["page"] == meta["page"]


def test_creator_location_is_never_audience_location(live):
    """[P66-I5] TikTok and YouTube filter where the creator is based; only Instagram's matched audience filter is an
    audience claim; an enriched location is the creator's."""
    pid = project()
    tk = ic.search(pid, "brisbane", "s1", platform="tiktok")["search_id"]
    v = next(s for s in ic.view(pid, "brisbane", "s1")["searches"] if s["id"] == tk)
    assert v["request"]["location_means"] == "CREATOR"
    assert all(a["audience_location"] is None and a["creator_location"] == "Brisbane, Queensland, Australia" for a in v["accounts"])
    lid = ic.choose(pid, tk, 0)["lead_id"]
    lead = db.get_record(lid)
    assert lead["creator"]["location"] == {"state": "SEARCH_FILTER", "text": "Brisbane, Queensland, Australia"}
    assert lead["audience_location"] is None
    ig = ic.search(pid, "brisbane", "s1")["search_id"]
    v = next(s for s in ic.view(pid, "brisbane", "s1")["searches"] if s["id"] == ig)
    assert v["request"]["location_means"] == "AUDIENCE" and all(a["creator_location"] is None for a in v["accounts"])
    ic.enrich(pid, lid, "s1")
    c = next(c for c in ic.view(pid, "brisbane", "s1")["chosen"] if c["lead_id"] == lid)
    assert c["creator_location"] == {"state": "PROVIDER", "text": "Brisbane"} and c["audience_location"] is None


def test_a_search_saves_the_request_sent_its_credits_and_pinned_inputs(live):
    """[P66-I6]"""
    pid = project()
    sid = ic.search(pid, "brisbane", "s1")["search_id"]
    rec = db.get_record(sid)
    sent = live.calls[-1]
    assert rec["request"]["sent"] == {"method": sent[0], "path": sent[1], "params": sent[2], "body": sent[3]}
    assert KEY not in json.dumps(rec)
    assert rec["credits"] == {"reserved": ic.LIMIT * ic.RATE, "charged": round(ic.LIMIT * ic.RATE, 4)}
    brief = db.latest("brief", pid)
    assert rec["inputs"]["brief"] == {"id": brief["id"], "version": brief["version"]}
    assert rec["inputs"]["category"]["from"] == "WHAT_YOU_SELL" and rec["inputs"]["category"]["claim"]["version"] == 1
    assert "products" in rec["inputs"] and "dossier" in rec["inputs"] and rec["inputs"]["creator_budget"] is None
    db.put_record("brief", pid, {"brand_name": "Changed", "facts": []}, rid=brief["id"])  # later edits leave it as saved
    assert db.get_record(sid)["inputs"]["brief"]["version"] == brief["version"]
    lid = ic.choose(pid, sid, 0)["lead_id"]
    ic.enrich(pid, lid, "s1")
    e = db.records("creator_enrichment", pid)[-1]
    assert e["request"]["body"]["handle"] and e["credits"]["charged"] == ic.ENRICH_CREDITS and KEY not in json.dumps(e)


def test_the_view_offers_only_what_the_allowance_admits(live, monkeypatch):
    """[P66-I7] An uncached lookup needs two requests; the last request is never spent on a lookup alone."""
    pid = project()
    c = ic.view(pid, "brisbane", "s1")["costs"]
    assert c["search"] == {"requests": 2, "credits": 0.24, "ok": True} and c["enrich"]["credits"] == 0.2
    monkeypatch.setitem(ledger.IC_POOLS, "personal", {"requests": 1, "credits": 4})
    c = ic.view(pid, "brisbane", "s1")["costs"]
    assert not c["search"]["ok"] and c["more"]["ok"] and c["enrich"]["ok"]
    with pytest.raises(ic.Refused) as e:
        ic.search(pid, "brisbane", "s1")
    assert e.value.code == 423 and live.calls == [] and ledger.ic_used("personal")["requests"] == 0
    monkeypatch.setitem(ledger.IC_POOLS, "personal", {"requests": 0, "credits": 4})
    assert not any(x["ok"] for x in ic.view(pid, "brisbane", "s1")["costs"].values())


def test_the_same_request_within_30_days_is_reused_at_no_request(live, monkeypatch):
    """Owner, 27 Oct 2026 ("we can cache stuff as needed"): an identical discovery or enrichment within 30 days is answered
    from the saved response, labelled CACHED, with 0 requests and 0 credits; after 30 days it is asked again."""
    pid, other = project(), project()
    first = db.get_record(ic.search(pid, "brisbane", "s1")["search_id"])
    used = ledger.ic_used("personal")["requests"]
    again = db.get_record(ic.search(other, "brisbane", "s1")["search_id"])  # another project, the same request
    assert first["origin"] == "LIVE" and again["origin"] == "CACHED" and again["credits"]["charged"] == 0.0
    assert ledger.ic_used("personal")["requests"] == used and len(again["accounts"]) == len(first["accounts"])
    db.x("UPDATE provider_cache SET fetched_at='2026-01-01T00:00:00+00:00' WHERE key LIKE 'ic:%'")
    assert db.get_record(ic.search(other, "brisbane", "s1")["search_id"])["origin"] == "LIVE"
    lid = ic.choose(pid, first["id"], 0)["lead_id"]
    ic.enrich(pid, lid, "s1")
    n = ledger.ic_used("personal")["requests"]
    lid2 = ic.choose(other, again["id"], 0)["lead_id"]
    ic.enrich(other, lid2, "s1")
    assert ledger.ic_used("personal")["requests"] == n  # the same handle's contact, reused


def test_creators_are_listed_biggest_first(live):
    pid = project()
    acc = db.get_record(ic.search(pid, "brisbane", "s1")["search_id"])["accounts"]
    assert [a["followers"] or 0 for a in acc] == sorted([a["followers"] or 0 for a in acc], reverse=True)
