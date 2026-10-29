"""v4 B13 · a partly created campaign (UI-V4-ALIGNMENT-PLAN P1-4), offline with the SYNTHETIC Provider of test_slice6.
A definitive rejection after some objects were created leaves those objects (paused) in the account; the next approved
attempt must dispatch only the missing steps, reusing the created ids, never a second campaign or ad set."""
from test_markets import idle, markets_env  # noqa: F401
from test_slice4 import env, research_ready  # noqa: F401
from test_slice6 import ads_ready, approve_and_run, deliver_env, dest, provider, view  # noqa: F401
from test_slice45_runner import spec_env  # noqa: F401

from landfall import db

REJECT = (400, {"error": {"message": "Invalid parameter", "code": 100, "error_user_msg": "Image too small"}})


def test_partly_created_reads_partly_created_and_a_retry_creates_only_the_missing_steps(client, deliver_env, monkeypatch, provider):
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    provider.reject["creative"] = REJECT
    first = db.get_record(approve_and_run(client, pid, v, "META_ADS").json()["id"])
    assert first["status"] == "FAILED" and first["partial"] == ["image", "campaign", "adset"]
    assert provider.writes == ["image", "campaign", "adset"]

    d = dest(view(client, pid, v["id"]), "META_ADS")
    provider.reject.clear()
    second = approve_and_run(client, pid, view(client, pid, v["id"]), "META_ADS").json()
    a = db.get_record(second["id"])
    # the retry dispatched the missing steps only, with the created ids: one campaign, one ad set, ever
    assert provider.writes == ["image", "campaign", "adset", "creative", "ad"], provider.writes

    assert d["summary"] == "PARTLY_CREATED", d["summary"]
    assert d["text"].startswith("Partly created · Campaign ✓ · ad set ✓") and "Everything created is paused." in d["text"]
    assert d["partly"]["created"] == ["image", "campaign", "adset"] and d["partly"]["missing"] == ["creative", "ad"]
    assert d["next"] == ["approve"]
    assert [s["step"] for s in a["steps"]] == ["creative", "ad"]
    assert a["ids"]["campaign"] == first["ids"]["campaign"] and a["ids"]["adset"] == first["ids"]["adset"]
    assert a["reused"] == {k: first["ids"][k] for k in ("image", "campaign", "adset")}
    ad_req = next(r for s, r in provider.calls if s == "ad")
    assert ad_req["form"]["adset_id"] == first["ids"]["adset"]
    assert a["status"] == "SUCCEEDED" and a["verification"]["verified"]  # read back covers every object, reused or new
    assert dest(view(client, pid, v["id"]), "META_ADS")["summary"] == "CREATED_PAUSED"


def test_a_partial_from_another_account_is_not_reused(client, deliver_env, monkeypatch, provider):
    from test_slice6 import connect
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    provider.reject["creative"] = REJECT
    approve_and_run(client, pid, v, "META_ADS")
    connect(pid, "META_ADS", {"id": "act_999", "name": "Other ads", "currency": "CAD", "page_id": "pg1", "page_name": "Tidewell"})
    monkeypatch.setitem(__import__("landfall").config._ENV, "LANDFALL_DELIVERY_ALLOWLIST", "*")
    provider.reject.clear()
    a = db.get_record(approve_and_run(client, pid, view(client, pid, v["id"]), "META_ADS").json()["id"])
    assert [s["step"] for s in a["steps"]] == ["image", "campaign", "adset", "creative", "ad"] and not a.get("reused")
