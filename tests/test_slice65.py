"""Slice 6.5: Brand → Markets → Plan → Act (BUILD-PLAN "Slice 6.5"; CONTRACTS B10). SYNTHETIC providers throughout."""
from landfall import db, ledger

from tests.test_markets import idle, markets_env, scanned  # noqa: F401  (fixture re-export)


def test_intake_ceiling_default_and_founder_value(client):
    pid = client.post("/api/projects", json={"url": "http://tidewell.example/"}).json()["project_id"]
    fi = db.latest("founder_input", pid)
    assert (fi["ceiling_usd"], fi["ceiling_by"]) == (10_000, "DEFAULT")
    pid2 = client.post("/api/projects", json={"url": "http://tidewell.example/", "ceiling_usd": 7500}).json()["project_id"]
    intent = client.get(f"/api/projects/{pid2}").json()["intent"]
    assert (intent["ceiling_usd"], intent["ceiling_by"]) == (7500, "YOU_SAID")
    assert client.post("/api/projects", json={"url": "http://tidewell.example/", "ceiling_usd": 0}).status_code == 422


def test_ceiling_edit_writes_founder_input_and_starts_nothing(client, markets_env):
    """AC3: 10,000 → 5,000 is FounderInput v+1, YOU_SAID; no run, no ledger dispatch, no Update notice."""
    pid, m = scanned(client, markets_env)
    fi = db.latest("founder_input", pid)
    runs = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=?", pid)[0]["n"]
    spent = ledger.totals()
    r = client.patch(f"/api/projects/{pid}/intent", json={"base_version": fi["version"], "ceiling_usd": 5000})
    assert r.status_code == 200 and "Update" not in r.json()["notice"]
    now = db.latest("founder_input", pid)
    assert (now["version"], now["ceiling_usd"], now["ceiling_by"]) == (fi["version"] + 1, 5000, "YOU_SAID")
    assert db.q("SELECT COUNT(*) n FROM runs WHERE project_id=?", pid)[0]["n"] == runs and ledger.totals() == spent
    assert not [n for n in client.get(f"/api/projects/{pid}/markets").json()["notices"] if n["action"] == "update"]
    assert client.patch(f"/api/projects/{pid}/intent", json={"base_version": now["version"], "ceiling_usd": None}).status_code == 422


def test_beachhead_is_chosen_kept_through_update_and_guarded(client, markets_env):
    """M2 + AC5e [P65-R7]: choosing a city adds and includes it; an Update whose assessment omits it keeps it."""
    pid, m = scanned(client, markets_env)
    sl = m["shortlist"]
    assert "lisbon" not in {c["city_id"] for c in sl["cities"]}  # not recommended
    r = client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": sl["version"], "beachhead": "lisbon"})
    assert r.json() == {"version": sl["version"] + 1}
    m = client.get(f"/api/projects/{pid}/markets").json()
    lis = next(c for c in m["shortlist"]["cities"] if c["city_id"] == "lisbon")
    assert lis["included"] and lis["added_by_founder"]
    bh = m["beachhead"]
    assert bh["city_id"] == "lisbon" and bh["chosen_under"]["assessment_set"]["version"] == m["assessment"]["version"]
    assert "No longer recommended · your beachhead" in bh["notes"]
    assert client.get(f"/api/projects/{pid}").json()["beachhead"]["city_id"] == "lisbon"
    # the beachhead can't be unticked, and an unknown city is refused
    v = m["shortlist"]["version"]
    assert client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": v, "exclude": ["lisbon"]}).status_code == 409
    assert client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": v, "beachhead": "atlantis"}).status_code == 422

    # a goal change, then Update: a new AssessmentSet that doesn't recommend Lisbon
    fi = db.latest("founder_input", pid)
    client.patch(f"/api/projects/{pid}/intent", json={"base_version": fi["version"], "purpose": "test_demand"})
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m2 = client.get(f"/api/projects/{pid}/markets").json()
    assert m2["assessment"]["version"] > m["assessment"]["version"]
    assert "lisbon" not in {c["city_id"] for c in m2["assessment"]["cards"]}
    lis = next(c for c in m2["shortlist"]["cities"] if c["city_id"] == "lisbon")
    assert lis["included"]
    assert m2["beachhead"]["chosen_under"] == bh["chosen_under"]
    assert "Chosen under goal v" in " ".join(m2["beachhead"]["notes"])

    # a recommended beachhead that a later assessment drops is kept, included, and marked
    db.put_record("shortlist", pid, {**{k: v for k, v in db.latest("shortlist", pid).items()
                                         if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")},
                                     "cities": [c for c in db.latest("shortlist", pid)["cities"] if c["city_id"] != "lisbon"]},
                  rid=f"sl_{pid}")
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    lis = next(c for c in client.get(f"/api/projects/{pid}/markets").json()["shortlist"]["cities"] if c["city_id"] == "lisbon")
    assert lis["included"] and lis["kept_as_beachhead"]

    # clearing it
    v = db.latest("shortlist", pid)["version"]
    assert client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": v, "beachhead": None}).status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["beachhead"] is None


def test_the_built_bundle_carries_no_v4_design_fixture_names():
    """AC10: the v4 prototype's scenario data (landfall-v4-fixtures.js) never reaches the product. The labelled Tidewell demo
    button is the app's own SYNTHETIC demo, not the design scenario, so the scenario's own names are checked."""
    import pytest

    from landfall import config
    dist = config.ROOT / "web" / "dist" / "assets"
    if not dist.exists():
        pytest.skip("frontend not built (make web)")
    bundle = "".join(p.read_text(errors="ignore") for p in dist.glob("*.js"))
    for name in ("Kettle & Crane Grocers", "Dundas West Fest", "Leaf Hall", "Lower Ossington Theatre", "Maya Lefebvre",
                 "Ossington Hall", "Ossington Run Collective", "Parkdale Provisions", "Small Batch Toronto",
                 "Trinity Bellwoods Run Crew", "@runeatsto", "landfall-v4-fixtures"):
        assert name not in bundle, name
