"""Slice 6.5 increment D: launch-kit mockups (BUILD-PLAN UI.13, AC13; CONTRACTS B10 ConceptPreview). A SYNTHETIC image
provider stands in for the model; plans, drafts and product images are written directly."""
import base64
import time

import pytest

from conftest import pause
from landfall import db, delivery, ledger, mockups

JPEG = base64.b64encode(b"\xff\xd8SYNTHETIC-scene\xff\xd9").decode()


@pytest.fixture
def images(monkeypatch):
    calls = []

    def fake(cassette, prompt, seed):
        calls.append(prompt)
        if "pop-up" in prompt and fake.fail:
            raise fake.fail
        return {"b64": JPEG, "media_type": "image/jpeg"}
    fake.fail = None
    monkeypatch.setattr(mockups, "render_scene", fake)
    fake.calls = calls
    return fake


def setup(roles=(("community", "MAIN"), ("popup", "SUPPORTS"), ("creator", "OPTIONAL")), signed=True):
    pid = db.new_id("prj")
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s1", "https://gotbag.example/", "live", None, None, db.now())
    db.put_record("brief", pid, {"brand_name": "GOT BAG", "facts": []}, rid=f"brief_{pid}")
    prod = db.put_record("product", pid, {"title": "Rolltop Backpack", "product_url": "https://gotbag.example/rolltop",
                                          "category_terms": ["backpacks"]}, rid=f"pr_{pid}_1")
    delivery.save_artifact(pid, b"\xff\xd8REAL-PRODUCT\xff\xd9", "rolltop.jpg", "image/jpeg",
                           {"kind": "FETCHED", "url": "https://gotbag.example/img.jpg", "page": prod["product_url"]},
                           "brand_own_unconfirmed", [])
    methods = []
    for m, role in roles:
        dr = db.put_record("experiment_draft", pid, {"concept": {"name": f"{m.title()} test", "line": f"A {m} idea"},
                                                     "products": [{"id": prod["id"], "version": prod["version"]}]},
                           rid=f"drf_{pid}_brisbane_{m}")
        methods.append({"method": m, "role": role, "role_by": "founder", "dossier": {"id": f"dos_{m}", "version": 1},
                        "draft": {"id": dr["id"], "version": dr["version"]}})
    plan = db.put_record("plan", pid, {"city_id": "brisbane", "methods": methods, "source": "founder_selection", "status": "active",
                                       "signed_off": {"at": db.now(), "by": "founder"} if signed else None}, rid=f"plan_{pid}_brisbane")
    return pid, plan, prod


def finish(pid, timeout=20):
    end = time.monotonic() + timeout
    while db.q("SELECT 1 FROM runs WHERE project_id=? AND kind='mockup' AND status IN ('queued','running')", pid):
        assert time.monotonic() < end, "mockup run did not finish"
        pause(0.05)


def test_one_mockup_per_included_method_saved_and_never_regenerated(images):
    pid, plan, prod = setup()
    assert mockups.start(pid, "brisbane", dry_run=True)["est"] == {"mockups": 2, "new_scenes": 2,
                                                                   "est_usd": round(2 * mockups.EXPOSURE_USD, 2)}
    mockups.start(pid, "brisbane")
    finish(pid)
    v = {r["method"]: r for r in mockups.view(pid, "brisbane")}
    assert set(v) == {"community", "popup"}  # MAIN and SUPPORTS only; OPTIONAL is not in the kit
    c = v["community"]
    assert c["state"] == "READY" and c["labels"] == ["ILLUSTRATIVE_CONCEPT"] and c["alt_text"].startswith("Illustrative concept: GOT BAG")
    assert c["overlay"] == {"brand": "GOT BAG", "city": "Brisbane", "concept": {"name": "Community test", "line": "A community idea"}}
    assert c["products"][0]["title"] == "Rolltop Backpack" and c["products"][0]["artifact"]["sha256"]
    assert delivery.artifact_bytes(c["image"]["artifact"]) == base64.b64decode(JPEG)
    assert all("No text, no logos, no product packaging." in p for p in images.calls) and len(images.calls) == 2
    # reading and asking again never regenerate
    for _ in range(3):
        mockups.view(pid, "brisbane")
    assert mockups.start(pid, "brisbane") == {"run_id": None, "est": {"mockups": 0, "new_scenes": 0, "est_usd": 0.0}}
    assert len(images.calls) == 2


def test_a_failed_mockup_fails_alone_and_retries_once(images):
    pid, plan, _ = setup()
    images.fail = RuntimeError("SYNTHETIC image failure")
    mockups.start(pid, "brisbane")
    finish(pid)
    v = {r["method"]: r for r in mockups.view(pid, "brisbane")}
    assert v["community"]["state"] == "READY" and v["popup"]["state"] == "FAILED" and v["popup"]["error"]["retryable"]
    assert db.q("SELECT status FROM runs WHERE project_id=? AND kind='mockup'", pid)[0]["status"] == "partial"
    images.fail = None
    n = len(images.calls)
    assert mockups.start(pid, "brisbane", dry_run=True)["est"]["mockups"] == 1
    mockups.start(pid, "brisbane", ["popup"])
    finish(pid)
    assert {r["method"]: r["state"] for r in mockups.view(pid, "brisbane")} == {"community": "READY", "popup": "READY"}
    assert len(images.calls) == n + 1


def test_out_of_budget_is_its_own_state(images):
    pid, plan, _ = setup()
    images.fail = ledger.BudgetExhausted("live authority expired")
    mockups.start(pid, "brisbane")
    finish(pid)
    v = {r["method"]: r for r in mockups.view(pid, "brisbane")}
    assert v["popup"]["state"] == "OUT_OF_BUDGET" and v["community"]["state"] == "READY"


def test_changes_mark_an_update_and_an_unchanged_scene_is_reused_free(images):
    pid, plan, prod = setup()
    mockups.start(pid, "brisbane")
    finish(pid)
    db.put_record("product", pid, {"title": "Rolltop Backpack", "product_url": prod["product_url"],
                                   "category_terms": ["backpacks"], "price_text": "€149"}, rid=prod["id"])
    v = {r["method"]: r for r in mockups.view(pid, "brisbane")}
    assert v["community"]["stale"] == ["A product changed"]
    n = len(images.calls)
    est = mockups.start(pid, "brisbane", dry_run=True)["est"]
    assert est["mockups"] == 2 and est["new_scenes"] == 0 and est["est_usd"] == 0  # same prompts: no new scene, no cost
    mockups.start(pid, "brisbane")
    finish(pid)
    assert len(images.calls) == n
    v = {r["method"]: r for r in mockups.view(pid, "brisbane")}
    assert v["community"]["reused_from"] and v["community"]["state"] == "READY"
    # the update composed the current product and settles: no further update is offered [P65-I4]
    assert v["community"]["products"][0]["version"] == 2 and v["community"]["stale"] == []
    assert mockups.start(pid, "brisbane", dry_run=True)["est"]["mockups"] == 0
    # removing a method in an unsigned version moves its mockup under the earlier plan
    db.put_record("plan", pid, {**{k: plan[k] for k in ("city_id", "source", "status")}, "signed_off": None,
                                "methods": [{**m, "role": "NOT_NOW"} if m["method"] == "popup" else m for m in plan["methods"]]},
                  rid=plan["id"])
    v = {r["method"]: r for r in mockups.view(pid, "brisbane")}
    assert v["popup"]["earlier_plan"] and not v["community"]["earlier_plan"]


def test_nothing_before_sign_off(images):
    pid, *_ = setup(signed=False)
    with pytest.raises(mockups.Refused) as e:
        mockups.start(pid, "brisbane")
    assert e.value.code == 409 and images.calls == []


def test_gemini_scene_parses_the_image_counts_its_price_and_keeps_the_key_out(monkeypatch, tmp_path):
    """The Gemini adapter offline: SYNTHETIC responses shaped as the docs' SDK examples (steps → model_output → image)."""
    import httpx

    from landfall import config
    from landfall.cassette import Cassette
    monkeypatch.setitem(config._ENV, "gemini_api_key", "SYNTHETIC-gemini-key")
    monkeypatch.setattr(mockups, "MODEL", "gemini-nano-banana-2.1")
    monkeypatch.setattr(mockups, "EXPOSURE_USD", 0.0336)
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        if post.status != 200:
            return httpx.Response(post.status, json={"error": {"message": "SYNTHETIC refusal"}})
        return httpx.Response(200, json={"id": "syn", "steps": [{"type": "model_output", "content": [{"type": "image", "data": JPEG}]}]})
    post.status = 200
    monkeypatch.setattr(mockups, "_post", post)
    cas = Cassette("record", tmp_path / "cas")
    out = mockups.render_scene(cas, "a pop-up market stand in Brisbane", 1)
    assert out == {"b64": JPEG, "media_type": "image/jpeg"}
    url, body, headers = sent[0]
    assert url.endswith("/v1beta/interactions") and body["store"] is False and body["model"] == "gemini-nano-banana-2.1"
    assert body["response_format"] == {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "16:9", "image_size": "1K"}
    assert headers["x-goog-api-key"] == "SYNTHETIC-gemini-key"
    assert "SYNTHETIC-gemini-key" not in (tmp_path / "cas" / "exchanges.jsonl").read_text()
    assert ledger.totals()["gemini_usd"] == pytest.approx(0.0336)
    monkeypatch.setattr(config, "CAP_GEMINI_USD", 0.05)  # its own US$15 cap (here 0.05): the next image is refused first
    with pytest.raises(ledger.BudgetExhausted):
        mockups.render_scene(Cassette("live"), "a shop display in Brisbane", 3)
    monkeypatch.setattr(config, "CAP_GEMINI_USD", 15.0)
    post.status = 403  # a refusal is never billed, and fails only that mockup
    with pytest.raises(RuntimeError):
        mockups.render_scene(Cassette("live"), "a community meeting place in Brisbane", 2)
    assert ledger.totals()["usd"] == pytest.approx(0.0336)


def test_the_package_product_selection_marks_an_update_and_drops_the_product(images):
    """[P65-I4] The founder takes the only product out of the method's package: the saved mockup needs an update,
    and the update shows no product, reusing the scene at no cost."""
    pid, plan, prod = setup(roles=(("community", "MAIN"),))
    mockups.start(pid, "brisbane")
    finish(pid)
    assert mockups.view(pid, "brisbane")[0]["products"][0]["id"] == prod["id"]
    db.put_record("package", pid, {"city_id": "brisbane", "method": "community", "status": "active",
                                   "products": [], "products_by": "YOU_SAID"}, rid="pkg_selection")
    assert mockups.view(pid, "brisbane")[0]["stale"] == ["The products you're offering changed"]
    n = len(images.calls)
    mockups.start(pid, "brisbane")
    finish(pid)
    after = mockups.view(pid, "brisbane")[0]
    assert after["state"] == "READY" and after["products"] == [] and after["stale"] == [] and len(images.calls) == n


def test_the_kit_reads_saved_photos_and_copy_even_when_the_scene_failed(images):
    """v4 D3a: the page composes the tiles from `kit`, read now: the saved product photo (no fetch), never a gift card,
    and the method's partner; a failed scene doesn't empty it."""
    pid, plan, prod = setup(roles=(("popup", "MAIN"),))
    gift = db.put_record("product", pid, {"title": "E-Gift Card", "product_url": "https://gotbag.example/gift"}, rid=f"pr_{pid}_2")
    dr = db.put_record("experiment_draft", pid, {"concept": {"name": "Popup test", "line": "A popup idea"},
                                                 "products": [{"id": prod["id"], "version": 1}, {"id": gift["id"], "version": 1}]},
                       rid=f"drf_{pid}_brisbane_popup")
    db.put_record("plan", pid, {**{k: plan[k] for k in ("city_id", "source", "status", "signed_off")},
                                "methods": [{**plan["methods"][0], "draft": {"id": dr["id"], "version": dr["version"]}}]}, rid=plan["id"])
    images.fail = RuntimeError("SYNTHETIC image failure")
    mockups.start(pid, "brisbane")
    finish(pid)
    v = mockups.view(pid, "brisbane")[0]
    assert v["state"] == "FAILED" and v["products"] == []
    assert [p["title"] for p in v["kit"]["products"]] == ["Rolltop Backpack"] and v["kit"]["products"][0]["artifact"]["sha256"]
    assert v["kit"]["copy"] is None and v["kit"]["partner"] is None
