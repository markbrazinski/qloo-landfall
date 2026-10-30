"""A1 access tracks (DEPLOYMENT-PLAN §5; owner, 27 Oct 2026: judges get live research, everyone else the recorded demo).
Offline only: live project creation is stubbed before any run starts."""
import hashlib

import pytest

from landfall import app as app_module, config, db, grants, influencers


@pytest.fixture
def env(monkeypatch):
    for k in ("LANDFALL_REQUIRE_GRANT", "LANDFALL_PUBLIC_URL", "LANDFALL_ENV"):
        monkeypatch.delitem(config._ENV, k, raising=False)
    return lambda k, v: monkeypatch.setitem(config._ENV, k, v)


@pytest.fixture
def no_live_runs(monkeypatch):
    """A live project is created but never reads anything: no fetch, no provider."""
    monkeypatch.setattr(app_module, "normalize_url", lambda raw: "https://brand.example/")
    monkeypatch.setattr(app_module.runner, "start", lambda *a, **k: "run_stub")


def _open(client, raw, param="judge_id", path="/?"):
    return client.get(f"{path}{param}={raw}", follow_redirects=False)


def test_only_the_hash_is_stored(env):
    raw = grants.create("judge", "judges")
    assert len(raw) == 22  # 128 bits, URL-safe base64
    row = dict(db.q("SELECT * FROM grants")[0])
    assert row["hash"] == hashlib.sha256(raw.encode()).hexdigest() and raw not in str(row)
    assert [g["label"] for g in grants.list()] == ["judges"]
    with pytest.raises(ValueError):
        grants.create("admin")


def test_link_gives_a_fresh_session_and_a_clean_url(client, env):
    raw = grants.create("judge")
    client.cookies.set("lf_session", "planted-by-someone-else")  # fixation: a pre-set id never gains the grant
    r = _open(client, raw, path="/p/x?tab=1&")  # other parameters survive; only the id is dropped
    assert r.status_code == 303 and r.headers["location"] == "/p/x?tab=1" and raw not in r.headers["location"]
    assert r.headers["referrer-policy"] == "no-referrer"
    sid = r.cookies["lf_session"]
    assert sid != "planted-by-someone-else" and grants.kind_of("planted-by-someone-else") is None
    assert grants.kind_of(sid) == "judge"
    client.cookies.set("lf_session", sid)
    assert client.get("/api/session").json().items() >= {"track": "live", "grant_kind": "judge"}.items()


def test_unknown_and_revoked_ids_redirect_without_a_grant(client, env):
    raw = grants.create("owner", "owner")
    assert grants.revoke("owner") == 1
    for bad in (raw, "not-a-real-id"):
        r = _open(client, bad, param="owner_id")
        assert r.status_code == 303 and r.headers["location"] == "/" and "set-cookie" not in r.headers
    assert db.q("SELECT COUNT(*) n FROM grant_sessions")[0]["n"] == 0


def test_revocation_applies_on_the_next_request(client, env):
    env("LANDFALL_REQUIRE_GRANT", "on")
    raw = grants.create("judge", "judges")
    client.cookies.set("lf_session", _open(client, raw).cookies["lf_session"])
    assert client.get("/api/session").json()["track"] == "live"
    grants.revoke(hashlib.sha256(raw.encode()).hexdigest())  # by hash as well as by label
    assert client.get("/api/session").json().items() >= {"track": "demo", "grant_kind": None}.items()


def test_access_form_accepts_a_pasted_id_or_the_whole_link(client, env):
    raw = grants.create("judge")
    assert "<form" in client.get("/access").text
    for pasted in (raw, f"https://x.cloudfront.net/?judge_id={raw}"):
        r = client.post("/access", data={"id": pasted}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/" and grants.kind_of(r.cookies["lf_session"]) == "judge"


def test_https_uses_the_host_prefixed_secure_cookie(client, env):
    env("LANDFALL_PUBLIC_URL", "https://x.cloudfront.net")
    raw = grants.create("judge")
    cookie = _open(client, raw).headers["set-cookie"]
    assert cookie.startswith("__Host-lf_session=") and "Secure" in cookie and "Path=/" in cookie and "Domain" not in cookie


def test_without_a_grant_every_project_is_the_recorded_demo(client, env, no_live_runs):
    env("LANDFALL_REQUIRE_GRANT", "on")
    assert client.get("/api/session").json().items() >= {"track": "demo", "grant_kind": None}.items()
    r = client.post("/api/projects", json={"url": "https://brand.example"}).json()
    assert r["track"] == "demo"
    p = db.q("SELECT * FROM projects WHERE id=?", r["project_id"])[0]
    assert p["mode"] == "replay" and p["url"] == "http://tidewell.example/" and not grants.live_project(r["project_id"])


def test_a_grant_or_no_requirement_is_live(client, env, no_live_runs):
    r = client.post("/api/projects", json={"url": "brand.example"}).json()  # REQUIRE_GRANT unset: local development
    assert r["track"] == "live" and grants.live_project(r["project_id"])
    env("LANDFALL_REQUIRE_GRANT", "on")
    assert not grants.live_project(r["project_id"])  # its session holds no grant
    client.cookies.set("lf_session", _open(client, grants.create("owner")).cookies["lf_session"])
    r = client.post("/api/projects", json={"url": "brand.example"}).json()
    assert r["track"] == "live" and grants.live_project(r["project_id"])
    assert db.q("SELECT mode FROM projects WHERE id=?", r["project_id"])[0]["mode"] == "live"


def test_only_judge_sessions_draw_on_the_judges_pool(env):
    env("LANDFALL_ENV", "judging")
    judge, owner = grants.bind(grants.create("judge", "judges")), grants.bind(grants.create("owner", "owner"))
    assert (influencers.pool_of(judge), influencers.pool_of(owner), influencers.pool_of("anon")) == ("judges", "personal", "personal")
    grants.revoke("judges")
    assert influencers.pool_of(judge) == "personal"


def test_a_revoked_link_stops_new_live_runs_in_existing_projects(monkeypatch):
    from landfall import runner
    monkeypatch.setitem(config._ENV, "LANDFALL_REQUIRE_GRANT", "on")
    pid = db.new_id("prj")
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "sess-revoked-test", "https://x.example/", "live", None, None, db.now())
    with pytest.raises(runner.NotAllowed):  # no grant bound to that session
        runner.start(pid, "brand_read", "attempt-1", {"url": "https://x.example/"})
